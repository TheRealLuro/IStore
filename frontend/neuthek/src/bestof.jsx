// Best-of-N picker.
//
// User multi-selects 2+ photos in the gallery, hits "Pick best of
// burst" → this modal calls POST /images/best-of, renders the
// per-criterion measured scores from the backend, and on "Keep this
// one" trashes every other selected photo via /images/bulk-delete.
//
// Image rendering goes through AuthedThumb / useAuthedBlobUrl
// (auth-image.jsx). The served-variant endpoint requires a Bearer
// token on every request, which a CSS `background-image: url(...)`
// can't carry — without the blob wrapper the modal would render
// empty tiles even though the API call succeeded.
//
// Opening this modal with no selection drops the user on a friendly
// empty state pointing them at the multi-select bar. The old
// upload-a-burst / try-a-sample mock that lived here was a marketing
// demo, not a useful in-app flow — removed so users don't think the
// feature operates on uploads instead of their library.
import React, {
  useState as useStateBo,
  useEffect as useEffectBo,
  useMemo as useMemoBo,
} from "react";
import toast from "react-hot-toast";
import { useQueryClient } from "@tanstack/react-query";
import { Icon } from "./icons.jsx";
import {
  Modal as ModalBo,
  ModalClose as ModalCloseBo,
} from "./primitives.jsx";
import { AuthedThumb, useAuthedBlobUrl } from "./auth-image.jsx";
import { pickBestOf, bulkDelete, servedUrl } from "@/api/files";

// Use-case preset chips. The user can also type free text into the
// custom input — handled by passing the raw string as `useCase` and
// letting the backend wrap it in the "a sharp well-composed
// photograph of <text>" template via _resolve_use_case_prompt.
// Keep this list aligned with backend/best_of.py USE_CASE_PROMPTS.
const USE_CASES = [
  // People
  { id: "portrait",  label: "Portrait",  group: "People" },
  { id: "candid",    label: "Candid",    group: "People" },
  { id: "group",     label: "Group",     group: "People" },
  { id: "kids",      label: "Kids",      group: "People" },
  { id: "pet",       label: "Pet",       group: "People" },
  { id: "wedding",   label: "Wedding",   group: "People" },
  // Scenes
  { id: "landscape", label: "Landscape", group: "Scenes" },
  { id: "sunset",    label: "Sunset",    group: "Scenes" },
  { id: "nature",    label: "Nature",    group: "Scenes" },
  { id: "city",      label: "City",      group: "Scenes" },
  { id: "architecture", label: "Architecture", group: "Scenes" },
  { id: "interior",  label: "Interior",  group: "Scenes" },
  { id: "travel",    label: "Travel",    group: "Scenes" },
  // Content
  { id: "food",      label: "Food",      group: "Content" },
  { id: "product",   label: "Product",   group: "Content" },
  { id: "document",  label: "Document",  group: "Content" },
  { id: "screenshot",label: "Screenshot",group: "Content" },
  { id: "art",       label: "Art",       group: "Content" },
  { id: "macro",     label: "Macro",     group: "Content" },
  { id: "sports",    label: "Sports",    group: "Content" },
  // Style
  { id: "social",    label: "Social",    group: "Style" },
  { id: "print",     label: "Print",     group: "Style" },
  { id: "black-and-white", label: "B&W", group: "Style" },
  { id: "vintage",   label: "Vintage",   group: "Style" },
  { id: "minimal",   label: "Minimal",   group: "Style" },
];
const USE_CASE_GROUPS = ["People", "Scenes", "Content", "Style"];

const MODES = [
  { id: "overall",  label: "Overall best",  hint: "Single best photo in the selection" },
  { id: "burst",    label: "Best of burst", hint: "Cluster similar shots, pick keeper per burst" },
  { id: "use_case", label: "For a use case", hint: "Match against a specific purpose" },
];

// Friendly criterion labels for the breakdown bars.
const CRIT_LABELS = {
  sharpness: "Sharpness",
  exposure:  "Exposure",
  face:      "Face quality",
  use_case:  "Use-case match",
};

