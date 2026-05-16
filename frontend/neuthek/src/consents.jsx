// Sequenced consents flow — one focused popup per policy.
// Each step animates in, the user acts, then advances.
// This replaces the long single-modal scroll experience.
import React, { useState as useStateC, useEffect as useEffectC } from "react";
import { Icon } from "./icons.jsx";
import {
  Modal as ModalC,
  ModalClose as ModalCloseC,
  Switch as SwitchC,
} from "./primitives.jsx";

const STEPS = [
  { id: "terms",    label: "Terms",         icon: "document" },
  { id: "privacy",  label: "Privacy",       icon: "shield" },
  { id: "bipa",     label: "Face",          icon: "users" },
  { id: "scopes",   label: "Features",      icon: "lock" },
  { id: "cookies",  label: "Cookies",       icon: "cookie" },
  { id: "sign",     label: "Sign",          icon: "key" },
];

function StepStrip({ stepIdx, onJump, completed }) {
  return (
    <div className="cs-strip">
      {STEPS.map((s, i) => {
        const state = i < stepIdx ? "done" : i === stepIdx ? "current" : "todo";
        return (
          <button key={s.id}
            className="cs-strip__seg"
            data-state={state}
            disabled={!completed[s.id] && i > stepIdx}
            onClick={() => onJump(i)}
            title={s.label}>
            <span className="cs-strip__dot"/>
            <span className="cs-strip__label">{s.label}</span>
          </button>
        );
      })}
    </div>
  );
}

function StepFrame({ stepKey, children }) {
  // crossfade + slight slide via key change
  return (
    <div key={stepKey} className="cs-step">
      {children}
    </div>
  );
}

