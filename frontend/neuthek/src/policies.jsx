// All seven policy popups, sharing the same shell shape (modal + scrolling
// prose + sticky footer with action buttons).
import React, { useState as useStateP, useEffect as useEffectP } from "react";
import { Icon } from "./icons.jsx";
import {
  Modal as ModalP,
  ModalClose as ModalCloseP,
  Switch as SwitchP,
  Check as CheckP,
} from "./primitives.jsx";

/* ============================================================
   1. Terms of Use — acceptance at signup
   ============================================================ */
export function TermsModal({ open, onClose, onAccept, mode = "accept" }) {
  const [accepted, setAccepted] = useStateP(false);
  useEffectP(() => { if (open) setAccepted(false); }, [open]);

  return (
    <ModalP open={open} onClose={onClose} size="lg" labelledBy="terms-title">
      <div className="modal__head">
        <h2 id="terms-title">
          <span className="modal__head-icon"><Icon name="document" size={16}/></span>
          Terms of Use
        </h2>
        <p>Effective May 1, 2026 · Version 4.2 · Please read before continuing.</p>
        {mode === "view" && <ModalCloseP onClose={onClose}/>}
      </div>
      <div className="modal__body modal__body--prose">
        <h3>1. Your account</h3>
        <p>You are responsible for the activity that happens on your neuthek account. Keep your sign-in credentials private and notify us promptly if you believe your account has been accessed without your permission.</p>
        <h3>2. Your content</h3>
        <p>The files you upload remain yours. You grant neuthek a limited license to store, transmit, decode, transcode, generate previews of, and (where you have enabled it) index your content for the sole purpose of operating the service for you.</p>
        <p>You confirm that you own the rights to upload the content, or have permission from the rights holders, and that the content does not break the law in your jurisdiction.</p>
        <h3>3. AI features</h3>
        <p>If you enable optional AI features (semantic search, summaries, face grouping), excerpts and embeddings derived from your content are processed by our AI providers. We do <strong>not</strong> use your content to train third-party models. You can turn AI features off at any time in <strong>Settings → Privacy</strong>; previously generated indexes are deleted within 30 days.</p>
        <h3>4. Acceptable use</h3>
        <ul>
          <li>No unlawful content, including content that exploits minors.</li>
          <li>No malware, phishing pages, or content used to attack other systems.</li>
          <li>No mass-scraping or attempts to circumvent storage quotas.</li>
        </ul>
        <h3>5. Storage and retention</h3>
        <p>Plans include a storage allowance. If you exceed your allowance for an extended period and do not upgrade, we may notify you and, after a grace period, archive cold files. You can always export your data with one click — see Section 8.</p>
        <h3>6. Termination</h3>
        <p>You can delete your account at any time from Account & privacy. We may suspend an account that violates these terms; we will give you a chance to export first unless legally prohibited.</p>
        <h3>7. Changes to these terms</h3>
        <p>If we make a material change, we'll notify you in-app at least 30 days before it takes effect. Continued use after that constitutes acceptance.</p>
        <h3>8. Your rights</h3>
        <p>You can <strong>access</strong>, <strong>correct</strong>, <strong>export</strong>, or <strong>delete</strong> your data using the controls in Account & privacy. For requests we can't fulfil in-app, write to <code>privacy@neuthek.app</code>.</p>
        <h3>9. Disclaimers and liability</h3>
        <p>The service is provided "as is" to the maximum extent permitted by law. Our aggregate liability for any claim is limited to the amount you paid us in the prior 12 months.</p>
        <h3>10. Governing law</h3>
        <p>These terms are governed by the laws of your country of residence where required, otherwise by the laws of the State of California.</p>
      </div>
      {mode === "accept" ? (
        <>
          <div style={{ padding: "0 26px" }}>
            <CheckP checked={accepted} onChange={setAccepted}
              label="I have read and agree to the Terms of Use and the Privacy Notice."
              sub="You must accept to create an account."/>
          </div>
          <div className="modal__foot">
            <span className="modal__foot-left mono">v4.2 · 30 day notice on changes</span>
            <div className="modal__foot-actions">
              <button className="btn btn--secondary" onClick={onClose}>Cancel</button>
              <button className="btn btn--primary" disabled={!accepted}
                      onClick={() => { onAccept && onAccept(); onClose && onClose(); }}>Accept & continue</button>
            </div>
          </div>
        </>
      ) : (
        <div className="modal__foot">
          <span className="modal__foot-left mono">v4.2</span>
          <div className="modal__foot-actions">
            <button className="btn btn--secondary"><Icon name="download" size={14}/> Save as PDF</button>
            <button className="btn btn--primary" onClick={onClose}>Done</button>
          </div>
        </div>
      )}
    </ModalP>
  );
}

