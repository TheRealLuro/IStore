// Public vault-link viewer (VLT-8 P7). Standalone, UNAUTHENTICATED page
// mounted by main.tsx at /v/{token}. Everything decrypts on THIS device:
//   - the link key comes from the URL fragment (#…), which the browser never
//     sends to the server, plus the password if the link is protected;
//   - the server only ever returns ciphertext.
// No comments, no account required — just view (and download).

import React, { useEffect, useRef, useState } from "react";

import NeuthekMark from "./NeuthekMark.jsx";
import { Icon } from "./icons.jsx";
import { safeHref } from "./safe-href.js";

import { viewPublicLink, downloadPublicFile } from "@/api/vault";
import {
  b64UrlToBytes,
  b64ToBytes,
  derivePublicLinkKey,
  openPublicLink,
  decryptFile,
} from "@/vault/crypto";

// --- tiny local helpers (standalone page; kept independent of the app) ---
function formatBytes(n) {
  if (n == null || isNaN(n)) return "";
  if (n < 1024) return `${n} B`;
  const u = ["KB", "MB", "GB", "TB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${u[i]}`;
}
function previewKind(mime = "") {
  const m = mime.toLowerCase();
  if (m.startsWith("image/") && m !== "image/svg+xml") return "image";
  if (m.startsWith("video/")) return "video";
  if (m.startsWith("audio/")) return "audio";
  if (m === "application/pdf") return "pdf";
  if ((m.startsWith("text/") && m !== "text/html") || m === "application/json") return "text";
  return "none";
}
function fileIcon(mime = "") {
  const m = mime.toLowerCase();
  if (m.startsWith("image/")) return "image";
  if (m.startsWith("video/")) return "video";
  if (m.startsWith("audio/")) return "music";
  if (m === "application/pdf") return "document";
  return "file";
}
const KIND_LABEL = { password: "Password", note: "Secure note", seed: "Recovery phrase", card: "Card", id: "ID document", ssh_key: "SSH key", contact: "Contact", file: "File" };
const KIND_ICON = { password: "key", note: "document", seed: "shield", card: "contact", id: "contact", ssh_key: "key", contact: "user", file: "file" };

// Mask a (possibly multiline) secret while keeping its shape — non-whitespace
// runs become bullets. Local copy; the public viewer is standalone.
function maskSecret(text) {
  return String(text || "").replace(/\S/g, "•");
}

// Group a card number for display (Amex 4-6-5, else 4-4-4-4…). Digits only.
function formatCardNumber(number) {
  const n = String(number || "").replace(/\D/g, "");
  if (!n) return "";
  if (/^3[47]/.test(n)) {
    return [n.slice(0, 4), n.slice(4, 10), n.slice(10, 15)].filter(Boolean).join(" ");
  }
  return n.match(/.{1,4}/g)?.join(" ") || n;
}

// Best-effort brand detection from the leading digits (hint only).
function detectCardBrand(number) {
  const n = String(number || "").replace(/\D/g, "");
  if (!n) return "";
  if (/^4/.test(n)) return "Visa";
  if (/^5[1-5]/.test(n) || /^2(2[2-9]|[3-6]\d|7[01]|720)/.test(n)) return "Mastercard";
  if (/^3[47]/.test(n)) return "Amex";
  if (/^3(0[0-5]|[68])/.test(n)) return "Diners Club";
  if (/^6(011|5|4[4-9])/.test(n)) return "Discover";
  if (/^35(2[89]|[3-8]\d)/.test(n)) return "JCB";
  if (/^62/.test(n)) return "UnionPay";
  return "";
}

function Shell({ children }) {
  return (
    <div className="vlink">
      <div className="vlink__bar">
        <a className="vlink__brand" href="/">
          <NeuthekMark size={22} /> <span>neuthek</span>
        </a>
        <span className="vlink__tag">
          <Icon name="lock" size={12} /> End-to-end encrypted
        </span>
      </div>
      <div className="vlink__stage">{children}</div>
      <div className="vlink__foot">
        Decrypted on your device. neuthek never sees the contents or the key.
      </div>
    </div>
  );
}

function Centered({ icon = "lock", title, children }) {
  return (
    <div className="vlink__card vlink__card--msg">
      <div className="vlink__msgicon"><Icon name={icon} size={26} strokeWidth={1.4} /></div>
      <h1 className="vlink__title">{title}</h1>
      {children}
    </div>
  );
}

export function VaultLinkView({ token }) {
  const [phase, setPhase] = useState("loading"); // loading|unavailable|broken|password|opening|ready|corrupt
  const [meta, setMeta] = useState(null);
  const [bundle, setBundle] = useState(null);
  const [password, setPassword] = useState("");
  const [pwBusy, setPwBusy] = useState(false);
  const [pwError, setPwError] = useState(false);
  const secretRef = useRef(null);

  // file preview
  const [fileState, setFileState] = useState(null); // null|loading|image|video|audio|pdf|text|nopreview|error
  const [fileUrl, setFileUrl] = useState(null);
  const [fileText, setFileText] = useState("");
  const blobRef = useRef(null);
  const urlRef = useRef(null);

  // 1) read fragment secret + fetch link meta
  useEffect(() => {
    let cancelled = false;
    const hash = (window.location.hash || "").replace(/^#/, "");
    if (!hash) { setPhase("broken"); return; }
    try {
      secretRef.current = b64UrlToBytes(hash);
    } catch {
      setPhase("broken");
      return;
    }
    (async () => {
      try {
        const m = await viewPublicLink(token);
        if (cancelled) return;
        setMeta(m);
        if (m.password_required) setPhase("password");
        else { setPhase("opening"); openBundle(m, null); }
      } catch {
        if (!cancelled) setPhase("unavailable");
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function openBundle(m, pw) {
    try {
      const salt = m.kdf_salt ? b64ToBytes(m.kdf_salt) : null;
      const key = await derivePublicLinkKey(
        secretRef.current, pw, salt, m.kdf_iterations || null,
      );
      const b = await openPublicLink(key, m.sealed_payload);
      setBundle(b);
      setPhase("ready");
      if (b?.kind === "file") loadFile(token, b);
    } catch {
      if (m.password_required) { setPwError(true); setPhase("password"); }
      else setPhase("corrupt");
    } finally {
      setPwBusy(false);
    }
  }

  async function loadFile(tok, b) {
    setFileState("loading");
    try {
      const blob = await downloadPublicFile(tok);
      const ct = new Uint8Array(await blob.arrayBuffer());
      const plain = await decryptFile(ct, b64ToBytes(b.key), b.data.file);
      const out = new Blob([plain], { type: b.data?.mime || "application/octet-stream" });
      blobRef.current = out;
      const kind = previewKind(b.data?.mime);
      if (kind === "text") {
        const t = await out.text();
        setFileText(t.length > 200000 ? t.slice(0, 200000) + "\n…" : t);
        setFileState("text");
      } else if (kind === "none") {
        setFileState("nopreview");
      } else {
        const u = URL.createObjectURL(out);
        urlRef.current = u;
        setFileUrl(u);
        setFileState(kind);
      }
    } catch {
      setFileState("error");
    }
  }

  useEffect(() => () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
  }, []);

  const submitPw = (e) => {
    e.preventDefault();
    if (!password || pwBusy) return;
    setPwBusy(true);
    setPwError(false);
    setPhase("opening");
    openBundle(meta, password);
  };

  const save = () => {
    if (!blobRef.current || !bundle) return;
    const url = URL.createObjectURL(blobRef.current);
    const a = document.createElement("a");
    a.href = url;
    a.download = bundle.data?.title || "file";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  };

  // ---- render ----
  if (phase === "loading" || phase === "opening") {
    return <Shell><Centered title="Opening…">{null}</Centered></Shell>;
  }
  if (phase === "unavailable") {
    return (
      <Shell><Centered icon="alert" title="This link isn’t available">
        <p className="vlink__body">It may have been revoked, expired, or never existed.</p>
      </Centered></Shell>
    );
  }
  if (phase === "broken") {
    return (
      <Shell><Centered icon="alert" title="This link is incomplete">
        <p className="vlink__body">The decryption key is missing from the link. Make sure you copied the full URL, including the part after <code>#</code>.</p>
      </Centered></Shell>
    );
  }
  if (phase === "corrupt") {
    return (
      <Shell><Centered icon="alert" title="Couldn’t open this link">
        <p className="vlink__body">The link or its key looks malformed.</p>
      </Centered></Shell>
    );
  }
  if (phase === "password") {
    return (
      <Shell>
        <form className="vlink__card vlink__card--msg" onSubmit={submitPw}>
          <div className="vlink__msgicon"><Icon name="lock" size={26} strokeWidth={1.4} /></div>
          <h1 className="vlink__title">Password required</h1>
          <p className="vlink__body">This link is protected. Enter the password the sender gave you.</p>
          <input
            className="input"
            type="password"
            autoFocus
            value={password}
            onChange={(e) => { setPassword(e.target.value); if (pwError) setPwError(false); }}
            placeholder="Password"
            style={{ marginTop: 6 }}
          />
          {pwError && <span className="vault-field__err">Wrong password</span>}
          <button className="btn btn--primary btn--lg" disabled={!password || pwBusy} style={{ marginTop: 12 }}>
            {pwBusy ? "Unlocking…" : "Unlock"}
          </button>
        </form>
      </Shell>
    );
  }

  // ready
  const b = bundle || {};
  const isFile = b.kind === "file";
  const d = b.data || {};
  return (
    <Shell>
      <div className="vlink__card">
        <div className="vlink__head">
          <span className="vault-row__icon" data-kind={b.kind}>
            <Icon name={isFile ? fileIcon(d.mime) : KIND_ICON[b.kind] || "document"} size={18} />
          </span>
          <div style={{ minWidth: 0 }}>
            <h1 className="vlink__title" style={{ fontSize: 18, textAlign: "left", margin: 0 }}>
              {d.title || (isFile ? "Shared file" : KIND_LABEL[b.kind] || "Shared item")}
            </h1>
            <div className="vault-viewer__meta">
              {isFile
                ? [formatBytes(meta?.size_bytes), d.mime].filter(Boolean).join(" · ")
                : `${KIND_LABEL[b.kind] || "Item"} · shared via link`}
            </div>
          </div>
        </div>

        {isFile ? (
          <>
            <div className="vault-viewer__stage" style={{ marginTop: 14 }}>
              {fileState === "loading" && <div className="vault-viewer__msg">Decrypting…</div>}
              {fileState === "error" && <div className="vault-viewer__msg">Couldn’t decrypt this file.</div>}
              {fileState === "image" && <img className="vault-viewer__img" src={fileUrl} alt={d.title || ""} />}
              {fileState === "video" && <video className="vault-viewer__media" src={fileUrl} controls />}
              {fileState === "audio" && <audio className="vault-viewer__audio" src={fileUrl} controls />}
              {fileState === "pdf" && <iframe className="vault-viewer__frame" src={fileUrl} title={d.title || "file"} sandbox="" />}
              {fileState === "text" && <pre className="vault-viewer__text">{fileText}</pre>}
              {fileState === "nopreview" && <div className="vault-viewer__msg">No preview for this file type. Download it to open it on your device.</div>}
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 14 }}>
              <button className="btn btn--primary" onClick={save} disabled={!blobRef.current}>
                <Icon name="download" size={13} /> Download
              </button>
            </div>
          </>
        ) : (
          <div style={{ marginTop: 8 }}>
            <SecureFields kind={b.kind} data={d} />
          </div>
        )}
      </div>
    </Shell>
  );
}

// Read-only, reveal-gated fields for a shared secure item.
function SecureFields({ kind, data }) {
  const [reveal, setReveal] = useState(false);
  const d = data || {};
  const toggle = () => setReveal((v) => !v);
  if (kind === "seed") {
    const words = String(d.phrase || "").trim().split(/\s+/).filter(Boolean);
    return (
      <div className="vault-seed">
        <div className="vault-warn vault-warn--tight">
          <Icon name="alert" size={15} />
          <div>Never share these words. Anyone who has them controls the wallet.</div>
        </div>
        <div className="vault-seed__bar">
          <span className="vault-detail-row__label">Recovery phrase · {words.length} words</span>
          <button className="btn-icon" onClick={toggle} aria-label={reveal ? "Hide" : "Reveal"}>
            <Icon name={reveal ? "eyeOff" : "eye"} size={14} />
          </button>
        </div>
        {reveal ? (
          <ol className="vault-seed__grid">
            {words.map((w, i) => (<li key={i} className="vault-seed__word"><span className="vault-seed__num">{i + 1}</span>{w}</li>))}
          </ol>
        ) : (
          <button type="button" className="vault-seed__cover" onClick={toggle}>
            <Icon name="eye" size={15} /> Tap to reveal {words.length} words
          </button>
        )}
        {d.passphrase ? <Row label="Passphrase" value={d.passphrase} secret reveal={reveal} onReveal={toggle} /> : null}
      </div>
    );
  }
  if (kind === "card") {
    const brand = d.brand || detectCardBrand(d.number);
    return (
      <>
        <Row label="Cardholder" value={d.cardholder} />
        <Row
          label="Card number"
          value={reveal ? formatCardNumber(d.number) : d.number}
          secret
          reveal={reveal}
          onReveal={toggle}
          mono
        />
        <Row label="Expiry" value={d.expiry} mono />
        <Row label="CVV" value={d.cvv} secret reveal={reveal} onReveal={toggle} mono />
        <Row label="Brand" value={brand} />
        <Row label="Billing ZIP" value={d.zip} />
        <Row label="Note" value={d.note} multiline />
      </>
    );
  }
  if (kind === "id") {
    return (
      <>
        <Row label="Document type" value={d.docType} />
        <Row label="Full name" value={d.fullName} />
        <Row label="Document number" value={d.number} secret reveal={reveal} onReveal={toggle} mono />
        <Row label="Country / issuer" value={d.issuer} />
        <Row label="Issued" value={d.issued} />
        <Row label="Expires" value={d.expires} />
        <Row label="Date of birth" value={d.dob} />
        <Row label="Note" value={d.note} multiline />
      </>
    );
  }
  if (kind === "ssh_key") {
    return (
      <>
        <Row label="Host / label" value={d.host} />
        <Row label="Key type" value={d.keyType} />
        {d.privateKey ? (
          <div className="vault-detail-row">
            <div className="vault-secretblock__bar">
              <span className="vault-detail-row__label">Private key</span>
              <button className="btn-icon" onClick={toggle} aria-label={reveal ? "Hide" : "Reveal"}>
                <Icon name={reveal ? "eyeOff" : "eye"} size={14} />
              </button>
            </div>
            <pre className="vault-detail-row__pre vault-secretblock__pre" style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)" }}>
              {reveal ? d.privateKey : maskSecret(d.privateKey)}
            </pre>
          </div>
        ) : null}
        {d.publicKey ? (
          <div className="vault-detail-row">
            <span className="vault-detail-row__label">Public key</span>
            <pre className="vault-detail-row__pre vault-secretblock__pre" style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)" }}>
              {d.publicKey}
            </pre>
          </div>
        ) : null}
        <Row label="Passphrase" value={d.passphrase} secret reveal={reveal} onReveal={toggle} mono />
      </>
    );
  }
  if (kind === "password") {
    return (
      <>
        <Row label="Username" value={d.username} />
        <Row label="Password" value={d.password} secret reveal={reveal} onReveal={toggle} mono />
        <Row label="Website" value={d.url} link />
        <Row label="Notes" value={d.notes} multiline />
      </>
    );
  }
  if (kind === "contact") {
    const lbl = (base, extra) => [base, extra].filter(Boolean).join(" · ");
    const list = (arr) => (Array.isArray(arr) ? arr : []);
    return (
      <>
        {(d.org || d.role) && <Row label="Organization" value={lbl(d.org, d.role)} />}
        {list(d.phones).map((p, i) => (
          <Row key={`ph${i}`} label={lbl("Phone", p?.label)} value={p?.value} />
        ))}
        {list(d.emails).map((e, i) => (
          <Row key={`em${i}`} label={lbl("Email", e?.label)} value={e?.value} />
        ))}
        {list(d.addresses).map((a, i) => (
          <Row key={`ad${i}`} label={lbl("Address", a?.label)} value={a?.value} multiline />
        ))}
        {list(d.urls).map((u, i) => {
          // The local Row renders a link target as its value via href=value, so
          // pass the SANITIZED href. Unsafe schemes (javascript:/data:) → null
          // → render the raw value as inert text (a public link could carry a
          // hostile URL).
          const href = safeHref(u?.value);
          return (
            <Row key={`ur${i}`} label={lbl("Website", u?.label)} value={href || u?.value} link={!!href} />
          );
        })}
        {d.birthday && <Row label="Birthday" value={d.birthday} />}
        {d.note && <Row label="Note" value={d.note} multiline />}
      </>
    );
  }
  // note
  return <Row label="" value={d.body} multiline />;
}

function Row({ label, value, secret, reveal, onReveal, link, multiline, mono }) {
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
          <a href={value} target="_blank" rel="noopener noreferrer">{value}</a>
        ) : (
          <span className={mono ? "vault-mono" : undefined}>{value}</span>
        )}
        <div className="vault-detail-row__actions">
          {secret && (
            <button className="btn-icon" onClick={onReveal} aria-label={reveal ? "Hide" : "Reveal"}>
              <Icon name={reveal ? "eyeOff" : "eye"} size={14} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
