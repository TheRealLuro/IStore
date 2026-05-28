// Vault panel (VLT-5) — the zero-knowledge passwords + secure-notes UI.
//
// Three gates, in order:
//   1. No vault yet      → VaultSetup  (create a master password)
//   2. Vault exists, locked → VaultUnlock (enter the master password)
//   3. Unlocked          → VaultHome  (list / add / view / delete items)
//
// The master password never leaves the browser. The vault key is derived
// client-side (PBKDF2 → AES-GCM, see @/vault/crypto), held only in memory
// (@/vault/session), and auto-locks on idle. Everything sent to the server
// is already ciphertext; the server can't read titles, passwords, or notes.

import React, { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";

import { Icon } from "./icons.jsx";
import { Modal, ModalClose } from "./primitives.jsx";

import {
  getVaultMeta,
  setupVault,
  setAccountKey,
  listVaultItems,
  createVaultItem,
  updateVaultItem,
  deleteVaultItem,
  wipeVault,
  listVaultFolders,
  createVaultFolder,
  deleteVaultFolder,
  uploadVaultFile,
  downloadVaultFile,
  getRecipientKey,
  shareVaultItem,
  listItemShares,
  listIncomingShares,
  deleteShare,
  downloadSharedFile,
  createPublicLink,
  getPublicLink,
  deletePublicLink,
} from "@/api/vault";
import {
  createVaultSetup,
  deriveVaultKey,
  checkVerifier,
  encryptItem,
  decryptItem,
  b64ToBytes,
  bytesToB64,
  randomBytes,
  createAccountKeyPair,
  unwrapAccountPrivateKey,
  randomFileKey,
  encryptFile,
  decryptFile,
  sealToPublicKey,
  unsealFromPrivateKey,
  randomLinkSecret,
  bytesToB64Url,
  derivePublicLinkKey,
  sealPublicLink,
  PUBLINK_KDF_ITERATIONS,
  PUBLINK_SALT_BYTES,
} from "@/vault/crypto";
import {
  unlockVault,
  setAccountKeys,
  lockVault,
  getVaultKey,
  getAccountPublicKey,
  getAccountPrivateKey,
  touchVault,
  useVaultUnlocked,
} from "@/vault/session";

// VLT-8 — bring the account keypair into the live session after the master
// key is available. If the vault has a keypair, unwrap its private key; if it
// predates VLT-8 (no keypair yet), generate one and persist it. Best-effort:
// a provisioning failure must never block access to passwords/notes, so any
// error here is swallowed (sharing/upload simply stays unavailable until the
// next unlock). The unwrapped private key never leaves memory.
async function loadAccountSession(masterKey, meta) {
  try {
    let publicKey = meta?.account_public_key || null;
    let encPrivate = meta?.enc_account_private_key || null;
    if (!publicKey || !encPrivate) {
      // Legacy vault — provision a keypair now.
      const kp = await createAccountKeyPair(masterKey);
      const updated = await setAccountKey({
        account_public_key: kp.publicKey,
        enc_account_private_key: kp.enc_private_key,
      });
      publicKey = updated.account_public_key;
      encPrivate = updated.enc_account_private_key;
    }
    const priv = await unwrapAccountPrivateKey(encPrivate, masterKey);
    setAccountKeys(priv, publicKey);
  } catch {
    // Sharing/upload features will be unavailable this session; core
    // password/note access is unaffected.
  }
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

// Rough, honest strength hint — length-dominated (the only thing that
// actually matters for a KDF'd master password) plus a small bonus for
// character variety. Not a security control, just UX guidance.
function passwordStrength(pw) {
  if (!pw) return { score: 0, label: "Empty", tone: "danger" };
  let bits = pw.length * 4;
  if (/[a-z]/.test(pw)) bits += 4;
  if (/[A-Z]/.test(pw)) bits += 4;
  if (/[0-9]/.test(pw)) bits += 4;
  if (/[^a-zA-Z0-9]/.test(pw)) bits += 6;
  const score = Math.min(100, Math.round(bits));
  if (pw.length < 8) return { score, label: "Too short", tone: "danger" };
  if (score < 50) return { score, label: "Weak", tone: "danger" };
  if (score < 75) return { score, label: "Okay", tone: "warning" };
  return { score, label: "Strong", tone: "success" };
}

function generatePassword(len = 20) {
  // Exclude visually ambiguous chars (O/0, l/1/I) for hand-typed cases.
  const alphabet =
    "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*-_=+";
  const bytes = new Uint8Array(len);
  crypto.getRandomValues(bytes);
  let out = "";
  for (let i = 0; i < len; i++) out += alphabet[bytes[i] % alphabet.length];
  return out;
}

async function copyToClipboard(text, label = "Copied") {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`${label} — clears in 20s`);
    // Best-effort clipboard hygiene: overwrite after 20s if still focused.
    setTimeout(() => {
      navigator.clipboard
        .writeText("")
        .catch(() => {});
    }, 20_000);
  } catch {
    toast.error("Clipboard unavailable");
  }
}

function relTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatBytes(n) {
  if (n == null || isNaN(n)) return "";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

// Secure-item kinds — icon, label, and add-form placeholder. Drives the row
// icons, the add picker, and the detail header so all four kinds look
// first-class. (Files use fileIconName instead.)
const KIND_META = {
  password: { icon: "key", label: "Password", placeholder: "e.g. GitHub" },
  note: { icon: "document", label: "Note", placeholder: "e.g. Recovery codes" },
  seed: { icon: "shield", label: "Seed phrase", placeholder: "e.g. Ledger backup" },
  card: { icon: "contact", label: "Card / ID", placeholder: "e.g. Visa ending 4242" },
};

// Map a MIME type / filename to one of our existing Icon names.
function fileIconName(name = "", mime = "") {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "music";
  if (mime === "application/pdf" || ext === "pdf") return "document";
  if (["zip", "rar", "7z", "tar", "gz"].includes(ext)) return "archive";
  return "file";
}

// Fetch a vault file's ciphertext, unseal its per-file key with the account
// private key, and decrypt it (chunk-by-chunk) into a plaintext Blob. The
// bytes only ever exist as plaintext in memory on this device. Throws on any
// failure. `view` is a decrypted row: { id, wrapped_key, data: { mime, file } }.
async function decryptFileToBlob(view) {
  const priv = getAccountPrivateKey();
  if (!priv) throw new Error("locked");
  if (!view.wrapped_key || !view.data?.file) throw new Error("no-key");
  const fileKey = await unsealFromPrivateKey(priv, view.wrapped_key);
  const blob = await downloadVaultFile(view.id);
  const ct = new Uint8Array(await blob.arrayBuffer());
  const plain = await decryptFile(ct, fileKey, view.data.file);
  return new Blob([plain], {
    type: view.data.mime || "application/octet-stream",
  });
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || "file";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

// Decrypt + save a file to disk (used as a fallback / explicit download).
async function downloadDecryptedFile(view) {
  if (!getAccountPrivateKey()) {
    toast.error("Lock and unlock the vault, then try again.");
    return;
  }
  const t = toast.loading(`Decrypting ${view.data?.title || "file"}…`);
  try {
    const out = await decryptFileToBlob(view);
    saveBlob(out, view.data?.title || "file");
    toast.success("Downloaded", { id: t });
  } catch {
    toast.error("Couldn’t decrypt this file.", { id: t });
  }
}

// What kind of inline preview a MIME type gets. HTML/SVG are deliberately
// NOT rendered as markup — a decrypted file is attacker-controlled and a
// blob: URL is same-origin, so rendering it could run script in our origin.
// Those fall through to "download only".
function previewKind(mime = "") {
  const m = mime.toLowerCase();
  if (m.startsWith("image/") && m !== "image/svg+xml") return "image";
  if (m.startsWith("video/")) return "video";
  if (m.startsWith("audio/")) return "audio";
  if (m === "application/pdf") return "pdf";
  if (m.startsWith("text/") && m !== "text/html") return "text";
  if (m === "application/json") return "text";
  return "none";
}

// Recipient-side: decrypt a SHARED file. The per-file key + metadata come from
// the unsealed bundle (not from the owner's wrapped_key), and the ciphertext
// streams from the share endpoint. Plaintext only ever lives in memory here.
async function decryptSharedFileToBlob(grantId, bundle) {
  const blob = await downloadSharedFile(grantId);
  const ct = new Uint8Array(await blob.arrayBuffer());
  const plain = await decryptFile(ct, b64ToBytes(bundle.key), bundle.data.file);
  return new Blob([plain], {
    type: bundle.data?.mime || "application/octet-stream",
  });
}

// Build the bundle a recipient/visitor unseals: files carry the per-file key
// (unsealed from the owner's wrapped_key) + decrypt metadata; secure items
// carry their plaintext fields. Used by both direct shares and public links.
async function buildItemBundle(item) {
  if (item.has_file || item.kind === "file") {
    const priv = getAccountPrivateKey();
    if (!priv) throw new Error("locked");
    const fileKey = await unsealFromPrivateKey(priv, item.wrapped_key);
    return {
      v: 1,
      kind: "file",
      key: bytesToB64(fileKey),
      data: {
        title: item.data?.title,
        mime: item.data?.mime,
        file: item.data?.file,
      },
    };
  }
  return { v: 1, kind: item.kind, data: item.data || {} };
}

// ---------------------------------------------------------------------------
// VaultPanel — top-level orchestrator
// ---------------------------------------------------------------------------

export function VaultPanel({ theme, setTheme }) {
  const qc = useQueryClient();
  const unlocked = useVaultUnlocked();

  const metaQ = useQuery({
    queryKey: ["vault", "meta"],
    queryFn: getVaultMeta,
    staleTime: 5 * 60_000,
  });

  // Lock the vault when this panel unmounts (user navigates away) — the key
  // shouldn't outlive the visible vault. (Idle auto-lock is independent.)
  useEffect(() => {
    return () => lockVault();
  }, []);

  const header = (
    <div className="vault-topbar">
      <div className="vault-topbar__title">
        <Icon name="lock" size={18} />
        <h1>Vault</h1>
        <span className="vault-topbar__sub">End-to-end encrypted</span>
      </div>
      <div style={{ flex: 1 }} />
      {setTheme && (
        <button
          className="btn-icon"
          onClick={() => setTheme(theme === "light" ? "dark" : "light")}
          aria-label={`Switch to ${theme === "light" ? "dark" : "light"} mode`}
          title={`Switch to ${theme === "light" ? "dark" : "light"} mode`}
        >
          <Icon name={theme === "light" ? "moon" : "sun"} size={14} />
        </button>
      )}
      {unlocked && (
        <button className="btn btn--secondary" onClick={() => lockVault()}>
          <Icon name="lock" size={13} /> Lock
        </button>
      )}
    </div>
  );

  let body;
  if (metaQ.isLoading) {
    body = <VaultCenter>Loading vault…</VaultCenter>;
  } else if (metaQ.isError) {
    body = (
      <VaultCenter>
        <div className="empty__title">Couldn’t load the vault</div>
        <div className="empty__body">
          {metaQ.error?.detail || "Please try again."}
        </div>
        <button
          className="btn btn--secondary"
          style={{ marginTop: 12 }}
          onClick={() => metaQ.refetch()}
        >
          Retry
        </button>
      </VaultCenter>
    );
  } else if (!metaQ.data) {
    body = (
      <VaultSetup
        onDone={() => qc.invalidateQueries({ queryKey: ["vault", "meta"] })}
      />
    );
  } else if (!unlocked) {
    body = <VaultUnlock meta={metaQ.data} />;
  } else {
    body = <VaultHome />;
  }

  return (
    <div className="vault">
      {header}
      {body}
    </div>
  );
}

function VaultCenter({ children }) {
  return (
    <div className="vault-center">
      <div className="empty" style={{ maxWidth: 460 }}>
        {children}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// VaultSetup — first run: create the master password
// ---------------------------------------------------------------------------

function VaultSetup({ onDone }) {
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [ack, setAck] = useState(false);
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const strength = passwordStrength(pw);

  const canSubmit =
    pw.length >= 8 && pw === confirm && ack && !busy;

  const submit = async (e) => {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    try {
      // Derive key + verifier in the browser, POST only the public meta.
      const { key, payload } = await createVaultSetup(pw);
      // Generate the account keypair (for sharing/file-key sealing) under the
      // master key and persist it alongside the meta at setup time.
      const kp = await createAccountKeyPair(key);
      await setupVault({
        ...payload,
        account_public_key: kp.publicKey,
        enc_account_private_key: kp.enc_private_key,
      });
      unlockVault(key); // session is live immediately after setup
      await loadAccountSession(key, {
        account_public_key: kp.publicKey,
        enc_account_private_key: kp.enc_private_key,
      });
      setPw("");
      setConfirm("");
      toast.success("Vault created");
      onDone?.();
    } catch (err) {
      toast.error(err?.detail || "Couldn’t create the vault");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="vault-center">
      <form className="vault-card" onSubmit={submit}>
        <div className="vault-card__hero">
          <div className="vault-card__shield">
            <Icon name="shield" size={26} />
          </div>
          <h2>Create your vault</h2>
          <p>
            Your master password encrypts everything on your device before it
            ever leaves it. We never see it — so we can’t reset it, and there
            is no “forgot password”.
          </p>
        </div>

        <div className="vault-warn">
          <Icon name="key" size={16} />
          <div>
            <strong>This is the only key — write it down.</strong> If you
            forget your master password, your vault and everything inside it
            is <strong>permanently deleted and unrecoverable</strong>, even by
            neuthek. Choose something strong but memorable, and save it
            somewhere safe (a password manager or written down).
          </div>
        </div>

        <label className="vault-field">
          <span>Master password</span>
          <div className="vault-field__input">
            <input
              className="input"
              type={show ? "text" : "password"}
              value={pw}
              autoFocus
              autoComplete="new-password"
              onChange={(e) => setPw(e.target.value)}
              placeholder="At least 8 characters"
            />
            <button
              type="button"
              className="btn-icon"
              onClick={() => setShow((v) => !v)}
              aria-label={show ? "Hide" : "Show"}
            >
              <Icon name={show ? "eyeOff" : "eye"} size={14} />
            </button>
          </div>
          {pw && (
            <div className="vault-strength" data-tone={strength.tone}>
              <div className="vault-strength__bar">
                <div style={{ width: strength.score + "%" }} />
              </div>
              <span>{strength.label}</span>
            </div>
          )}
        </label>

        <label className="vault-field">
          <span>Confirm master password</span>
          <input
            className="input"
            type={show ? "text" : "password"}
            value={confirm}
            autoComplete="new-password"
            onChange={(e) => setConfirm(e.target.value)}
            placeholder="Re-enter it"
          />
          {confirm && confirm !== pw && (
            <span className="vault-field__err">Passwords don’t match</span>
          )}
        </label>

        <button
          type="button"
          className="vault-ack"
          data-checked={ack}
          onClick={() => setAck((v) => !v)}
        >
          <span className="vault-ack__box">
            <Icon name="check" size={11} strokeWidth={2.6} />
          </span>
          <span>
            I’ve saved my master password somewhere safe and understand that
            if I lose it, <strong>everything in my vault is permanently
            deleted</strong> — not even neuthek can recover it.
          </span>
        </button>

        <button className="btn btn--primary btn--lg" disabled={!canSubmit}>
          {busy ? "Creating…" : "Create vault"}
        </button>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// VaultUnlock — returning: enter the master password
// ---------------------------------------------------------------------------

function VaultUnlock({ meta }) {
  const qc = useQueryClient();
  const [pw, setPw] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [resetOpen, setResetOpen] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (!pw || busy) return;
    setBusy(true);
    setError("");
    try {
      const salt = b64ToBytes(meta.kdf_salt);
      const key = await deriveVaultKey(pw, salt, meta.kdf_iterations);
      const good = await checkVerifier(key, {
        verifier_nonce: meta.verifier_nonce,
        verifier_ct: meta.verifier_ct,
      });
      if (!good) {
        setError("Incorrect master password");
        setPw("");
        return;
      }
      unlockVault(key);
      // Bring the account keypair into the session (unwrap, or provision for
      // a legacy vault). Best-effort — never blocks unlock.
      await loadAccountSession(key, meta);
      setPw("");
    } catch (err) {
      setError(err?.message || "Couldn’t unlock");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="vault-center">
      <form className="vault-card" onSubmit={submit}>
        <div className="vault-card__hero">
          <div className="vault-card__shield">
            <Icon name="lock" size={24} />
          </div>
          <h2>Unlock your vault</h2>
          <p>Enter your master password to decrypt your vault on this device.</p>
        </div>

        <label className="vault-field">
          <span>Master password</span>
          <div className="vault-field__input">
            <input
              className="input"
              type={show ? "text" : "password"}
              value={pw}
              autoFocus
              autoComplete="current-password"
              onChange={(e) => {
                setPw(e.target.value);
                if (error) setError("");
              }}
              placeholder="Master password"
            />
            <button
              type="button"
              className="btn-icon"
              onClick={() => setShow((v) => !v)}
              aria-label={show ? "Hide" : "Show"}
            >
              <Icon name={show ? "eyeOff" : "eye"} size={14} />
            </button>
          </div>
          {error && <span className="vault-field__err">{error}</span>}
        </label>

        <button className="btn btn--primary btn--lg" disabled={!pw || busy}>
          {busy ? "Unlocking…" : "Unlock"}
        </button>

        <button
          type="button"
          className="vault-link"
          onClick={() => setResetOpen(true)}
        >
          Forgot your master password?
        </button>
      </form>

      <ResetVaultModal
        open={resetOpen}
        onClose={() => setResetOpen(false)}
        onWiped={() => {
          setResetOpen(false);
          lockVault();
          qc.invalidateQueries({ queryKey: ["vault", "meta"] });
          qc.invalidateQueries({ queryKey: ["vault", "items"] });
        }}
      />
    </div>
  );
}

function ResetVaultModal({ open, onClose, onWiped }) {
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const wipe = async () => {
    setBusy(true);
    try {
      await wipeVault();
      toast.success("Vault reset");
      onWiped?.();
    } catch (e) {
      toast.error(e?.detail || "Couldn’t reset the vault");
    } finally {
      setBusy(false);
      setConfirm("");
    }
  };
  return (
    <Modal open={open} onClose={onClose} size="md" labelledBy="vault-reset-title">
      <ModalClose onClose={onClose} />
      <div style={{ padding: 22, display: "flex", flexDirection: "column", gap: 14 }}>
        <h2 id="vault-reset-title" style={{ margin: 0, fontSize: 18 }}>
          Reset your vault?
        </h2>
        <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 14, lineHeight: 1.5 }}>
          Because your vault is encrypted with a key only you hold, a forgotten
          master password can’t be recovered. The only option is to{" "}
          <strong>permanently delete the vault</strong> and start over. Every
          saved password and note will be lost.
        </p>
        <label className="vault-field">
          <span>Type DELETE to confirm</span>
          <input
            className="input"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder="DELETE"
          />
        </label>
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button className="btn btn--secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            className="btn btn--danger"
            disabled={confirm !== "DELETE" || busy}
            onClick={wipe}
          >
            {busy ? "Resetting…" : "Reset vault"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// VaultHome — unlocked: a drive-like, end-to-end-encrypted file store with
// nested folders + secure items (passwords / notes). Files are encrypted on
// this device and uploaded as ciphertext; folder names are encrypted too.
// ---------------------------------------------------------------------------

function VaultHome() {
  const qc = useQueryClient();
  const [tab, setTab] = useState("mine"); // mine | shared
  const [currentFolder, setCurrentFolder] = useState(null); // null = vault root
  const [search, setSearch] = useState("");
  const [items, setItems] = useState(null); // decrypted item views, null = loading
  const [folders, setFolders] = useState(null); // decrypted folder views
  const [decryptErr, setDecryptErr] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [detail, setDetail] = useState(null);
  const [viewer, setViewer] = useState(null); // decrypted file row being previewed
  const [shareItem, setShareItem] = useState(null); // item being shared (owner)
  const [uploading, setUploading] = useState(0); // in-flight upload count
  const fileInputRef = useRef(null);

  // Count of items shared WITH me — drives the tab badge. SharedWithMe reuses
  // the same query key, so the cache is shared.
  const incomingQ = useQuery({
    queryKey: ["vault", "shares"],
    queryFn: listIncomingShares,
    staleTime: 30_000,
  });
  const incomingCount = incomingQ.data?.length || 0;

  const itemsQ = useQuery({
    queryKey: ["vault", "items"],
    queryFn: listVaultItems,
    staleTime: 30_000,
  });
  const foldersQ = useQuery({
    queryKey: ["vault", "folders"],
    queryFn: listVaultFolders,
    staleTime: 30_000,
  });

  // Decrypt item rows (small items decrypt to their fields; file items decrypt
  // to their metadata { title, mime, file }). Async → can't live in `select`.
  useEffect(() => {
    let cancelled = false;
    const rows = itemsQ.data;
    if (!rows) return;
    const key = getVaultKey();
    if (!key) return; // locked mid-flight; gate will swap us out
    (async () => {
      setDecryptErr(false);
      try {
        const out = [];
        for (const row of rows) {
          try {
            const data = await decryptItem(key, {
              nonce: row.nonce,
              ciphertext: row.ciphertext,
            });
            out.push({
              id: row.id,
              kind: row.kind,
              folder_id: row.folder_id ?? null,
              has_file: !!row.has_file,
              wrapped_key: row.wrapped_key ?? null,
              size_bytes: row.size_bytes ?? null,
              created_at: row.created_at,
              updated_at: row.updated_at,
              data,
            });
          } catch {
            out.push({
              id: row.id,
              kind: row.kind,
              folder_id: row.folder_id ?? null,
              has_file: !!row.has_file,
              created_at: row.created_at,
              updated_at: row.updated_at,
              data: null,
              corrupt: true,
            });
          }
        }
        if (!cancelled) setItems(out);
      } catch {
        if (!cancelled) setDecryptErr(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [itemsQ.data]);

  // Decrypt folder names.
  useEffect(() => {
    let cancelled = false;
    const rows = foldersQ.data;
    if (!rows) return;
    const key = getVaultKey();
    if (!key) return;
    (async () => {
      try {
        const out = [];
        for (const f of rows) {
          let name = "Folder";
          try {
            const d = await decryptItem(key, {
              nonce: f.name_nonce,
              ciphertext: f.name_ct,
            });
            name = d?.name || name;
          } catch {
            name = "Unreadable folder";
          }
          out.push({
            id: f.id,
            parent_id: f.parent_id ?? null,
            name,
            created_at: f.created_at,
            updated_at: f.updated_at,
          });
        }
        if (!cancelled) setFolders(out);
      } catch {
        if (!cancelled) setFolders([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [foldersQ.data]);

  const folderById = useMemo(() => {
    const m = new Map();
    (folders || []).forEach((f) => m.set(f.id, f));
    return m;
  }, [folders]);

  // Breadcrumb chain from root down to the current folder.
  const crumbs = useMemo(() => {
    const chain = [];
    const guard = new Set();
    let cur = currentFolder;
    while (cur) {
      const f = folderById.get(cur);
      if (!f || guard.has(cur)) break;
      guard.add(cur);
      chain.unshift(f);
      cur = f.parent_id;
    }
    return chain;
  }, [currentFolder, folderById]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["vault", "items"] });
    qc.invalidateQueries({ queryKey: ["vault", "folders"] });
  };

  const q = search.trim().toLowerCase();
  const searching = q.length > 0;

  const shownFolders = useMemo(() => {
    let list = folders || [];
    if (searching) list = list.filter((f) => f.name.toLowerCase().includes(q));
    else list = list.filter((f) => (f.parent_id ?? null) === currentFolder);
    return [...list].sort((a, b) => a.name.localeCompare(b.name));
  }, [folders, currentFolder, searching, q]);

  const shownItems = useMemo(() => {
    let list = items || [];
    if (searching) {
      list = list.filter((i) => {
        const d = i.data || {};
        return [d.title, d.username, d.url]
          .filter(Boolean)
          .some((s) => String(s).toLowerCase().includes(q));
      });
    } else {
      list = list.filter((i) => (i.folder_id ?? null) === currentFolder);
    }
    return [...list].sort((a, b) =>
      String(b.updated_at).localeCompare(String(a.updated_at)),
    );
  }, [items, currentFolder, searching, q]);

  const onDelete = async (id) => {
    touchVault();
    try {
      await deleteVaultItem(id);
      toast.success("Deleted");
      setDetail(null);
      refresh();
    } catch (e) {
      toast.error(e?.detail || "Couldn’t delete");
    }
  };

  const onDeleteFolder = async (folder) => {
    touchVault();
    if (
      !window.confirm(
        `Delete “${folder.name}” and everything inside it? This can’t be undone.`,
      )
    )
      return;
    try {
      await deleteVaultFolder(folder.id);
      toast.success("Folder deleted");
      if (currentFolder === folder.id) setCurrentFolder(folder.parent_id ?? null);
      refresh();
    } catch (e) {
      toast.error(e?.detail || "Couldn’t delete folder");
    }
  };

  // Encrypt + upload each picked file into the current folder.
  const onPickFiles = async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (!files.length) return;
    touchVault();
    const key = getVaultKey();
    const pub = getAccountPublicKey();
    if (!key) {
      toast.error("Vault locked");
      return;
    }
    if (!pub) {
      toast.error("Lock and unlock the vault, then try again.");
      return;
    }
    for (const file of files) {
      setUploading((n) => n + 1);
      const t = toast.loading(`Encrypting ${file.name}…`);
      try {
        const fileKey = randomFileKey();
        const { blob, meta } = await encryptFile(file, fileKey);
        const wrapped_key = await sealToPublicKey(pub, fileKey);
        const sealed = await encryptItem(key, {
          title: file.name,
          mime: file.type || "application/octet-stream",
          file: meta,
        });
        toast.loading(`Uploading ${file.name}…`, { id: t });
        await uploadVaultFile({
          nonce: sealed.nonce,
          ciphertext: sealed.ciphertext,
          wrapped_key,
          folderId: currentFolder,
          blob,
        });
        toast.success(`Uploaded ${file.name}`, { id: t });
      } catch (err) {
        toast.error(err?.detail || `Couldn’t upload ${file.name}`, { id: t });
      } finally {
        setUploading((n) => n - 1);
      }
    }
    refresh();
  };

  const loading =
    itemsQ.isLoading || foldersQ.isLoading || items === null || folders === null;
  if (loading) {
    return <VaultCenter>Decrypting your vault…</VaultCenter>;
  }
  if (decryptErr) {
    return (
      <VaultCenter>
        <div className="empty__title">Couldn’t decrypt</div>
        <div className="empty__body">
          The vault is locked or the key changed. Try locking and unlocking
          again.
        </div>
      </VaultCenter>
    );
  }

  const empty = shownFolders.length === 0 && shownItems.length === 0;

  return (
    <div className="vault-home">
      <input
        ref={fileInputRef}
        type="file"
        multiple
        hidden
        onChange={onPickFiles}
      />
      <div className="vault-tabs">
        <button
          className="vault-tab"
          data-active={tab === "mine"}
          onClick={() => setTab("mine")}
        >
          My vault
        </button>
        <button
          className="vault-tab"
          data-active={tab === "shared"}
          onClick={() => setTab("shared")}
        >
          Shared with me
          {incomingCount > 0 && (
            <span className="vault-segment__count">{incomingCount}</span>
          )}
        </button>
      </div>

      {tab === "shared" ? (
        <SharedWithMe />
      ) : (
        <>
      <div className="vault-toolbar">
        <div className="vault-breadcrumb">
          <button
            className="vault-crumb"
            data-active={currentFolder === null}
            onClick={() => {
              setCurrentFolder(null);
              setSearch("");
            }}
          >
            <Icon name="lock" size={12} /> Vault
          </button>
          {crumbs.map((f) => (
            <React.Fragment key={f.id}>
              <Icon name="chevronRight" size={12} className="vault-crumb__sep" />
              <button
                className="vault-crumb"
                data-active={currentFolder === f.id}
                onClick={() => {
                  setCurrentFolder(f.id);
                  setSearch("");
                }}
              >
                {f.name}
              </button>
            </React.Fragment>
          ))}
        </div>
        <div className="vault-search">
          <Icon name="search" size={14} style={{ color: "var(--ink-3)" }} />
          <input
            placeholder="Search the vault…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {search && (
            <button
              className="btn-icon"
              style={{ width: 22, height: 22 }}
              onClick={() => setSearch("")}
              aria-label="Clear"
            >
              <Icon name="x" size={11} />
            </button>
          )}
        </div>
        <button
          className="btn btn--secondary"
          onClick={() => setNewFolderOpen(true)}
        >
          <Icon name="folderPlus" size={14} /> New folder
        </button>
        <button
          className="btn btn--secondary"
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading > 0}
        >
          <Icon name="upload" size={14} />{" "}
          {uploading > 0 ? `Uploading ${uploading}…` : "Upload"}
        </button>
        <button className="btn btn--primary" onClick={() => setAddOpen(true)}>
          <Icon name="plus" size={14} /> Add
        </button>
      </div>

      {empty ? (
        <div className="vault-empty">
          <div className="empty__icon">
            <Icon name="lock" size={26} strokeWidth={1.4} />
          </div>
          <div className="empty__title">
            {searching ? "Nothing matches" : "This folder is empty"}
          </div>
          <div className="empty__body">
            {searching
              ? "Try a different search."
              : "Upload a file, create a folder, or add a password or note. Everything is encrypted on your device before it leaves it."}
          </div>
          {!searching && (
            <div style={{ display: "flex", gap: 10, marginTop: 14 }}>
              <button
                className="btn btn--secondary"
                onClick={() => fileInputRef.current?.click()}
              >
                <Icon name="upload" size={14} /> Upload a file
              </button>
              <button
                className="btn btn--primary"
                onClick={() => setAddOpen(true)}
              >
                <Icon name="plus" size={14} /> Add an item
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className="vault-list">
          {shownFolders.map((f) => (
            <div key={f.id} className="vault-row vault-row--folder">
              <button
                className="vault-row__hit"
                onClick={() => {
                  touchVault();
                  setCurrentFolder(f.id);
                  setSearch("");
                }}
              >
                <span className="vault-row__icon" data-kind="folder">
                  <Icon name="folder" size={16} />
                </span>
                <span className="vault-row__main">
                  <span className="vault-row__title">{f.name}</span>
                  <span className="vault-row__sub">Folder</span>
                </span>
              </button>
              <button
                className="btn-icon vault-row__del"
                title="Delete folder"
                aria-label="Delete folder"
                onClick={() => onDeleteFolder(f)}
              >
                <Icon name="trash" size={13} />
              </button>
            </div>
          ))}
          {shownItems.map((item) => {
            const isFile = item.has_file || item.kind === "file";
            return (
              <div
                key={item.id}
                className={`vault-row${isFile ? " vault-row--file" : ""}`}
              >
                <button
                  className="vault-row__hit"
                  onClick={() => {
                    touchVault();
                    if (isFile) setViewer(item);
                    else setDetail(item);
                  }}
                >
                  <span className="vault-row__icon" data-kind={item.kind}>
                    <Icon
                      name={
                        isFile
                          ? fileIconName(item.data?.title, item.data?.mime)
                          : KIND_META[item.kind]?.icon || "document"
                      }
                      size={16}
                    />
                  </span>
                  <span className="vault-row__main">
                    <span className="vault-row__title">
                      {item.corrupt
                        ? "Unreadable item"
                        : item.data?.title || "(untitled)"}
                    </span>
                    <span className="vault-row__sub">
                      {isFile
                        ? formatBytes(item.size_bytes) || "File"
                        : item.kind === "password"
                          ? item.data?.username || item.data?.url || "Password"
                          : KIND_META[item.kind]?.label || "Secure item"}
                    </span>
                  </span>
                </button>
                <span className="vault-row__meta">
                  {relTime(item.updated_at)}
                </span>
                <button
                  className="btn-icon vault-row__del"
                  title="Delete"
                  aria-label="Delete"
                  onClick={() => onDelete(item.id)}
                >
                  <Icon name="trash" size={13} />
                </button>
              </div>
            );
          })}
        </div>
      )}
        </>
      )}

      <AddItemModal
        open={addOpen}
        folderId={currentFolder}
        onClose={() => setAddOpen(false)}
        onSaved={() => {
          setAddOpen(false);
          refresh();
        }}
      />
      <NewFolderModal
        open={newFolderOpen}
        parentId={currentFolder}
        onClose={() => setNewFolderOpen(false)}
        onSaved={() => {
          setNewFolderOpen(false);
          refresh();
        }}
      />
      <ItemDetailModal
        item={detail}
        onClose={() => setDetail(null)}
        onDelete={onDelete}
        onShare={(it) => setShareItem(it)}
        onSaved={() => {
          setDetail(null);
          refresh();
        }}
      />
      <FileViewerModal
        open={!!viewer}
        sourceId={viewer?.id}
        title={viewer?.data?.title || "File"}
        mime={viewer?.data?.mime}
        size={viewer?.size_bytes}
        loadBlob={() => decryptFileToBlob(viewer)}
        actions={({ save }) => (
          <>
            <button
              className="btn btn--danger btn--sm"
              onClick={() => {
                if (window.confirm("Delete this file permanently?")) {
                  const id = viewer.id;
                  setViewer(null);
                  onDelete(id);
                }
              }}
            >
              <Icon name="trash" size={13} /> Delete
            </button>
            <div style={{ display: "flex", gap: 10 }}>
              <button
                className="btn btn--secondary"
                onClick={() => setShareItem(viewer)}
              >
                <Icon name="share" size={13} /> Share
              </button>
              <button className="btn btn--primary" onClick={save}>
                <Icon name="download" size={13} /> Download
              </button>
            </div>
          </>
        )}
        onClose={() => setViewer(null)}
      />
      <ShareModal
        item={shareItem}
        onClose={() => setShareItem(null)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// FileViewerModal — decrypt + preview a file in-browser (P3). Images, video,
// audio, PDF, and plain text render inline; everything else is download-only.
// Decryption happens on this device; the server only ever sees ciphertext.
// ---------------------------------------------------------------------------

// Generic file viewer: given a stable `sourceId`, display meta and decrypt
// via `loadBlob()` (owner or recipient path), render the right inline preview,
// and let the footer (`actions`) trigger a Download of the decrypted blob.
function FileViewerModal({ open, sourceId, title = "File", mime, size, loadBlob, actions, onClose }) {
  const [state, setState] = useState("loading"); // loading|locked|error|image|video|audio|pdf|text|nopreview
  const [url, setUrl] = useState(null);
  const [text, setText] = useState("");
  const blobRef = useRef(null);
  const urlRef = useRef(null);
  const loadRef = useRef(loadBlob);
  loadRef.current = loadBlob;

  useEffect(() => {
    if (!open || !sourceId) return;
    let cancelled = false;
    setState("loading");
    setUrl(null);
    setText("");
    blobRef.current = null;
    const kind = previewKind(mime);
    (async () => {
      try {
        const blob = await loadRef.current();
        if (cancelled) return;
        blobRef.current = blob;
        if (kind === "text") {
          const t = await blob.text();
          if (cancelled) return;
          // Cap what we render so a huge text file can't freeze the tab.
          setText(t.length > 200_000 ? t.slice(0, 200_000) + "\n…" : t);
          setState("text");
        } else if (kind === "none") {
          setState("nopreview");
        } else {
          const u = URL.createObjectURL(blob);
          urlRef.current = u;
          setUrl(u);
          setState(kind);
        }
      } catch (e) {
        if (!cancelled) setState(e?.message === "locked" ? "locked" : "error");
      }
    })();
    return () => {
      cancelled = true;
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
      blobRef.current = null;
    };
  }, [open, sourceId, mime]);

  if (!open) return null;
  const save = () => {
    if (blobRef.current) saveBlob(blobRef.current, title);
  };

  return (
    <Modal open={open} onClose={onClose} size="lg" labelledBy="vault-view-title">
      <ModalClose onClose={onClose} />
      <div className="vault-modal-form">
        <div className="vault-viewer">
          <div className="vault-viewer__head">
            <span className="vault-row__icon" data-kind="file">
              <Icon name={fileIconName(title, mime)} size={18} />
            </span>
            <div style={{ minWidth: 0 }}>
              <h2 id="vault-view-title" className="vault-viewer__title">
                {title}
              </h2>
              <div className="vault-viewer__meta">
                {[formatBytes(size), mime].filter(Boolean).join(" · ")}
              </div>
            </div>
          </div>

          <div className="vault-viewer__stage">
            {state === "loading" && (
              <div className="vault-viewer__msg">Decrypting…</div>
            )}
            {state === "locked" && (
              <div className="vault-viewer__msg">
                Lock and unlock the vault, then try again.
              </div>
            )}
            {state === "error" && (
              <div className="vault-viewer__msg">Couldn’t decrypt this file.</div>
            )}
            {state === "image" && (
              <img className="vault-viewer__img" src={url} alt={title} />
            )}
            {state === "video" && (
              <video className="vault-viewer__media" src={url} controls />
            )}
            {state === "audio" && (
              <audio className="vault-viewer__audio" src={url} controls />
            )}
            {state === "pdf" && (
              <iframe
                className="vault-viewer__frame"
                src={url}
                title={title}
                sandbox=""
              />
            )}
            {state === "text" && (
              <pre className="vault-viewer__text">{text}</pre>
            )}
            {state === "nopreview" && (
              <div className="vault-viewer__msg">
                No preview for this file type. Download it to open it on your
                device.
              </div>
            )}
          </div>
        </div>

        <div
          className="vault-modal-actions"
          style={{ justifyContent: "space-between" }}
        >
          {actions ? actions({ save }) : <span />}
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// NewFolderModal — create a folder with an encrypted name
// ---------------------------------------------------------------------------

function NewFolderModal({ open, parentId, onClose, onSaved }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const close = () => {
    setName("");
    onClose?.();
  };

  const save = async (e) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    const key = getVaultKey();
    if (!key) {
      toast.error("Vault locked");
      return;
    }
    setBusy(true);
    try {
      const sealed = await encryptItem(key, { name: trimmed });
      await createVaultFolder({
        parent_id: parentId,
        name_nonce: sealed.nonce,
        name_ct: sealed.ciphertext,
      });
      toast.success("Folder created");
      setName("");
      onSaved?.();
    } catch (err) {
      toast.error(err?.detail || "Couldn’t create the folder");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={close} size="sm" labelledBy="vault-folder-title">
      <ModalClose onClose={close} />
      <form onSubmit={save} style={{ padding: 22, display: "flex", flexDirection: "column", gap: 14 }}>
        <h2 id="vault-folder-title" style={{ margin: 0, fontSize: 18 }}>
          New folder
        </h2>
        <label className="vault-field">
          <span>Folder name</span>
          <input
            className="input"
            value={name}
            autoFocus
            maxLength={120}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Tax documents"
          />
        </label>
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button type="button" className="btn btn--secondary" onClick={close} disabled={busy}>
            Cancel
          </button>
          <button className="btn btn--primary" disabled={!name.trim() || busy}>
            {busy ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// AddItemModal — create a password, note, seed phrase, or card / ID
// ---------------------------------------------------------------------------

function AddItemModal({ open, folderId = null, onClose, onSaved }) {
  const [kind, setKind] = useState("password");
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState("");
  // password
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [url, setUrl] = useState("");
  const [pnotes, setPnotes] = useState("");
  const [showPw, setShowPw] = useState(false);
  // note
  const [body, setBody] = useState("");
  // seed phrase
  const [phrase, setPhrase] = useState("");
  const [passphrase, setPassphrase] = useState("");
  // card / id
  const [cardholder, setCardholder] = useState("");
  const [cardNumber, setCardNumber] = useState("");
  const [expiry, setExpiry] = useState("");
  const [cvv, setCvv] = useState("");
  const [brand, setBrand] = useState("");

  const reset = () => {
    setKind("password");
    setTitle("");
    setUsername(""); setPassword(""); setUrl(""); setPnotes(""); setShowPw(false);
    setBody("");
    setPhrase(""); setPassphrase("");
    setCardholder(""); setCardNumber(""); setExpiry(""); setCvv(""); setBrand("");
  };

  const close = () => {
    reset();
    onClose?.();
  };

  const canSave =
    !busy &&
    !!title.trim() &&
    (kind === "password"
      ? !!password
      : kind === "note"
        ? !!body.trim()
        : kind === "seed"
          ? !!phrase.trim()
          : kind === "card"
            ? !!cardNumber.trim()
            : true);

  const buildPlaintext = () => {
    const t = title.trim();
    if (kind === "password") return { title: t, username, password, url, notes: pnotes };
    if (kind === "note") return { title: t, body };
    // Seed + card carry only their structured secret fields — no freeform
    // notes, which keeps anything potentially shareable free of stray context.
    if (kind === "seed") return { title: t, phrase: phrase.trim().replace(/\s+/g, " "), passphrase };
    if (kind === "card") return { title: t, cardholder, number: cardNumber, expiry, cvv, brand };
    return { title: t };
  };

  const save = async (e) => {
    e.preventDefault();
    if (!canSave) return;
    const key = getVaultKey();
    if (!key) {
      toast.error("Vault locked");
      return;
    }
    setBusy(true);
    try {
      const sealed = await encryptItem(key, buildPlaintext());
      await createVaultItem({
        kind,
        nonce: sealed.nonce,
        ciphertext: sealed.ciphertext,
        folder_id: folderId,
      });
      toast.success(`${KIND_META[kind]?.label || "Item"} saved`);
      reset();
      onSaved?.();
    } catch (err) {
      toast.error(err?.detail || "Couldn’t save");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={close} size="md" labelledBy="vault-add-title">
      <ModalClose onClose={close} />
      <form onSubmit={save} className="vault-modal-form">
        <div className="vault-modal-scroll">
        <h2 id="vault-add-title" style={{ margin: 0, fontSize: 18 }}>
          Add to vault
        </h2>

        <div className="vault-segments vault-segments--wrap">
          {Object.entries(KIND_META).map(([k, m]) => (
            <button
              key={k}
              type="button"
              className="vault-segment"
              data-active={kind === k}
              onClick={() => setKind(k)}
            >
              <Icon name={m.icon} size={13} /> {m.label}
            </button>
          ))}
        </div>

        <label className="vault-field">
          <span>Title</span>
          <input
            className="input"
            value={title}
            autoFocus
            onChange={(e) => setTitle(e.target.value)}
            placeholder={KIND_META[kind]?.placeholder}
          />
        </label>

        {kind === "password" && (
          <>
            <label className="vault-field">
              <span>Username or email</span>
              <input
                className="input"
                value={username}
                autoComplete="off"
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            <label className="vault-field">
              <span>Password</span>
              <div className="vault-field__input">
                <input
                  className="input"
                  type={showPw ? "text" : "password"}
                  value={password}
                  autoComplete="off"
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  className="btn-icon"
                  onClick={() => setShowPw((v) => !v)}
                  aria-label={showPw ? "Hide" : "Show"}
                >
                  <Icon name={showPw ? "eyeOff" : "eye"} size={14} />
                </button>
                <button
                  type="button"
                  className="btn-icon"
                  title="Generate strong password"
                  onClick={() => {
                    setPassword(generatePassword());
                    setShowPw(true);
                  }}
                >
                  <Icon name="refresh" size={14} />
                </button>
              </div>
            </label>
            <label className="vault-field">
              <span>Website</span>
              <input
                className="input"
                value={url}
                inputMode="url"
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://"
              />
            </label>
            <label className="vault-field">
              <span>Notes</span>
              <textarea
                className="input"
                rows={2}
                value={pnotes}
                onChange={(e) => setPnotes(e.target.value)}
              />
            </label>
          </>
        )}

        {kind === "note" && (
          <label className="vault-field">
            <span>Note</span>
            <textarea
              className="input"
              rows={8}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="Type anything — it’s encrypted before it leaves your device."
            />
          </label>
        )}

        {kind === "seed" && (
          <>
            <div className="vault-warn">
              <Icon name="alert" size={16} />
              <div>
                <strong>Never share your recovery phrase.</strong> Anyone with
                these words controls the wallet. neuthek will never ask for it —
                store it here, and type it only on a device you trust.
              </div>
            </div>
            <label className="vault-field">
              <span>Recovery phrase</span>
              <textarea
                className="input vault-mono"
                rows={3}
                value={phrase}
                spellCheck={false}
                autoComplete="off"
                onChange={(e) => setPhrase(e.target.value)}
                placeholder="word1 word2 word3 … (12 or 24 words)"
              />
            </label>
            <label className="vault-field">
              <span>Passphrase (optional)</span>
              <input
                className="input"
                value={passphrase}
                autoComplete="off"
                onChange={(e) => setPassphrase(e.target.value)}
                placeholder="BIP39 25th-word passphrase, if you use one"
              />
            </label>
          </>
        )}

        {kind === "card" && (
          <>
            <label className="vault-field">
              <span>Cardholder / name</span>
              <input
                className="input"
                value={cardholder}
                autoComplete="off"
                onChange={(e) => setCardholder(e.target.value)}
              />
            </label>
            <label className="vault-field">
              <span>Number</span>
              <input
                className="input vault-mono"
                value={cardNumber}
                inputMode="numeric"
                autoComplete="off"
                onChange={(e) => setCardNumber(e.target.value)}
                placeholder="Card or ID number"
              />
            </label>
            <div className="vault-field-row">
              <label className="vault-field">
                <span>Expiry</span>
                <input
                  className="input"
                  value={expiry}
                  autoComplete="off"
                  onChange={(e) => setExpiry(e.target.value)}
                  placeholder="MM / YY"
                />
              </label>
              <label className="vault-field">
                <span>CVV / code</span>
                <input
                  className="input vault-mono"
                  value={cvv}
                  inputMode="numeric"
                  autoComplete="off"
                  onChange={(e) => setCvv(e.target.value)}
                />
              </label>
            </div>
            <label className="vault-field">
              <span>Type / issuer (optional)</span>
              <input
                className="input"
                value={brand}
                autoComplete="off"
                onChange={(e) => setBrand(e.target.value)}
                placeholder="e.g. Visa, Passport, Driver’s license"
              />
            </label>
          </>
        )}

        </div>
        <div className="vault-modal-actions">
          <button type="button" className="btn btn--secondary" onClick={close}>
            Cancel
          </button>
          <button className="btn btn--primary" disabled={!canSave}>
            {busy ? "Encrypting…" : "Save"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// ItemDetailModal — view / reveal / copy / edit / delete
// ---------------------------------------------------------------------------

function ItemDetailModal({ item, onClose, onDelete, onShare, onSaved }) {
  const [reveal, setReveal] = useState(false);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState(null);

  useEffect(() => {
    setReveal(false);
    setEditing(false);
    setDraft(item ? { ...(item.data || {}) } : null);
  }, [item]);

  if (!item) return null;
  const d = item.data || {};
  const toggleReveal = () => setReveal((v) => !v);

  const saveEdit = async () => {
    const key = getVaultKey();
    if (!key) {
      toast.error("Vault locked");
      return;
    }
    setBusy(true);
    try {
      const sealed = await encryptItem(key, draft);
      await updateVaultItem(item.id, {
        nonce: sealed.nonce,
        ciphertext: sealed.ciphertext,
      });
      toast.success("Saved");
      onSaved?.();
    } catch (e) {
      toast.error(e?.detail || "Couldn’t save");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={!!item} onClose={onClose} size="md" labelledBy="vault-detail-title">
      <ModalClose onClose={onClose} />
      <div className="vault-modal-form">
        <div className="vault-modal-scroll">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span className="vault-row__icon" data-kind={item.kind}>
            <Icon name={KIND_META[item.kind]?.icon || "document"} size={18} />
          </span>
          <div>
            <h2 id="vault-detail-title" style={{ margin: 0, fontSize: 18 }}>
              {d.title || "(untitled)"}
            </h2>
            <div style={{ fontSize: 12, color: "var(--ink-3)" }}>
              {KIND_META[item.kind]?.label} · Updated {relTime(item.updated_at)}
            </div>
          </div>
        </div>

        {item.corrupt ? (
          <div className="vault-field__err">
            This item couldn’t be decrypted with the current key.
          </div>
        ) : editing ? (
          <EditFields kind={item.kind} draft={draft} setDraft={setDraft} />
        ) : item.kind === "password" ? (
          <>
            <DetailRow label="Username" value={d.username} copyable />
            <DetailRow
              label="Password"
              value={d.password}
              secret
              reveal={reveal}
              onReveal={toggleReveal}
              copyable
              mono
            />
            <DetailRow label="Website" value={d.url} link copyable />
            <DetailRow label="Notes" value={d.notes} multiline />
          </>
        ) : item.kind === "seed" ? (
          <SeedView
            phrase={d.phrase}
            passphrase={d.passphrase}
            reveal={reveal}
            onReveal={toggleReveal}
          />
        ) : item.kind === "card" ? (
          <>
            <DetailRow label="Cardholder / name" value={d.cardholder} copyable />
            <DetailRow
              label="Number"
              value={d.number}
              secret
              reveal={reveal}
              onReveal={toggleReveal}
              copyable
              mono
            />
            <DetailRow label="Expiry" value={d.expiry} />
            <DetailRow
              label="CVV / code"
              value={d.cvv}
              secret
              reveal={reveal}
              onReveal={toggleReveal}
              copyable
              mono
            />
            <DetailRow label="Type / issuer" value={d.brand} />
          </>
        ) : (
          <DetailRow label="" value={d.body} multiline />
        )}

        </div>
        <div className="vault-modal-actions" style={{ justifyContent: "space-between" }}>
          <button
            className="btn btn--danger btn--sm"
            onClick={() => {
              if (window.confirm("Delete this item permanently?")) {
                onDelete?.(item.id);
              }
            }}
          >
            <Icon name="trash" size={13} /> Delete
          </button>
          <div style={{ display: "flex", gap: 10 }}>
            {!item.corrupt &&
              (editing ? (
                <>
                  <button
                    className="btn btn--secondary"
                    onClick={() => {
                      setEditing(false);
                      setDraft({ ...(item.data || {}) });
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    className="btn btn--primary"
                    onClick={saveEdit}
                    disabled={busy || !draft?.title?.trim()}
                  >
                    {busy ? "Saving…" : "Save"}
                  </button>
                </>
              ) : (
                <>
                  <button
                    className="btn btn--secondary"
                    onClick={() => onShare?.(item)}
                  >
                    <Icon name="share" size={13} /> Share
                  </button>
                  <button className="btn btn--secondary" onClick={() => setEditing(true)}>
                    <Icon name="edit" size={13} /> Edit
                  </button>
                </>
              ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}

// Reveal-gated viewer for a recovery phrase: a numbered word grid behind a
// tap-to-reveal cover, with copy-all. No freeform notes — nothing to leak.
function SeedView({ phrase, passphrase, reveal, onReveal }) {
  const words = String(phrase || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  return (
    <div className="vault-seed">
      <div className="vault-warn vault-warn--tight">
        <Icon name="alert" size={15} />
        <div>
          Never share these words. Anyone who has them controls the wallet.
        </div>
      </div>
      <div className="vault-seed__bar">
        <span className="vault-detail-row__label">
          Recovery phrase · {words.length} words
        </span>
        <div style={{ display: "flex", gap: 6 }}>
          <button
            className="btn-icon"
            onClick={onReveal}
            aria-label={reveal ? "Hide" : "Reveal"}
            title={reveal ? "Hide" : "Reveal"}
          >
            <Icon name={reveal ? "eyeOff" : "eye"} size={14} />
          </button>
          <button
            className="btn-icon"
            onClick={() => {
              touchVault();
              copyToClipboard(words.join(" "), "Recovery phrase");
            }}
            aria-label="Copy"
            title="Copy"
          >
            <Icon name="copy" size={14} />
          </button>
        </div>
      </div>
      {reveal ? (
        <ol className="vault-seed__grid">
          {words.map((w, i) => (
            <li key={i} className="vault-seed__word">
              <span className="vault-seed__num">{i + 1}</span>
              {w}
            </li>
          ))}
        </ol>
      ) : (
        <button type="button" className="vault-seed__cover" onClick={onReveal}>
          <Icon name="eye" size={15} /> Tap to reveal {words.length} words
        </button>
      )}
      {passphrase ? (
        <DetailRow
          label="Passphrase"
          value={passphrase}
          secret
          reveal={reveal}
          onReveal={onReveal}
          copyable
          mono
        />
      ) : null}
    </div>
  );
}

function EditFields({ kind, draft, setDraft }) {
  const set = (k) => (e) => setDraft((d) => ({ ...d, [k]: e.target.value }));
  const titleField = (
    <label className="vault-field">
      <span>Title</span>
      <input className="input" value={draft?.title || ""} onChange={set("title")} />
    </label>
  );

  if (kind === "note") {
    return (
      <>
        {titleField}
        <label className="vault-field">
          <span>Note</span>
          <textarea
            className="input"
            rows={8}
            value={draft?.body || ""}
            onChange={set("body")}
          />
        </label>
      </>
    );
  }

  if (kind === "seed") {
    return (
      <>
        {titleField}
        <label className="vault-field">
          <span>Recovery phrase</span>
          <textarea
            className="input vault-mono"
            rows={3}
            spellCheck={false}
            value={draft?.phrase || ""}
            onChange={set("phrase")}
          />
        </label>
        <label className="vault-field">
          <span>Passphrase (optional)</span>
          <input className="input" value={draft?.passphrase || ""} onChange={set("passphrase")} />
        </label>
      </>
    );
  }

  if (kind === "card") {
    return (
      <>
        {titleField}
        <label className="vault-field">
          <span>Cardholder / name</span>
          <input className="input" value={draft?.cardholder || ""} onChange={set("cardholder")} />
        </label>
        <label className="vault-field">
          <span>Number</span>
          <input className="input vault-mono" value={draft?.number || ""} onChange={set("number")} />
        </label>
        <div className="vault-field-row">
          <label className="vault-field">
            <span>Expiry</span>
            <input className="input" value={draft?.expiry || ""} onChange={set("expiry")} />
          </label>
          <label className="vault-field">
            <span>CVV / code</span>
            <input className="input vault-mono" value={draft?.cvv || ""} onChange={set("cvv")} />
          </label>
        </div>
        <label className="vault-field">
          <span>Type / issuer (optional)</span>
          <input className="input" value={draft?.brand || ""} onChange={set("brand")} />
        </label>
      </>
    );
  }

  // password
  return (
    <>
      {titleField}
      <label className="vault-field">
        <span>Username or email</span>
        <input className="input" value={draft?.username || ""} onChange={set("username")} />
      </label>
      <label className="vault-field">
        <span>Password</span>
        <input className="input" value={draft?.password || ""} onChange={set("password")} />
      </label>
      <label className="vault-field">
        <span>Website</span>
        <input className="input" value={draft?.url || ""} onChange={set("url")} />
      </label>
      <label className="vault-field">
        <span>Notes</span>
        <textarea
          className="input"
          rows={2}
          value={draft?.notes || ""}
          onChange={set("notes")}
        />
      </label>
    </>
  );
}

function DetailRow({ label, value, secret, reveal, onReveal, copyable, link, multiline, mono }) {
  if (!value) return null;
  return (
    <div className="vault-detail-row">
      {label && <span className="vault-detail-row__label">{label}</span>}
      <div className="vault-detail-row__value">
        {multiline ? (
          <pre className="vault-detail-row__pre">{value}</pre>
        ) : secret && !reveal ? (
          <span className="vault-detail-row__secret">••••••••••••</span>
        ) : link ? (
          <a href={value} target="_blank" rel="noopener noreferrer">
            {value}
          </a>
        ) : (
          <span className={mono ? "vault-mono" : undefined}>{value}</span>
        )}
        <div className="vault-detail-row__actions">
          {secret && (
            <button
              className="btn-icon"
              onClick={onReveal}
              aria-label={reveal ? "Hide" : "Reveal"}
              title={reveal ? "Hide" : "Reveal"}
            >
              <Icon name={reveal ? "eyeOff" : "eye"} size={14} />
            </button>
          )}
          {copyable && (
            <button
              className="btn-icon"
              onClick={() => {
                touchVault();
                copyToClipboard(value, label || "Copied");
              }}
              aria-label="Copy"
              title="Copy"
            >
              <Icon name="copy" size={14} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ShareModal — owner: seal an item to a recipient's account public key.
// No comments, no public links — a direct, revocable grant to a named account.
// ---------------------------------------------------------------------------

function ShareModal({ item, onClose }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [shares, setShares] = useState(null); // recipient list, null = loading

  useEffect(() => {
    setEmail("");
    setShares(null);
    if (!item) return;
    let cancelled = false;
    listItemShares(item.id)
      .then((s) => !cancelled && setShares(s))
      .catch(() => !cancelled && setShares([]));
    return () => {
      cancelled = true;
    };
  }, [item]);

  if (!item) return null;

  const refreshShares = async () => {
    try {
      setShares(await listItemShares(item.id));
    } catch {
      /* keep prior list */
    }
    qc.invalidateQueries({ queryKey: ["vault", "shares"] });
  };

  const share = async (e) => {
    e.preventDefault();
    const addr = email.trim();
    if (!addr || busy) return;
    if (!getAccountPrivateKey() || !getAccountPublicKey()) {
      toast.error("Lock and unlock the vault, then try again.");
      return;
    }
    setBusy(true);
    try {
      const rk = await getRecipientKey(addr);
      const bundle = await buildItemBundle(item);
      const bytes = new TextEncoder().encode(JSON.stringify(bundle));
      const sealed = await sealToPublicKey(rk.account_public_key, bytes);
      await shareVaultItem(item.id, {
        recipient_email: addr,
        sealed_payload: sealed,
      });
      toast.success(`Shared with ${addr}`);
      setEmail("");
      await refreshShares();
    } catch (err) {
      if (err?.status === 404)
        toast.error("No neuthek vault found for that email.");
      else if (err?.status === 400)
        toast.error(err?.detail || "Can’t share with that recipient.");
      else if (err?.message === "locked")
        toast.error("Lock and unlock the vault, then try again.");
      else toast.error(err?.detail || "Couldn’t share.");
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (grantId) => {
    try {
      await deleteShare(grantId);
      toast.success("Access revoked");
      await refreshShares();
    } catch (e) {
      toast.error(e?.detail || "Couldn’t revoke");
    }
  };

  return (
    <Modal open={!!item} onClose={onClose} size="md" labelledBy="vault-share-title">
      <ModalClose onClose={onClose} />
      <div style={{ padding: 22, display: "flex", flexDirection: "column", gap: 14 }}>
        <div>
          <h2 id="vault-share-title" style={{ margin: 0, fontSize: 18 }}>
            Share securely
          </h2>
          <p style={{ margin: "6px 0 0", color: "var(--ink-2)", fontSize: 13.5, lineHeight: 1.5 }}>
            “{item.data?.title || "This item"}” is sealed to the recipient’s
            neuthek key on your device — only they can open it. Revoke any time.
          </p>
        </div>
        <form onSubmit={share} style={{ display: "flex", gap: 8 }}>
          <input
            className="input"
            type="email"
            value={email}
            autoFocus
            placeholder="Recipient’s neuthek email"
            onChange={(e) => setEmail(e.target.value)}
            style={{ flex: 1 }}
          />
          <button className="btn btn--primary" disabled={!email.trim() || busy}>
            {busy ? "Sharing…" : "Share"}
          </button>
        </form>

        <div className="vault-share-list">
          {shares === null ? (
            <div className="vault-share-empty">Loading…</div>
          ) : shares.length === 0 ? (
            <div className="vault-share-empty">Not shared with anyone yet.</div>
          ) : (
            shares.map((s) => (
              <div key={s.id} className="vault-share-row">
                <span className="vault-row__icon" data-kind="contact">
                  <Icon name="user" size={14} />
                </span>
                <span className="vault-share-row__main">
                  <span className="vault-share-row__email">{s.recipient_email}</span>
                  {s.recipient_display_name && (
                    <span className="vault-share-row__name">
                      {s.recipient_display_name}
                    </span>
                  )}
                </span>
                <button
                  className="btn btn--secondary btn--sm"
                  onClick={() => revoke(s.id)}
                >
                  Revoke
                </button>
              </div>
            ))
          )}
        </div>

        <PublicLinkSection item={item} />
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// PublicLinkSection — an "anyone with the link" share, optionally password-
// protected. The decryption key lives only in the URL fragment, which the
// server never sees; the password (if any) is mixed into the key on this
// device. Because the secret never reaches us, the full URL can only be shown
// ONCE, at creation — afterwards you can revoke or replace, not re-reveal.
// ---------------------------------------------------------------------------

function PublicLinkSection({ item }) {
  const [existing, setExisting] = useState(undefined); // undefined=loading, null=none
  const [createdUrl, setCreatedUrl] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [pwEnabled, setPwEnabled] = useState(false);
  const [password, setPassword] = useState("");
  const [expiry, setExpiry] = useState(0); // days; 0 = never
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setExisting(undefined);
    setCreatedUrl(null);
    setShowForm(false);
    setPwEnabled(false);
    setPassword("");
    setExpiry(0);
    if (!item) return;
    let cancelled = false;
    getPublicLink(item.id)
      .then((l) => !cancelled && setExisting(l))
      .catch(() => !cancelled && setExisting(null));
    return () => {
      cancelled = true;
    };
  }, [item]);

  const create = async () => {
    if (busy) return;
    if (pwEnabled && !password.trim()) {
      toast.error("Enter a password or turn it off.");
      return;
    }
    if (!getAccountPrivateKey()) {
      toast.error("Lock and unlock the vault, then try again.");
      return;
    }
    setBusy(true);
    try {
      const bundle = await buildItemBundle(item);
      const secret = randomLinkSecret();
      let salt = null;
      let iters = null;
      let key;
      if (pwEnabled) {
        salt = randomBytes(PUBLINK_SALT_BYTES);
        iters = PUBLINK_KDF_ITERATIONS;
        key = await derivePublicLinkKey(secret, password, salt, iters);
      } else {
        key = await derivePublicLinkKey(secret, null, null, null);
      }
      const sealed = await sealPublicLink(key, bundle);
      const link = await createPublicLink(item.id, {
        sealed_payload: sealed,
        password_required: pwEnabled,
        kdf_salt: salt ? bytesToB64(salt) : null,
        kdf_iterations: iters,
        expires_in_days: expiry || null,
      });
      const url = `${window.location.origin}/v/${link.token}#${bytesToB64Url(secret)}`;
      setCreatedUrl(url);
      setExisting(link);
      setShowForm(false);
      setPassword("");
    } catch (err) {
      if (err?.message === "locked")
        toast.error("Lock and unlock the vault, then try again.");
      else toast.error(err?.detail || "Couldn’t create the link.");
    } finally {
      setBusy(false);
    }
  };

  const revoke = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await deletePublicLink(item.id);
      toast.success("Public link revoked");
      setExisting(null);
      setCreatedUrl(null);
      setShowForm(false);
    } catch (e) {
      toast.error(e?.detail || "Couldn’t revoke");
    } finally {
      setBusy(false);
    }
  };

  const copy = () => {
    if (createdUrl) copyToClipboard(createdUrl, "Link");
  };

  return (
    <div className="vault-publink">
      <div className="vault-publink__head">
        <Icon name="cloud" size={14} />
        <span>Public link</span>
        <span className="vault-publink__sub">anyone with the link can open it</span>
      </div>

      {existing === undefined ? (
        <div className="vault-share-empty">Loading…</div>
      ) : createdUrl ? (
        <>
          <div className="vault-publink__url">
            <input className="input vault-mono" readOnly value={createdUrl} onFocus={(e) => e.target.select()} />
            <button className="btn btn--secondary btn--sm" onClick={copy}>
              <Icon name="copy" size={13} /> Copy
            </button>
          </div>
          <div className="vault-publink__note">
            <Icon name="alert" size={13} /> Copy it now — for your security we
            can’t show this link again.{existing?.password_required ? " Share the password separately." : ""}
          </div>
          <button className="btn btn--danger btn--sm" onClick={revoke} disabled={busy}>
            Revoke link
          </button>
        </>
      ) : existing && !showForm ? (
        <>
          <div className="vault-publink__active">
            <span><Icon name="check" size={13} /> A public link is active{existing.password_required ? " · password-protected" : ""}{existing.expires_at ? ` · expires ${relTime(existing.expires_at)}` : ""}.</span>
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            <button className="btn btn--secondary btn--sm" onClick={() => setShowForm(true)} disabled={busy}>
              Replace
            </button>
            <button className="btn btn--danger btn--sm" onClick={revoke} disabled={busy}>
              Revoke
            </button>
          </div>
        </>
      ) : (
        <>
          <button
            type="button"
            className="vault-ack vault-ack--inline"
            data-checked={pwEnabled}
            onClick={() => setPwEnabled((v) => !v)}
          >
            <span className="vault-ack__box">
              <Icon name="check" size={11} strokeWidth={2.6} />
            </span>
            <span>Require a password to open</span>
          </button>
          {pwEnabled && (
            <input
              className="input"
              type="text"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Set a password (share it separately)"
            />
          )}
          <label className="vault-field">
            <span>Expires</span>
            <select
              className="input"
              value={expiry}
              onChange={(e) => setExpiry(Number(e.target.value))}
            >
              <option value={0}>Never</option>
              <option value={1}>After 1 day</option>
              <option value={7}>After 7 days</option>
              <option value={30}>After 30 days</option>
              <option value={90}>After 90 days</option>
            </select>
          </label>
          <button className="btn btn--primary btn--sm" onClick={create} disabled={busy}>
            {busy ? "Creating…" : existing ? "Replace link" : "Create public link"}
          </button>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SharedWithMe — recipient: items shared with me, opened by unsealing the
// bundle on this device. Read-only, no comments.
// ---------------------------------------------------------------------------

function SharedWithMe() {
  const qc = useQueryClient();
  const sharesQ = useQuery({
    queryKey: ["vault", "shares"],
    queryFn: listIncomingShares,
    staleTime: 30_000,
  });
  const [opened, setOpened] = useState(null); // { grant, bundle }
  const [opening, setOpening] = useState(false);

  const open = async (grant) => {
    const priv = getAccountPrivateKey();
    if (!priv) {
      toast.error("Lock and unlock the vault, then try again.");
      return;
    }
    setOpening(true);
    const t = toast.loading("Opening…");
    try {
      const bytes = await unsealFromPrivateKey(priv, grant.sealed_payload);
      const bundle = JSON.parse(new TextDecoder().decode(bytes));
      toast.dismiss(t);
      setOpened({ grant, bundle });
    } catch {
      toast.error("Couldn’t open this share.", { id: t });
    } finally {
      setOpening(false);
    }
  };

  const remove = async (grantId) => {
    try {
      await deleteShare(grantId);
      toast.success("Removed");
      qc.invalidateQueries({ queryKey: ["vault", "shares"] });
    } catch (e) {
      toast.error(e?.detail || "Couldn’t remove");
    }
  };

  if (sharesQ.isLoading) return <VaultCenter>Loading…</VaultCenter>;
  const rows = sharesQ.data || [];
  const openedIsFile =
    opened && (opened.grant.has_file || opened.bundle?.kind === "file");

  return (
    <>
      {rows.length === 0 ? (
        <div className="vault-empty">
          <div className="empty__icon">
            <Icon name="share" size={26} strokeWidth={1.4} />
          </div>
          <div className="empty__title">Nothing shared with you</div>
          <div className="empty__body">
            When someone shares a vault item with you, it appears here —
            end-to-end encrypted, openable only by you.
          </div>
        </div>
      ) : (
        <div className="vault-list">
          {rows.map((g) => {
            const isFile = g.has_file || g.kind === "file";
            return (
              <div key={g.id} className="vault-row">
                <button
                  className="vault-row__hit"
                  disabled={opening}
                  onClick={() => open(g)}
                >
                  <span className="vault-row__icon" data-kind={g.kind}>
                    <Icon
                      name={isFile ? "file" : KIND_META[g.kind]?.icon || "document"}
                      size={16}
                    />
                  </span>
                  <span className="vault-row__main">
                    <span className="vault-row__title">
                      {isFile ? "Shared file" : KIND_META[g.kind]?.label || "Shared item"}
                    </span>
                    <span className="vault-row__sub">
                      From {g.owner_email}
                      {isFile && g.size_bytes
                        ? ` · ${formatBytes(g.size_bytes)}`
                        : ""}
                    </span>
                  </span>
                </button>
                <button
                  className="btn-icon vault-row__del"
                  title="Remove"
                  aria-label="Remove"
                  onClick={() => remove(g.id)}
                >
                  <Icon name="x" size={13} />
                </button>
              </div>
            );
          })}
        </div>
      )}

      <FileViewerModal
        open={!!opened && openedIsFile}
        sourceId={opened?.grant?.id}
        title={opened?.bundle?.data?.title || "Shared file"}
        mime={opened?.bundle?.data?.mime}
        size={opened?.grant?.size_bytes}
        loadBlob={() => decryptSharedFileToBlob(opened.grant.id, opened.bundle)}
        actions={({ save }) => (
          <>
            <span style={{ fontSize: 12, color: "var(--ink-3)" }}>
              Shared with you · read-only
            </span>
            <button className="btn btn--primary" onClick={save}>
              <Icon name="download" size={13} /> Download
            </button>
          </>
        )}
        onClose={() => setOpened(null)}
      />
      <SharedSecureViewer
        open={!!opened && !openedIsFile}
        kind={opened?.bundle?.kind}
        data={opened?.bundle?.data}
        ownerEmail={opened?.grant?.owner_email}
        onClose={() => setOpened(null)}
      />
    </>
  );
}

// Read-only viewer for a shared secure item (password/note/seed/card). Reuses
// the same reveal-gated rows; no edit, no delete, no comments.
function SharedSecureViewer({ open, kind, data, ownerEmail, onClose }) {
  const [reveal, setReveal] = useState(false);
  useEffect(() => {
    if (open) setReveal(false);
  }, [open]);
  if (!open) return null;
  const d = data || {};
  const toggle = () => setReveal((v) => !v);
  return (
    <Modal open={open} onClose={onClose} size="md" labelledBy="vault-shared-title">
      <ModalClose onClose={onClose} />
      <div className="vault-modal-form">
        <div className="vault-modal-scroll">
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span className="vault-row__icon" data-kind={kind}>
              <Icon name={KIND_META[kind]?.icon || "document"} size={18} />
            </span>
            <div>
              <h2 id="vault-shared-title" style={{ margin: 0, fontSize: 18 }}>
                {d.title || "(untitled)"}
              </h2>
              <div style={{ fontSize: 12, color: "var(--ink-3)" }}>
                Shared by {ownerEmail} · read-only
              </div>
            </div>
          </div>

          {kind === "password" ? (
            <>
              <DetailRow label="Username" value={d.username} copyable />
              <DetailRow label="Password" value={d.password} secret reveal={reveal} onReveal={toggle} copyable mono />
              <DetailRow label="Website" value={d.url} link copyable />
              <DetailRow label="Notes" value={d.notes} multiline />
            </>
          ) : kind === "seed" ? (
            <SeedView phrase={d.phrase} passphrase={d.passphrase} reveal={reveal} onReveal={toggle} />
          ) : kind === "card" ? (
            <>
              <DetailRow label="Cardholder / name" value={d.cardholder} copyable />
              <DetailRow label="Number" value={d.number} secret reveal={reveal} onReveal={toggle} copyable mono />
              <DetailRow label="Expiry" value={d.expiry} />
              <DetailRow label="CVV / code" value={d.cvv} secret reveal={reveal} onReveal={toggle} copyable mono />
              <DetailRow label="Type / issuer" value={d.brand} />
            </>
          ) : (
            <DetailRow label="" value={d.body} multiline />
          )}
        </div>
      </div>
    </Modal>
  );
}