/* ============================================================
   2. Privacy Notice — read-only viewer
   ============================================================ */
export function PrivacyModal({ open, onClose }) {
  return (
    <ModalP open={open} onClose={onClose} size="lg" labelledBy="priv-title">
      <div className="modal__head">
        <h2 id="priv-title">
          <span className="modal__head-icon"><Icon name="shield" size={16}/></span>
          Privacy Notice
        </h2>
        <p>What we collect, why we collect it, and how to control it. Last updated May 1, 2026.</p>
        <ModalCloseP onClose={onClose}/>
      </div>
      <div className="modal__body modal__body--prose">
        <h3>What we collect</h3>
        <ul>
          <li><strong>Files you upload</strong> — stored encrypted at rest. Only you and people you share with can read them.</li>
          <li><strong>Account info</strong> — email, hashed password, sign-in history.</li>
          <li><strong>Device & usage</strong> — IP address, app version, crash reports. Used to keep the service running and secure.</li>
          <li><strong>Optional AI inputs</strong> — when you enable semantic search or summaries, we derive embeddings and short text excerpts from your files. <strong>You can opt out</strong> per-feature.</li>
          <li><strong>Optional location</strong> — only if you allow GPS tagging on upload. Stripped on export by default.</li>
          <li><strong>Optional face data</strong> — only if you sign the Face Recognition Consent. Stored as math-only templates, never as a database of faces.</li>
        </ul>
        <h3>Why we collect it</h3>
        <p>Strictly to operate the service: store your files, secure your account, prevent abuse, generate the AI features you enabled, and meet legal obligations.</p>
        <h3>What we don't do</h3>
        <ul>
          <li>We do not sell your data.</li>
          <li>We do not use your content to train third-party AI models.</li>
          <li>We do not share your face templates with anyone, ever.</li>
        </ul>
        <h3>Who has access</h3>
        <p>Engineers can access systems but cannot read your files without break-glass approval, which is logged and reviewed. Subprocessors (cloud hosting, AI inference) are listed at <code>neuthek.app/subprocessors</code> and bound by data-protection agreements.</p>
        <h3>Retention</h3>
        <p>Files: as long as your account is active. Deleted files: 30 days in trash. Backups: rolling 90 days. AI indexes: deleted within 30 days of disabling the feature.</p>
        <h3>Your rights</h3>
        <p>You can access, correct, export, and delete your data from Account & privacy. To make a complaint, contact <code>privacy@neuthek.app</code> or your local data-protection authority.</p>
        <h3>Contact</h3>
        <p>Data Protection Officer: <code>dpo@neuthek.app</code> · Postal: 1 neuthek Way, Wilmington DE 19801, USA.</p>
      </div>
      <div className="modal__foot">
        <span className="modal__foot-left mono">neuthek.app/privacy · v3.1</span>
        <div className="modal__foot-actions">
          <button className="btn btn--secondary"><Icon name="download" size={14}/> Save as PDF</button>
          <button className="btn btn--primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </ModalP>
  );
}