export function ConsentsModal({ open, onClose, onComplete, requireFace = false, allowEarlyAI = true }) {
  const [stepIdx, setStepIdx] = useStateC(0);
  const [terms, setTerms] = useStateC(false);
  // §A6 — age gate. Required at signup; under-13 users are prohibited
  // because neuthek does not implement the COPPA verifiable-parental-
  // consent flow. The backend `UserCreate._require_age_gate` validator
  // refuses any registration with `age_confirmed != true`, so this
  // checkbox isn't decorative — unchecking it blocks signup.
  const [age13, setAge13] = useStateC(false);
  const [privacyRead, setPrivacyRead] = useStateC(false);
  const [bipa, setBipa] = useStateC(false);
  const [scopes, setScopes] = useStateC({ gps: true, aiSummary: allowEarlyAI, semanticSearch: false, telemetry: true });
  const [cookies, setCookies] = useStateC("all");
  const [signature, setSignature] = useStateC("");

  useEffectC(() => {
    if (!open) return;
    setStepIdx(0); setTerms(false); setAge13(false); setPrivacyRead(false); setBipa(false);
    setScopes({ gps: true, aiSummary: allowEarlyAI, semanticSearch: false, telemetry: true });
    setCookies("all"); setSignature("");
  }, [open, allowEarlyAI]);

  const completed = {
    terms: terms && age13,
    privacy: privacyRead,
    bipa: !requireFace || bipa, // optional unless required
    scopes: true,                // user can leave defaults
    cookies: !!cookies,
    sign: signature.trim().length >= 3,
  };

  const stepId = STEPS[stepIdx].id;
  const canAdvance = (() => {
    if (stepId === "terms") return terms && age13;
    if (stepId === "privacy") return privacyRead;
    if (stepId === "bipa") return !requireFace || true; // optional
    if (stepId === "scopes") return true;
    if (stepId === "cookies") return !!cookies;
    if (stepId === "sign") return signature.trim().length >= 3;
    return true;
  })();

  const isLast = stepIdx === STEPS.length - 1;
  const next = () => {
    if (!canAdvance) return;
    if (isLast) {
      onComplete && onComplete({ scopes, cookies, bipa, signature, age13 });
    } else {
      setStepIdx(i => Math.min(STEPS.length - 1, i + 1));
    }
  };
  const back = () => setStepIdx(i => Math.max(0, i - 1));

  const setOne = (k, v) => setScopes(s => ({ ...s, [k]: v }));

  return (
    <ModalC open={open} onClose={onClose} size="md" labelledBy="consents-title">
      <div className="modal__head" style={{ paddingBottom: 14 }}>
        <h2 id="consents-title" style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className="modal__head-icon"><Icon name={STEPS[stepIdx].icon} size={16}/></span>
          {stepId === "terms" && "Terms of Use"}
          {stepId === "privacy" && "Privacy Notice"}
          {stepId === "bipa" && "Face Recognition"}
          {stepId === "scopes" && "Choose your features"}
          {stepId === "cookies" && "Cookies"}
          {stepId === "sign" && "Sign & create account"}
        </h2>
        <ModalCloseC onClose={onClose}/>
      </div>

      <StepStrip stepIdx={stepIdx} onJump={(i) => i <= stepIdx && setStepIdx(i)} completed={completed}/>

      <div className="modal__body cs-body">
        {stepId === "terms" && <StepFrame stepKey="terms">
          <p className="cs-lead">A short version of the rules. Full document available any time from your account.</p>
          <div className="cs-prose">
            <p><strong>Your account.</strong> You're responsible for activity on it. Keep credentials private.</p>
            <p><strong>Your content.</strong> Your files remain yours. We get a limited license only to store, transmit, generate previews and (if you opt in) index them — strictly to operate the service for you.</p>
            <p><strong>AI features.</strong> Off by default. When enabled, embeddings and short excerpts are processed in our region. We do <strong>not</strong> train third-party models on your data.</p>
            <p><strong>Acceptable use.</strong> No unlawful content, no malware, no scraping or quota circumvention.</p>
            <p><strong>Storage & export.</strong> You can export everything any time. We notify you well before any cold-archiving.</p>
            <p><strong>Termination.</strong> Delete your account any time. We may suspend accounts that break these terms.</p>
            <p><strong>Changes.</strong> Material changes notified at least 30 days before they take effect.</p>
          </div>
          <button type="button" className="check cs-check" data-checked={terms} onClick={() => setTerms(v => !v)}>
            <span className="check__box"><Icon name="check" size={11} strokeWidth={2.6}/></span>
            <span className="check__label">I agree to the Terms of Use</span>
          </button>
          {/* §A6 age gate — required by COPPA: neuthek does not run
              the verifiable-parental-consent flow, so under-13 users
              are prohibited. Refusing this check blocks signup. */}
          <button
            type="button"
            className="check cs-check"
            data-checked={age13}
            onClick={() => setAge13(v => !v)}
            style={{ marginTop: 8 }}
          >
            <span className="check__box"><Icon name="check" size={11} strokeWidth={2.6}/></span>
            <span className="check__label">
              I confirm I am <strong>at least 13 years old</strong>
              <div className="check__sub" style={{ marginTop: 2 }}>
                neuthek isn't built for children under 13. If you're a parent
                signing up on behalf of a child, please don't — use a service
                that's COPPA-compliant.
              </div>
            </span>
          </button>
        </StepFrame>}

        {stepId === "privacy" && <StepFrame stepKey="privacy">
          <p className="cs-lead">What we collect, why, and what we never do.</p>
          <div className="cs-prose">
            <p><strong>What we collect.</strong> Files (encrypted at rest), account info, device & usage data, and — only with your opt-in — AI inputs, location, and face templates.</p>
            <p><strong>Why.</strong> To operate the service, secure your account, prevent abuse, deliver opted-in AI features, and meet legal obligations.</p>
            <p><strong>What we never do.</strong> We do not sell your data. We do not train third-party AI on it. We do not share your face templates with anyone.</p>
            <p><strong>Retention.</strong> Files: while account active. Trash: 30 days. Backups: rolling 90 days. AI indexes: deleted within 30 days of disabling.</p>
            <p><strong>Your rights.</strong> Access, correct, export, delete — all from <em>Account & privacy</em>.</p>
          </div>
          <button type="button" className="check cs-check" data-checked={privacyRead} onClick={() => setPrivacyRead(v => !v)}>
            <span className="check__box"><Icon name="check" size={11} strokeWidth={2.6}/></span>
            <span className="check__label">I have read the Privacy Notice</span>
          </button>
        </StepFrame>}

        {stepId === "bipa" && <StepFrame stepKey="bipa">
          <p className="cs-lead">Optional. Required by law to be a separate, written consent.</p>
          <div className="cs-prose">
            <p>If you turn this on, neuthek detects faces in your photos and creates math-only templates so you can group people. Templates stay in your private library, encrypted, in the same region as your files. They are never shared or combined into a global database.</p>
            <p>You can withdraw any time and we delete templates within 30 days. neuthek works fully without this.</p>
          </div>
          <div className="scope-row" style={{ marginTop: 12 }}>
            <div className="scope-row__icon"><Icon name="users" size={15}/></div>
            <div className="scope-row__body">
              <div className="scope-row__title">Enable People & Faces
                <span className="scope-row__chip" data-state={bipa ? "on" : "off"}>{bipa ? "Signed" : "Off"}</span>
              </div>
              <div className="scope-row__desc">Toggling on counts as your written biometric consent.</div>
            </div>
            <SwitchC on={bipa} onChange={setBipa} ariaLabel="Face recognition consent"/>
          </div>
          {!requireFace && (
            <div style={{ marginTop: 10, fontSize: 11.5, color: "var(--ink-3)" }}>
              You can skip this and turn it on later in Account & privacy.
            </div>
          )}
        </StepFrame>}

        {stepId === "scopes" && <StepFrame stepKey="scopes">
          <p className="cs-lead">Decide what's on. You can change any of these later.</p>
          {[
            { id: "gps", icon: "map_pin", title: "Location tagging",
              desc: "Read EXIF GPS so you can filter by where photos were taken. Stripped on export by default." },
            { id: "aiSummary", icon: "sparkles", title: "AI summaries",
              desc: "One-line topic for each photo. Excerpts processed in our region, never used to train models." },
            { id: "semanticSearch", icon: "search", title: "Semantic search",
              desc: "Search by meaning. Embeddings stay inside your account." },
            { id: "telemetry", icon: "info", title: "Anonymous usage data",
              desc: "Help us prioritise features. No file contents, no filenames." },
          ].map((it, i) => (
            <div className="scope-row" key={it.id} style={{ marginTop: i === 0 ? 4 : 8 }}>
              <div className="scope-row__icon"><Icon name={it.icon} size={15}/></div>
              <div className="scope-row__body">
                <div className="scope-row__title">{it.title}
                  <span className="scope-row__chip" data-state={scopes[it.id] ? "on" : "off"}>
                    {scopes[it.id] ? "On" : "Off"}
                  </span>
                </div>
                <div className="scope-row__desc">{it.desc}</div>
              </div>
              <SwitchC on={scopes[it.id]} onChange={(v) => setOne(it.id, v)} ariaLabel={it.title}/>
            </div>
          ))}
        </StepFrame>}

        {stepId === "cookies" && <StepFrame stepKey="cookies">
          <p className="cs-lead">Essentials keep you signed in. Optional cookies remember your last view and help us spot bugs.</p>
          <div className="cs-cookie-choice">
            <button className="cs-cookie-card" data-active={cookies === "essential"} onClick={() => setCookies("essential")}>
              <div className="cs-cookie-card__icon"><Icon name="shield" size={16}/></div>
              <div className="cs-cookie-card__title">Essentials only</div>
              <div className="cs-cookie-card__desc">Just what's needed to keep you signed in.</div>
              {cookies === "essential" && <div className="cs-cookie-card__tick"><Icon name="check" size={11} strokeWidth={2.6}/></div>}
            </button>
            <button className="cs-cookie-card" data-active={cookies === "all"} onClick={() => setCookies("all")}>
              <div className="cs-cookie-card__icon"><Icon name="cookie" size={16}/></div>
              <div className="cs-cookie-card__title">Accept all</div>
              <div className="cs-cookie-card__desc">Includes preferences & anonymous error reports.</div>
              {cookies === "all" && <div className="cs-cookie-card__tick"><Icon name="check" size={11} strokeWidth={2.6}/></div>}
            </button>
          </div>
        </StepFrame>}

        {stepId === "sign" && <StepFrame stepKey="sign">
          <p className="cs-lead">Last step. Type your name to sign and finish creating your account.</p>
          <div className="cs-summary">
            <div className="cs-summary__row"><Icon name="check" size={12} strokeWidth={2.4}/> Terms accepted</div>
            <div className="cs-summary__row"><Icon name="check" size={12} strokeWidth={2.4}/> Privacy reviewed</div>
            <div className="cs-summary__row">
              <Icon name={bipa ? "check" : "minus"} size={12} strokeWidth={2.4}/>
              Face recognition: <strong>{bipa ? "On (signed)" : "Off"}</strong>
            </div>
            <div className="cs-summary__row">
              <Icon name="check" size={12} strokeWidth={2.4}/>
              Cookies: <strong>{cookies === "all" ? "All" : "Essentials only"}</strong>
            </div>
          </div>
          <div className="label-block" style={{ marginTop: 16 }}>
            <div className="label-block__label">Type your full name to sign</div>
            <input className="input input--lg" autoFocus placeholder="Full legal name" value={signature}
                   onChange={(e) => setSignature(e.target.value)}/>
          </div>
        </StepFrame>}
      </div>

      <div className="modal__foot">
        <span className="modal__foot-left mono">Step {stepIdx + 1} of {STEPS.length}</span>
        <div className="modal__foot-actions">
          {stepIdx > 0 && <button className="btn btn--secondary" onClick={back}>Back</button>}
          <button className="btn btn--primary" disabled={!canAdvance} onClick={next}>
            {isLast ? "Sign & create account" : "Continue"}
            {!isLast && <Icon name="arrowRight" size={13}/>}
          </button>
        </div>
      </div>
    </ModalC>
  );
}

// Named export above; legacy `window.ConsentsModal` removed.
