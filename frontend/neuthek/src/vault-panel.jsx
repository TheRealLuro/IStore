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
} from "@/api/vault";
import {
  createVaultSetup,
  deriveVaultKey,
  checkVerifier,
  encryptItem,
  decryptItem,
  b64ToBytes,
  createAccountKeyPair,
  unwrapAccountPrivateKey,
  randomFileKey,
  encryptFile,
  decryptFile,
  sealToPublicKey,
  unsealFromPrivateKey,
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

// Download a vault file: fetch ciphertext, unseal the per-file key with the
// account private key, decrypt chunk-by-chunk, and save to disk. The bytes
// are only ever plaintext in memory on this device. `view` is a decrypted
// row: { id, wrapped_key, data: { title, mime, file } }.
async function downloadDecryptedFile(view) {
  const priv = getAccountPrivateKey();
  if (!priv) {
    toast.error("Lock and unlock the vault, then try again.");
    return;
  }
  if (!view.wrapped_key || !view.data?.file) {
    toast.error("This file is missing its key material.");
    return;
  }
  const t = toast.loading(`Decrypting ${view.data.title || "file"}…`);
  try {
    const fileKey = await unsealFromPrivateKey(priv, view.wrapped_key);
    const blob = await downloadVaultFile(view.id);
    const ct = new Uint8Array(await blob.arrayBuffer());
    const plain = await decryptFile(ct, fileKey, view.data.file);
    const out = new Blob([plain], {
      type: view.data.mime || "application/octet-stream",
    });
    const url = URL.createObjectURL(out);
    const a = document.createElement("a");
    a.href = url;
    a.download = view.data.title || "file";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
    toast.success("Downloaded", { id: t });
  } catch {
    toast.error("Couldn’t decrypt this file.", { id: t });
  }
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
  const [currentFolder, setCurrentFolder] = useState(null); // null = vault root
  const [search, setSearch] = useState("");
  const [items, setItems] = useState(null); // decrypted item views, null = loading
  const [folders, setFolders] = useState(null); // decrypted folder views
  const [decryptErr, setDecryptErr] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [detail, setDetail] = useState(null);
  const [uploading, setUploading] = useState(0); // in-flight upload count
  const fileInputRef = useRef(null);

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
                    if (isFile) downloadDecryptedFile(item);
                    else setDetail(item);
                  }}
                >
                  <span className="vault-row__icon" data-kind={item.kind}>
                    <Icon
                      name={
                        isFile
                          ? fileIconName(item.data?.title, item.data?.mime)
                          : item.kind === "password"
                            ? "key"
                            : "document"
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
                          : "Secure note"}
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
        onSaved={() => {
          setDetail(null);
          refresh();
        }}
      />
    </div>
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
// AddItemModal — create a password or note
// ---------------------------------------------------------------------------

function AddItemModal({ open, folderId = null, onClose, onSaved }) {
  const [kind, setKind] = useState("password");
  const [busy, setBusy] = useState(false);
  // password fields
  const [title, setTitle] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [url, setUrl] = useState("");
  const [pnotes, setPnotes] = useState("");
  const [showPw, setShowPw] = useState(false);
  // note fields
  const [body, setBody] = useState("");

  const reset = () => {
    setKind("password");
    setTitle("");
    setUsername("");
    setPassword("");
    setUrl("");
    setPnotes("");
    setBody("");
    setShowPw(false);
  };

  const close = () => {
    reset();
    onClose?.();
  };

  const canSave =
    !busy &&
    title.trim() &&
    (kind === "password" ? password : true) &&
    (kind === "note" ? body.trim() : true);

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
      const plaintext =
        kind === "password"
          ? {
              title: title.trim(),
              username,
              password,
              url,
              notes: pnotes,
            }
          : { title: title.trim(), body };
      const sealed = await encryptItem(key, plaintext);
      await createVaultItem({
        kind,
        nonce: sealed.nonce,
        ciphertext: sealed.ciphertext,
        folder_id: folderId,
      });
      toast.success(kind === "password" ? "Password saved" : "Note saved");
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

        <div className="vault-segments" style={{ alignSelf: "flex-start" }}>
          <button
            type="button"
            className="vault-segment"
            data-active={kind === "password"}
            onClick={() => setKind("password")}
          >
            <Icon name="key" size={13} /> Password
          </button>
          <button
            type="button"
            className="vault-segment"
            data-active={kind === "note"}
            onClick={() => setKind("note")}
          >
            <Icon name="document" size={13} /> Note
          </button>
        </div>

        <label className="vault-field">
          <span>Title</span>
          <input
            className="input"
            value={title}
            autoFocus
            onChange={(e) => setTitle(e.target.value)}
            placeholder={kind === "password" ? "e.g. GitHub" : "e.g. Recovery codes"}
          />
        </label>

        {kind === "password" ? (
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
        ) : (
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

function ItemDetailModal({ item, onClose, onDelete, onSaved }) {
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
  const isPw = item.kind === "password";

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
            <Icon name={isPw ? "key" : "document"} size={18} />
          </span>
          <div>
            <h2 id="vault-detail-title" style={{ margin: 0, fontSize: 18 }}>
              {d.title || "(untitled)"}
            </h2>
            <div style={{ fontSize: 12, color: "var(--ink-3)" }}>
              Updated {relTime(item.updated_at)}
            </div>
          </div>
        </div>

        {item.corrupt ? (
          <div className="vault-field__err">
            This item couldn’t be decrypted with the current key.
          </div>
        ) : editing ? (
          <EditFields kind={item.kind} draft={draft} setDraft={setDraft} />
        ) : isPw ? (
          <>
            <DetailRow label="Username" value={d.username} copyable />
            <DetailRow
              label="Password"
              value={d.password}
              secret
              reveal={reveal}
              onReveal={() => setReveal((v) => !v)}
              copyable
            />
            <DetailRow label="Website" value={d.url} link copyable />
            <DetailRow label="Notes" value={d.notes} multiline />
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
                <button className="btn btn--secondary" onClick={() => setEditing(true)}>
                  <Icon name="edit" size={13} /> Edit
                </button>
              ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}

function EditFields({ kind, draft, setDraft }) {
  const set = (k) => (e) => setDraft((d) => ({ ...d, [k]: e.target.value }));
  if (kind === "note") {
    return (
      <>
        <label className="vault-field">
          <span>Title</span>
          <input className="input" value={draft?.title || ""} onChange={set("title")} />
        </label>
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
  return (
    <>
      <label className="vault-field">
        <span>Title</span>
        <input className="input" value={draft?.title || ""} onChange={set("title")} />
      </label>
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

function DetailRow({ label, value, secret, reveal, onReveal, copyable, link, multiline }) {
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
          <span>{value}</span>
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