/* ============================================================
   3. Face Recognition Consent — BIPA-style signed
   ============================================================ */
export function FaceConsentModal({ open, onClose, onSign }) {
  const [readScroll, setReadScroll] = useStateP(false);
  const [agree1, setAgree1] = useStateP(false);
  const [agree2, setAgree2] = useStateP(false);
  const [agree3, setAgree3] = useStateP(false);
  const [signature, setSignature] = useStateP("");
  useEffectP(() => {
    if (!open) return;
    setReadScroll(false); setAgree1(false); setAgree2(false); setAgree3(false); setSignature("");
  }, [open]);

  const handleScroll = (e) => {
    const el = e.target;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 24) setReadScroll(true);
  };

  const allAgreed = agree1 && agree2 && agree3 && signature.trim().length >= 3 && readScroll;

  return (
    <ModalP open={open} onClose={onClose} size="lg" labelledBy="face-title">
      <div className="modal__head">
        <h2 id="face-title">
          <span className="modal__head-icon"><Icon name="users" size={16}/></span>
          Face Recognition Consent
        </h2>
        <p>This is a separate, written consent required under biometric-information laws (BIPA, GDPR Art. 9, others). It is not part of the Terms of Use.</p>
        <ModalCloseP onClose={onClose}/>
      </div>
      <div className="modal__body modal__body--prose" onScroll={handleScroll}>
        <h3>What you're consenting to</h3>
        <p>If you enable People & Faces, neuthek will analyze the images you upload to detect faces and generate a <strong>mathematical template</strong> for each person it finds. Templates are vectors of numbers — they cannot be reversed back into a photo.</p>
        <h3>How the data is used</h3>
        <ul>
          <li>To group photos of the same person in your library.</li>
          <li>To let you tag and search by person in your own library.</li>
          <li><strong>Nothing else.</strong> Templates are not used for advertising, identity verification, or shared with anyone.</li>
        </ul>
        <h3>Where it is stored</h3>
        <p>In your private library, encrypted at rest, in the same region as your files. Templates are never combined into a global database.</p>
        <h3>How long it is kept</h3>
        <p>While your account is active and this consent is on. If you turn off People & Faces, all templates are deleted within <strong>30 days</strong>. If you delete your account, templates are deleted with it.</p>
        <h3>Your right to refuse</h3>
        <p>You do <strong>not</strong> need to consent to use neuthek. All other features work without it. You can withdraw consent at any time in Privacy settings; we will purge templates within 30 days.</p>
        <h3>Subprocessors</h3>
        <p>Face detection runs on our servers. We do not share biometric templates with any third party. Inference uses an internal model fine-tuned in-house.</p>
        <h3>Disputes</h3>
        <p>To dispute the processing of your biometric data, write to <code>privacy@neuthek.app</code>. You may also have the right to lodge a complaint with a regulator, or in the U.S. to bring a private right of action under applicable state law (e.g. BIPA in Illinois).</p>
        <h3>Acknowledgement of receipt</h3>
        <p>By signing below, you confirm you have received this written notice in a language you understand, that you have had the opportunity to ask questions, and that you are giving consent freely.</p>
      </div>
      <div style={{ padding: "8px 26px 6px" }}>
        {!readScroll && (
          <div style={{ fontSize: 12, color: "var(--ink-3)", padding: "6px 0 10px", display: "flex", alignItems: "center", gap: 8 }}>
            <Icon name="info" size={13}/> Scroll to the end to enable signing.
          </div>
        )}
        <CheckP checked={agree1} onChange={setAgree1}
          label="I consent to the collection and storage of biometric templates derived from photos I upload."/>
        <CheckP checked={agree2} onChange={setAgree2}
          label="I understand templates will be deleted within 30 days if I withdraw consent."/>
        <CheckP checked={agree3} onChange={setAgree3}
          label="I confirm I am the person opening this account, or have authority to consent on its behalf."/>
        <div className="label-block" style={{ padding: "6px 12px 0" }}>
          <div className="label-block__label">Type your full name to sign</div>
          <input className="input" placeholder="Full legal name" value={signature}
                 onChange={(e) => setSignature(e.target.value)} disabled={!readScroll}/>
        </div>
      </div>
      <div className="modal__foot">
        <span className="modal__foot-left mono">Signed: {signature ? signature : "—"} · {new Date().toLocaleDateString()}</span>
        <div className="modal__foot-actions">
          <button className="btn btn--secondary" onClick={onClose}>Decline</button>
          <button className="btn btn--primary" disabled={!allAgreed}
                  onClick={() => { onSign && onSign(signature); onClose && onClose(); }}>Sign & enable</button>
        </div>
      </div>
    </ModalP>
  );
}