export function BestOfModal({ open, onClose, imageIds = [], onAfterKeep }) {
  const qc = useQueryClient();
  const haveSelection = imageIds && imageIds.length >= 2;

  const [step, setStep] = useStateBo("idle");
  // shots[i] = { id, url, breakdown?, score?, cluster_id?, reasons? }
  const [shots, setShots] = useStateBo([]);
  const [pickedIdx, setPickedIdx] = useStateBo(null);
  const [progress, setProgress] = useStateBo(0);
  const [mode, setMode] = useStateBo("overall");
  // `useCase` is the value we'll send to the backend. It's either a
  // preset id ("portrait") or arbitrary free text ("vintage car").
  // `customText` is the live value of the typed-in input box; when
  // the user submits it (Enter or blur), we set useCase = customText.
  const [useCase, setUseCase] = useStateBo("portrait");
  const [customText, setCustomText] = useStateBo("");
  // Resolved prompt echoed back from the backend so the user can see
  // exactly what their typed prompt got expanded to.
  const [resolvedPrompt, setResolvedPrompt] = useStateBo(null);
  const [error, setError] = useStateBo("");
  const [busyKeep, setBusyKeep] = useStateBo(false);

  // Route on open: empty state if no selection, otherwise jump
  // straight into the scoring pass.
  useEffectBo(() => {
    if (!open) return;
    setError("");
    setPickedIdx(null);
    setProgress(0);
    if (haveSelection) {
      const initial = imageIds.map((id) => ({
        id,
        url: servedUrl(id, { maxDim: 900 }),
      }));
      setShots(initial);
      setStep("analyze");
    } else {
      setShots([]);
      setStep("empty");
    }
  }, [open, haveSelection, imageIds.join(",")]);

  // Real scoring pass — runs every time we re-enter analyze
  // (mode/use-case toggle re-triggers it).
  useEffectBo(() => {
    if (step !== "analyze" || !haveSelection) return;
    let cancelled = false;
    setProgress(0);
    setError("");

    // Animate up to ~80% while the request is in flight; jump to
    // 100% on response. Typical call ~0.5-2 s for N=10.
    const ticker = setInterval(() => {
      if (cancelled) return;
      setProgress((p) => Math.min(80, p + 6 + Math.random() * 6));
    }, 80);

    (async () => {
      try {
        const opts = { mode };
        if (mode === "use_case") opts.useCase = useCase;
        const resp = await pickBestOf(imageIds, opts);
        if (cancelled) return;
        setResolvedPrompt(resp.resolved_prompt || null);
        // Merge scores back by image_id; preserve the user's original
        // selection order in the display, not the backend ranking.
        const byId = new Map(resp.results.map((r) => [r.image_id, r]));
        const enriched = imageIds.map((id) => {
          const r = byId.get(id);
          return {
            id,
            url: servedUrl(id, { maxDim: 900 }),
            breakdown: r?.breakdown || {},
            score: r?.score ?? 0,
            cluster_id: r?.cluster_id ?? 0,
            reasons: r?.reasons || [],
          };
        });
        setShots(enriched);
        setProgress(100);
        const top = resp.results[0];
        const topIdx = top ? imageIds.indexOf(top.image_id) : 0;
        setPickedIdx(topIdx >= 0 ? topIdx : 0);
        setStep("review");
      } catch (e) {
        if (cancelled) return;
        const msg = e?.detail || e?.message || "Best-of failed.";
        setError(msg);
        setStep("error");
      } finally {
        clearInterval(ticker);
      }
    })();

    return () => { cancelled = true; clearInterval(ticker); };
  }, [step, haveSelection, mode, useCase, imageIds.join(",")]);

  // Score-sorted view (winners first) for the "N of M" counter on the stage.
  const sorted = useMemoBo(
    () => [...shots].map((s, i) => ({ ...s, idx: i })).sort((a, b) => (b.score ?? 0) - (a.score ?? 0)),
    [shots]
  );

  const finalIdx = pickedIdx ?? 0;
  const finalCard = shots[finalIdx];
  const losers = shots.filter((_, i) => i !== finalIdx);

  // Auth'd URL for the big stage. Hook stays at top level — only
  // resolves the URL when we have a final card; null URL is a no-op.
  const stageBlob = useAuthedBlobUrl(finalCard?.url || null);

  const doKeep = async () => {
    if (busyKeep || finalCard == null || !haveSelection) return;
    const loserIds = losers.map((s) => s.id);
    if (loserIds.length === 0) {
      onClose?.();
      return;
    }
    const ok = window.confirm(
      `Keep this photo and move ${loserIds.length} other${loserIds.length === 1 ? "" : "s"} to Trash?`,
    );
    if (!ok) return;
    setBusyKeep(true);
    try {
      const r = await bulkDelete(loserIds);
      toast.success(`Kept 1 photo, moved ${r.count} to Trash.`);
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["facets"] });
      qc.invalidateQueries({ queryKey: ["account-trash"] });
      onAfterKeep?.(finalCard.id, loserIds);
      onClose?.();
    } catch (e) {
      toast.error(e?.detail || "Couldn't move the others to Trash.");
    } finally {
      setBusyKeep(false);
    }
  };

  return (
    <ModalBo open={open} onClose={onClose} size="xl" labelledBy="bo2-title">
      <div className="modal__head">
        <h2 id="bo2-title">
          <span className="modal__head-icon"><Icon name="wand" size={15}/></span>
          {haveSelection
            ? `Pick the best of ${imageIds.length} photos`
            : "Pick the best shot"}
        </h2>
        <p>
          {step === "empty"   && "Multi-select 2+ similar photos in the gallery, then come back here."}
          {step === "analyze" && `Scoring ${imageIds.length} photos on the server (sharpness, exposure, face, use-case match)…`}
          {step === "review"  && "Top pick is highlighted. Tap any frame to override. \"Keep this one\" moves the others to Trash."}
          {step === "error"   && "Something went wrong scoring this batch."}
        </p>
        <ModalCloseBo onClose={onClose}/>
      </div>

      <div className="modal__body">
        <div className="bo2-shell">
          {step === "empty" && (
            <div style={{
              padding: "48px 32px",
              textAlign: "center",
              border: "1px dashed var(--line)",
              borderRadius: 14,
              background: "var(--surface)",
            }}>
              <div style={{
                width: 56, height: 56,
                margin: "0 auto 18px",
                borderRadius: 14,
                background: "var(--surface-2)",
                display: "grid",
                placeItems: "center",
                color: "var(--ink-3)",
              }}>
                <Icon name="wand" size={26} strokeWidth={1.4}/>
              </div>
              <div style={{ fontSize: 18, fontWeight: 600, color: "var(--ink)", marginBottom: 8 }}>
                Select photos to compare
              </div>
              <div style={{ fontSize: 13.5, color: "var(--ink-2)", lineHeight: 1.6, maxWidth: 420, margin: "0 auto" }}>
                Multi-select <strong style={{ color: "var(--ink)" }}>2 to 30</strong> similar photos in the gallery
                (Cmd/Ctrl-click to add, or drag-rectangle), then click{" "}
                <strong style={{ color: "var(--ink)" }}>Pick best of burst</strong> in the action bar.
                We'll score each one on sharpness, exposure, face quality, and an optional use-case
                match — then keep your pick and move the rest to Trash.
              </div>
            </div>
          )}

          {step === "analyze" && (
            <div className="bo-analyzing">
              <div className="bo-analyzing__hero">
                {shots.slice(0, 6).map((s, i) => (
                  <AuthedThumb
                    key={s.id}
                    url={s.url}
                    className="bo-analyzing__tile"
                    style={{ animationDelay: (i * 80) + "ms" }}
                    placeholder={{ background: "var(--surface-2)" }}
                  />
                ))}
                <div className="bo-analyzing__scan"/>
              </div>
              <div style={{ marginTop: 18, fontSize: 13.5, fontWeight: 500 }}>
                <Icon name="sparkles" size={13}/> Analyzing {shots.length} frames…
              </div>
              <div className="bo-progress"><div className="bo-progress__fill" style={{ width: progress + "%" }}/></div>
              <div className="bo-progress-label mono">
                {Math.round(progress)}% · sharpness · exposure · face · {mode === "use_case" ? `use-case (${useCase})` : "composition"}
              </div>
            </div>
          )}

          {step === "error" && (
            <div style={{ padding: 40, textAlign: "center" }}>
              <Icon name="alert" size={32} style={{ color: "var(--bad)" }}/>
              <div style={{ marginTop: 14, fontSize: 14, color: "var(--ink-2)" }}>{error}</div>
              <button className="btn btn--secondary" style={{ marginTop: 16 }} onClick={() => setStep("analyze")}>
                Try again
              </button>
            </div>
          )}

          {step === "review" && finalCard && (
            <>
              <div className="bo2-stage">
                <div
                  className="bo2-stage__img"
                  style={{
                    backgroundImage: stageBlob ? `url(${stageBlob})` : undefined,
                    background: stageBlob ? undefined : "var(--surface-2)",
                  }}
                />
                <div className="bo2-stage__overlay"/>
                <div className="bo2-stage__top">
                  <span className="bo2-toppick">
                    <Icon name="check" size={11} strokeWidth={2.6}/>
                    {pickedIdx == null || pickedIdx === sorted[0]?.idx ? "Top pick" : "Your override"}
                  </span>
                  <span className="bo2-score-pill">
                    <span className="bo2-score-pill__num">{Math.round(finalCard.score ?? 0)}</span>
                    <span className="bo2-score-pill__of">/100</span>
                  </span>
                </div>
                <div className="bo2-stage__bottom">
                  <span className="bo2-stage__filename">
                    {finalCard.id.slice(0, 8)}
                    {finalCard.reasons?.length > 0 && (
                      <span style={{ marginLeft: 8, opacity: 0.7, fontSize: 12 }}>
                        · {finalCard.reasons.join(" · ")}
                      </span>
                    )}
                  </span>
                  <span className="bo2-stage__counter">
                    {sorted.findIndex((s) => s.idx === finalIdx) + 1} of {shots.length}
                  </span>
                </div>
              </div>

              <div className="bo2-criteria-label">Scoring mode</div>
              <div className="bo2-criteria">
                {MODES.map((m) => (
                  <button
                    key={m.id}
                    className="bo2-crit"
                    data-active={mode === m.id}
                    onClick={() => { setMode(m.id); setStep("analyze"); }}
                    title={m.hint}
                  >
                    {mode === m.id && <Icon name="check" size={10} strokeWidth={2.6}/>}
                    {m.label}
                  </button>
                ))}
              </div>
              {mode === "use_case" && (
                <div style={{ marginTop: 6 }}>
                  {/* Free-text input — type anything ("garden", "vintage
                      car", "skateboard tricks") and the backend wraps it
                      with the photo-prompt template. Submit on Enter or
                      blur (when the value changed). */}
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
                    <input
                      type="text"
                      placeholder='Or type a custom use case — e.g. "vintage car", "garden plants"'
                      value={customText}
                      onChange={(e) => setCustomText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && customText.trim()) {
                          e.preventDefault();
                          setUseCase(customText.trim());
                          setStep("analyze");
                        }
                      }}
                      onBlur={() => {
                        const v = customText.trim();
                        if (v && v.toLowerCase() !== useCase.toLowerCase()) {
                          setUseCase(v);
                          setStep("analyze");
                        }
                      }}
                      maxLength={80}
                      style={{
                        flex: 1,
                        minWidth: 0,
                        padding: "8px 12px",
                        border: "1px solid var(--line)",
                        borderRadius: 999,
                        background: "var(--surface)",
                        color: "var(--ink)",
                        fontSize: 13,
                        outline: "none",
                      }}
                    />
                    {customText.trim() && (
                      <button
                        type="button"
                        className="btn btn--secondary btn--sm"
                        onClick={() => {
                          setUseCase(customText.trim());
                          setStep("analyze");
                        }}
                      >
                        Apply
                      </button>
                    )}
                  </div>

                  {/* Preset chips, grouped. Click to apply immediately. */}
                  {USE_CASE_GROUPS.map((g) => (
                    <div key={g} style={{ marginBottom: 6 }}>
                      <div style={{
                        fontSize: 10, color: "var(--ink-3)",
                        textTransform: "uppercase", letterSpacing: "0.08em",
                        margin: "4px 0 4px",
                      }}>
                        {g}
                      </div>
                      <div className="bo2-criteria">
                        {USE_CASES.filter((u) => u.group === g).map((u) => (
                          <button
                            key={u.id}
                            className="bo2-crit"
                            data-active={useCase === u.id}
                            onClick={() => {
                              setUseCase(u.id);
                              setCustomText("");
                              setStep("analyze");
                            }}
                            style={{ fontSize: 11 }}
                          >
                            {useCase === u.id && <Icon name="check" size={9} strokeWidth={2.6}/>}
                            {u.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  ))}

                  {/* Echo the actual CLIP prompt the backend ran. Lets
                      the user confirm their typed text got applied (vs.
                      silently swallowed) and see what preset chips
                      expand to under the hood. */}
                  {resolvedPrompt && (
                    <div style={{
                      marginTop: 8, padding: "6px 10px",
                      background: "var(--surface-2)",
                      border: "1px solid var(--line)",
                      borderRadius: 8,
                      fontSize: 11.5, color: "var(--ink-2)",
                      fontFamily: "ui-monospace, 'Geist Mono', monospace",
                    }}>
                      <span style={{ color: "var(--ink-3)" }}>Scored by:</span> "{resolvedPrompt}"
                    </div>
                  )}
                </div>
              )}

              <div className="bo2-breakdown">
                {Object.entries(finalCard.breakdown || {}).map(([k, v]) => (
                  <div key={k} className="bo2-bd">
                    <div className="bo2-bd__label">{CRIT_LABELS[k] || k}</div>
                    <div className="bo2-bd__row">
                      <span className="bo2-bd__num">{Math.round(v)}</span>
                      <span className="bo2-bd__of">/100</span>
                    </div>
                    <div className="bo2-bd__track"><div className="bo2-bd__fill" style={{ width: v + "%" }}/></div>
                  </div>
                ))}
              </div>

              <div className="bo2-frames-label">
                <span className="bo2-frames-label__title">All frames</span>
                <span className="bo2-frames-label__hint">Tap to override</span>
              </div>
              <div className="bo2-frames">
                {shots.map((shot, i) => {
                  const isFinal = i === finalIdx;
                  return (
                    <AuthedThumb
                      key={shot.id}
                      url={shot.url}
                      className="bo2-frame"
                      data-pick={isFinal ? "winner" : ""}
                      onClick={() => setPickedIdx(i)}
                      title={shot.id.slice(0, 8) + " · score " + (shot.score != null ? Math.round(shot.score) : "—")}
                      placeholder={{ background: "var(--surface-2)" }}
                    >
                      <span className="bo2-frame__num">#{i + 1}</span>
                      {isFinal && <span className="bo2-frame__check"><Icon name="check" size={11} strokeWidth={2.8}/></span>}
                      <span className="bo2-frame__score">{shot.score != null ? Math.round(shot.score) : "—"}</span>
                    </AuthedThumb>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="modal__foot">
        <span className="modal__foot-left mono">
          {step === "empty"   && "Waiting for selection"}
          {step === "analyze" && "Scoring…"}
          {step === "review"  && finalCard && haveSelection &&
            `Keeping ${finalCard.id.slice(0, 8)} · ${losers.length} moved to Trash`}
          {step === "error"   && "Something went wrong"}
        </span>
        <div className="modal__foot-actions">
          <button className="btn btn--secondary" onClick={onClose} disabled={busyKeep}>Cancel</button>
          {step === "review" && finalCard && haveSelection && (
            <button className="btn btn--primary" onClick={doKeep} disabled={busyKeep}>
              {busyKeep ? "Working…" : `Keep this one (trash ${losers.length})`}
            </button>
          )}
        </div>
      </div>
    </ModalBo>
  );
}