/* ============================================================
   4. Per-scope privacy controls
   ============================================================ */
export function PrivacyScopesModal({ open, onClose }) {
  const [scopes, setScopes] = useStateP({
    gps: true,
    aiSummary: true,
    semanticSearch: false,
    telemetry: true,
  });
  const setOne = (k, v) => setScopes(s => ({ ...s, [k]: v }));

  const items = [
    { id: "gps", icon: "map_pin", title: "GPS & Location tagging",
      desc: "Read EXIF location from photos, and let me filter by where photos were taken.",
      detail: "Strips location on export when off." },
    { id: "aiSummary", icon: "sparkles", title: "AI summary",
      desc: "Generate a one-sentence topic for each photo and document.",
      detail: "Excerpts processed in our region; never used for training." },
    { id: "semanticSearch", icon: "search", title: "Semantic search",
      desc: "Search by meaning — \"sunsets at the lake\" finds photos that match.",
      detail: "Builds embeddings from filenames + summaries. No content leaves your account." },
    { id: "telemetry", icon: "info", title: "Anonymous usage telemetry",
      desc: "Help us prioritise features by sharing crash reports and feature usage.",
      detail: "No file contents, no filenames, no identifiers tied to you." },
  ];

  return (
    <ModalP open={open} onClose={onClose} size="lg" labelledBy="scopes-title">
      <div className="modal__head">
        <h2 id="scopes-title">
          <span className="modal__head-icon"><Icon name="lock" size={16}/></span>
          Privacy controls
        </h2>
        <p>Each scope is independent. Turning a feature off deletes derived data within 30 days.</p>
        <ModalCloseP onClose={onClose}/>
      </div>
      <div className="modal__body">
        {items.map(it => (
          <div className="scope-row" key={it.id}>
            <div className="scope-row__icon"><Icon name={it.icon} size={15}/></div>
            <div className="scope-row__body">
              <div className="scope-row__title">
                <span dangerouslySetInnerHTML={{ __html: it.title }}/>
                <span className="scope-row__chip" data-state={scopes[it.id] ? "on" : "off"}>
                  {scopes[it.id] ? "On" : "Off"}
                </span>
              </div>
              <div className="scope-row__desc">{it.desc}</div>
              <div className="scope-row__desc" style={{ color: "var(--ink-3)", marginTop: 4 }}>{it.detail}</div>
            </div>
            <SwitchP on={scopes[it.id]} onChange={(v) => setOne(it.id, v)} ariaLabel={it.title}/>
          </div>
        ))}
      </div>
      <div className="modal__foot">
        <span className="modal__foot-left">Changes save automatically.</span>
        <div className="modal__foot-actions">
          <button className="btn btn--primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </ModalP>
  );
}

/* ============================================================
   5. Cookie / first-visit notice (corner card, not banner)
   ============================================================ */
// §A6 — Storage notice (not a cookie banner). neuthek doesn't set
// cookies at all (the CI test `test_backend_does_not_set_cookies`
// keeps that property honest). The frontend uses `localStorage`
// for theme, recent searches, and the auth JWT. We still surface
// this notice because users often expect a "storage notice" of some
// kind — but the language reflects what we actually do, and there's
// no Accept/Decline because nothing toggles based on the answer.
export function CookieBanner({ open, onAcceptAll, onEssentialOnly, onCustomize }) {
  if (!open) return null;
  return (
    <div className="cookie" role="dialog" aria-labelledby="cookie-title">
      <div className="cookie__icon"><Icon name="cookie" size={18}/></div>
      <div className="cookie__body">
        <h3 id="cookie-title" style={{ margin: "0 0 4px", fontSize: 14, fontWeight: 600 }}>How we store data in your browser</h3>
        <p>
          neuthek <strong>does not set any cookies</strong>. Your
          sign-in token, theme choice, and recent searches live in
          your browser's <strong>localStorage</strong> — same scope as
          cookies but never sent on cross-site requests. No tracking,
          no third-party analytics. <a href="#" onClick={(e) => { e.preventDefault(); onCustomize && onCustomize(); }}>Read the privacy notice</a>.
        </p>
        <div className="cookie__actions">
          <button className="btn btn--secondary btn--sm" onClick={onEssentialOnly}>Got it</button>
          <button className="btn btn--primary btn--sm" onClick={onAcceptAll}>OK, dismiss</button>
        </div>
      </div>
    </div>
  );
}

/* ============================================================
   6. Account deletion confirmation (heavy)
   ============================================================ */
export function DeleteAccountModal({ open, onClose, onConfirm, email = "you@example.com" }) {
  const [stage, setStage] = useStateP(0); // 0 = warning, 1 = confirm
  const [text, setText] = useStateP("");
  const [pwd, setPwd] = useStateP("");
  useEffectP(() => { if (open) { setStage(0); setText(""); setPwd(""); } }, [open]);

  return (
    <ModalP open={open} onClose={onClose} labelledBy="del-title">
      <div className="modal__head">
        <h2 id="del-title">
          <span className="modal__head-icon modal__head-icon--danger"><Icon name="trash" size={16}/></span>
          Delete your account
        </h2>
        <p>This is permanent. Take a minute — there's no undo.</p>
        <ModalCloseP onClose={onClose}/>
      </div>
      {stage === 0 ? (
        <>
          <div className="modal__body modal__body--prose">
            <h3>What gets deleted</h3>
            <ul>
              <li>All your files (photos, videos, documents) — including those in shared folders you own.</li>
              <li>Your face templates and AI indexes.</li>
              <li>Your account, sign-in history, and preferences.</li>
            </ul>
            <h3>What stays for a short window</h3>
            <p>Backups roll off automatically within <strong>90 days</strong>. We can't restore individual files from backups.</p>
            <h3>Before you go</h3>
            <p>You may want to <strong>export your data</strong> first — it's free and runs in the background. We'll email you when it's ready.</p>
          </div>
          <div className="modal__foot">
            <span className="modal__foot-left mono">{email}</span>
            <div className="modal__foot-actions">
              <button className="btn btn--secondary" onClick={onClose}>Keep my account</button>
              <button className="btn btn--ghost"><Icon name="download" size={14}/> Export first</button>
              <button className="btn btn--danger" onClick={() => setStage(1)}>Continue to delete</button>
            </div>
          </div>
        </>
      ) : (
        <>
          <div className="modal__body">
            <div style={{ padding: "8px 0 0" }}>
              <div className="label-block">
                <div className="label-block__label">Type DELETE to confirm</div>
                <input className="input" value={text} onChange={e => setText(e.target.value.toUpperCase())} placeholder="DELETE"/>
              </div>
              <div className="label-block">
                <div className="label-block__label">Re-enter your password</div>
                <input
                  type="password"
                  className="input"
                  value={pwd}
                  onChange={e => setPwd(e.target.value)}
                  placeholder="••••••••"
                  autoComplete="current-password"
                />
              </div>
              <div style={{
                marginTop: 16, padding: "12px 14px",
                background: "var(--danger-soft)", color: "var(--danger)",
                borderRadius: 12, fontSize: 12.5, lineHeight: 1.5
              }}>
                <strong>Last call.</strong> Once you click delete, your files are queued for permanent removal and your sign-in stops working immediately.
              </div>
            </div>
          </div>
          <div className="modal__foot">
            <span className="modal__foot-left mono">{email}</span>
            <div className="modal__foot-actions">
              <button className="btn btn--secondary" onClick={() => setStage(0)}>Back</button>
              <button className="btn btn--danger-solid"
                      disabled={text !== "DELETE" || pwd.length < 4}
                      onClick={() => { onConfirm && onConfirm(); onClose && onClose(); }}>
                Delete my account
              </button>
            </div>
          </div>
        </>
      )}
    </ModalP>
  );
}

/* ============================================================
   7. Data export confirmation
   ============================================================ */
export function ExportModal({ open, onClose, onStart }) {
  const [scopes, setScopes] = useStateP({
    files: true, metadata: true, faces: false, account: true,
  });
  const [format, setFormat] = useStateP("zip");
  const setOne = (k, v) => setScopes(s => ({ ...s, [k]: v }));

  return (
    <ModalP open={open} onClose={onClose} labelledBy="exp-title">
      <div className="modal__head">
        <h2 id="exp-title">
          <span className="modal__head-icon"><Icon name="download" size={16}/></span>
          Export your data
        </h2>
        <p>We'll prepare a download in the background. You'll get an email when it's ready — usually under 30 minutes.</p>
        <ModalCloseP onClose={onClose}/>
      </div>
      <div className="modal__body">
        <div className="acc-section">
          <div className="acc-section__label">Include</div>
          <div>
            <CheckP checked={scopes.files} onChange={(v) => setOne("files", v)}
              label="Files" sub="Photos, videos, documents — original quality."/>
            <CheckP checked={scopes.metadata} onChange={(v) => setOne("metadata", v)}
              label="Metadata" sub="Filenames, dates, tags, AI summaries (JSON sidecar)."/>
            <CheckP checked={scopes.faces} onChange={(v) => setOne("faces", v)}
              label="Face groupings" sub="Person labels and member lists. Templates are never exported (they're derived data)."/>
            <CheckP checked={scopes.account} onChange={(v) => setOne("account", v)}
              label="Account" sub="Sign-in history, preferences, consent records."/>
          </div>
        </div>
        <div className="acc-section">
          <div className="acc-section__label">Format</div>
          <div className="tabs" style={{ width: "100%" }}>
            <button className="tab" data-active={format === "zip"} style={{ flex: 1 }} onClick={() => setFormat("zip")}>Single .zip</button>
            <button className="tab" data-active={format === "split"} style={{ flex: 1 }} onClick={() => setFormat("split")}>Split into 2&nbsp;GB parts</button>
            <button className="tab" data-active={format === "tar"} style={{ flex: 1 }} onClick={() => setFormat("tar")}>.tar (Linux)</button>
          </div>
        </div>
        <div style={{
          marginTop: 18, padding: "12px 14px",
          background: "var(--surface-2)", borderRadius: 12,
          fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.5
        }}>
          Estimated size: <strong className="mono">~14.2 GB</strong> · Download link expires after 7 days.
        </div>
      </div>
      <div className="modal__foot">
        <span className="modal__foot-left">Sent to <span className="mono">you@example.com</span></span>
        <div className="modal__foot-actions">
          <button className="btn btn--secondary" onClick={onClose}>Cancel</button>
          <button className="btn btn--primary" onClick={() => { onStart && onStart(); onClose && onClose(); }}>Start export</button>
        </div>
      </div>
    </ModalP>
  );
}

// Named exports above; legacy `window.Policies` access removed.
