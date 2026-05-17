// Best-of-N picker.
//
// When opened from the gallery multi-select bar with `imageIds`
// populated, calls the real /images/best-of backend endpoint. The
// score breakdown rendered on each card (sharpness / exposure / face
// / use_case) comes from the server's measurements — not the seeded
// mock that lived here before.
//
// When opened with no `imageIds` (legacy / marketing-screenshot
// path), falls back to the upload-or-sample-burst flow so a user can
// try the feature before they have a multi-select.
//
// "Keep this one" closes the modal AND trashes every other selected
// photo via the existing bulk-delete endpoint. Soft delete — they
// land in Trash and can be restored within 30 days.
import React, {
  useState as useStateBo,
  useEffect as useEffectBo,
  useMemo as useMemoBo,
  useRef as useRefBo,
} from "react";
import toast from "react-hot-toast";
import { useQueryClient } from "@tanstack/react-query";
import { Icon } from "./icons.jsx";
import {
  Modal as ModalBo,
  ModalClose as ModalCloseBo,
} from "./primitives.jsx";
import { pickBestOf, bulkDelete, servedUrl } from "@/api/files";

// Use-case prompts. Keep aligned with backend/best_of.py USE_CASE_PROMPTS.
const USE_CASES = [
  { id: "portrait",  label: "Portrait" },
  { id: "landscape", label: "Landscape" },
  { id: "social",    label: "Social media" },
  { id: "print",     label: "Print quality" },
  { id: "candid",    label: "Candid" },
  { id: "pet",       label: "Pet" },
];

const MODES = [
  { id: "overall",  label: "Overall best",  hint: "Single best photo in the selection" },
  { id: "burst",    label: "Best of burst", hint: "Cluster similar shots, pick keeper per burst" },
  { id: "use_case", label: "For a use case", hint: "Match against a specific purpose" },
];

// Sample bursts kept for the no-imageIds fallback path. Unsplash URLs
// stay outside the user's library — pure demo.
const SAMPLE_BURSTS = {
  portraits: [
    "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=900",
    "https://images.unsplash.com/photo-1517423440428-a5a00ad493e8?w=900",
    "https://images.unsplash.com/photo-1487412720507-e7ab37603c6f?w=900",
    "https://images.unsplash.com/photo-1547425260-76bcadfb4f2c?w=900",
    "https://images.unsplash.com/photo-1495216875107-c6c043eb703f?w=900",
    "https://images.unsplash.com/photo-1502323777036-f29e3972d82f?w=900",
  ],
  pets: [
    "https://images.unsplash.com/photo-1517849845537-4d257902454a?w=900",
    "https://images.unsplash.com/photo-1583511655826-05700d52f4d9?w=900",
    "https://images.unsplash.com/photo-1543466835-00a7907e9de1?w=900",
    "https://images.unsplash.com/photo-1561037404-61cd46aa615b?w=900",
    "https://images.unsplash.com/photo-1518717758536-85ae29035b6d?w=900",
    "https://images.unsplash.com/photo-1591946614720-90a587da4a36?w=900",
  ],
};

function seededScore(seed, criterion) {
  let h = 2166136261;
  const s = seed + criterion;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return Math.abs(h % 100);
}

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
  // shots[i] = { id, src, name, breakdown?, score?, cluster_id?, reasons? }
  const [shots, setShots] = useStateBo([]);
  const [pickedIdx, setPickedIdx] = useStateBo(null);
  const [progress, setProgress] = useStateBo(0);
  const [drag, setDrag] = useStateBo(false);
  const [mode, setMode] = useStateBo("overall");
  const [useCase, setUseCase] = useStateBo("portrait");
  const [error, setError] = useStateBo("");
  const [busyKeep, setBusyKeep] = useStateBo(false);
  const fileInput = useRefBo(null);

  // Auto-route on open. If we have a real multi-selection, jump
  // straight to analyze; otherwise the legacy upload-or-sample path.
  useEffectBo(() => {
    if (!open) return;
    setError("");
    setPickedIdx(null);
    setProgress(0);
    if (haveSelection) {
      const shotsFromIds = imageIds.map((id) => ({
        id,
        src: servedUrl(id, { maxDim: 900 }),
        name: id.slice(0, 8),
      }));
      setShots(shotsFromIds);
      setStep("analyze");
    } else {
      setShots([]);
      setStep("upload");
    }
  }, [open, haveSelection, imageIds.join(",")]);

  // Real scoring pass — runs when we have a real selection.
  useEffectBo(() => {
    if (step !== "analyze" || !haveSelection) return;
    let cancelled = false;
    setProgress(0);
    setError("");

    // Animate the progress bar to ~80% while the request is in flight;
    // jump to 100% when it returns. Average call is ~0.5-2 s for N=10.
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
        // Merge scores back onto shots by image_id (preserve order
        // from the user's selection, NOT the backend ranking — we want
        // the displayed strip to match what they had selected).
        const byId = new Map(resp.results.map((r) => [r.image_id, r]));
        const enriched = imageIds.map((id, i) => {
          const r = byId.get(id);
          return {
            id,
            src: servedUrl(id, { maxDim: 900 }),
            name: id.slice(0, 8),
            breakdown: r?.breakdown || {},
            score: r?.score ?? 0,
            cluster_id: r?.cluster_id ?? 0,
            reasons: r?.reasons || [],
          };
        });
        setShots(enriched);
        setProgress(100);
        // Default pick = backend's top result.
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

  // Legacy mock path — only runs in the no-selection / sample / upload flow.
  // We keep the seeded scoring there so the marketing demo still works
  // without hitting the backend.
  useEffectBo(() => {
    if (step !== "analyze" || haveSelection) return;
    setProgress(0);
    const ticker = setInterval(() => setProgress((p) => Math.min(100, p + 7 + Math.random() * 6)), 70);
    const t = setTimeout(() => {
      clearInterval(ticker);
      setProgress(100);
      // Seed mock scores so the review UI renders.
      const enriched = shots.map((s) => ({
        ...s,
        breakdown: {
          sharpness: 60 + (seededScore(s.id, "sharp")  % 38),
          exposure:  60 + (seededScore(s.id, "expr")   % 38),
          face:      60 + (seededScore(s.id, "eyes")   % 38),
        },
        score: 60 + (seededScore(s.id, "ovr") % 38),
        reasons: [],
        cluster_id: 0,
      }));
      setShots(enriched);
      setPickedIdx(0);
      setStep("review");
    }, 1300);
    return () => { clearInterval(ticker); clearTimeout(t); };
  }, [step, haveSelection]);

  const loadSample = (key) => {
    const items = SAMPLE_BURSTS[key].map((src, i) => ({
      id: key + "-" + i, src, name: `${key}_${String(i + 1).padStart(2, "0")}.jpg`,
    }));
    setShots(items);
    setStep("analyze");
  };
  const onFiles = (fileList) => {
    const arr = Array.from(fileList || []).filter((f) => f.type.startsWith("image/")).slice(0, 12);
    if (!arr.length) return;
    Promise.all(arr.map((f, i) => new Promise((res) => {
      const r = new FileReader();
      r.onload = () => res({ id: "u-" + i + "-" + f.name, src: r.result, name: f.name });
      r.readAsDataURL(f);
    }))).then((items) => { setShots(items); setStep("analyze"); });
  };

  // Sorted-by-score view (winners first) for the analytics bits.
  const sorted = useMemoBo(
    () => [...shots].map((s, i) => ({ ...s, idx: i })).sort((a, b) => (b.score ?? 0) - (a.score ?? 0)),
    [shots]
  );

  const finalIdx = pickedIdx ?? 0;
  const finalCard = shots[finalIdx];
  const losers = shots.filter((_, i) => i !== finalIdx);

  const doKeep = async () => {
    if (busyKeep || finalCard == null) return;
    // Only the real-selection path actually deletes anything — the
    // mock/sample path just closes the modal (no real photos involved).
    if (!haveSelection) {
      onClose?.();
      return;
    }
    const loserIds = losers.map((s) => s.id);
    if (loserIds.length === 0) {
      onClose?.();
      return;
    }
    const ok = window.confirm(
      `Keep "${finalCard.name}" and move ${loserIds.length} other photo${loserIds.length === 1 ? "" : "s"} to Trash?`
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
          {step === "upload"  && "Drop a burst of similar photos. We'll score each frame and recommend a keeper."}
          {step === "analyze" && (haveSelection
            ? `Scoring ${imageIds.length} photos on the server (sharpness, exposure, face, use-case match)…`
            : "Scoring frames…")}
          {step === "review"  && "Top pick is highlighted. Tap any frame to override. \"Keep this one\" moves the others to Trash."}
          {step === "error"   && "Something went wrong scoring this batch."}
        </p>
        <ModalCloseBo onClose={onClose}/>
      </div>

      <div className="modal__body">
        <div className="bo2-shell">
          {step === "upload" && (
            <>
              <div
                className="bo-drop"
                data-drag={drag}
                onClick={() => fileInput.current?.click()}
                onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
                onDragLeave={() => setDrag(false)}
                onDrop={(e) => { e.preventDefault(); setDrag(false); onFiles(e.dataTransfer.files); }}
              >
                <div className="bo-drop__icon"><Icon name="upload" size={26} strokeWidth={1.4}/></div>
                <div className="bo-drop__title">Drop a burst here</div>
                <div className="bo-drop__sub">Up to 12 similar photos · JPG, PNG, HEIC</div>
                <button className="btn btn--secondary btn--sm" onClick={(e) => { e.stopPropagation(); fileInput.current?.click(); }}>
                  <Icon name="folder" size={12}/> Choose files
                </button>
                <input ref={fileInput} type="file" accept="image/*" multiple style={{ display: "none" }} onChange={(e) => onFiles(e.target.files)}/>
              </div>
              <div className="bo-or"><span>or try a sample</span></div>
              <div className="bo-samples">
                <button className="bo-sample" onClick={() => loadSample("portraits")}>
                  <div className="bo-sample__cover" style={{ backgroundImage: `url(${SAMPLE_BURSTS.portraits[0]})` }}/>
                  <div className="bo-sample__label"><strong>Portraits</strong><span>6 frames · group selfie</span></div>
                </button>
                <button className="bo-sample" onClick={() => loadSample("pets")}>
                  <div className="bo-sample__cover" style={{ backgroundImage: `url(${SAMPLE_BURSTS.pets[0]})` }}/>
                  <div className="bo-sample__label"><strong>Pets</strong><span>6 frames · dog portraits</span></div>
                </button>
              </div>
              <div style={{ marginTop: 18, fontSize: 12, color: "var(--ink-3)", textAlign: "center" }}>
                Tip: in the gallery, multi-select photos and click <strong style={{ color: "var(--ink-2)" }}>Pick best of burst</strong> to score your real library.
              </div>
            </>
          )}

          {step === "analyze" && (
            <div className="bo-analyzing">
              <div className="bo-analyzing__hero">
                {shots.slice(0, 6).map((s, i) => (
                  <div key={s.id} className="bo-analyzing__tile"
                       style={{ backgroundImage: `url(${s.src})`, animationDelay: (i * 80) + "ms" }}/>
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
                <div className="bo2-stage__img" style={{ backgroundImage: `url(${finalCard.src})` }}/>
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
                    {finalCard.name}
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

              {haveSelection && (
                <>
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
                    <div className="bo2-criteria" style={{ marginTop: 6 }}>
                      {USE_CASES.map((u) => (
                        <button
                          key={u.id}
                          className="bo2-crit"
                          data-active={useCase === u.id}
                          onClick={() => { setUseCase(u.id); setStep("analyze"); }}
                          style={{ fontSize: 11 }}
                        >
                          {useCase === u.id && <Icon name="check" size={9} strokeWidth={2.6}/>}
                          {u.label}
                        </button>
                      ))}
                    </div>
                  )}
                </>
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
                    <button
                      key={shot.id}
                      className="bo2-frame"
                      data-pick={isFinal ? "winner" : ""}
                      style={{ backgroundImage: `url(${shot.src})` }}
                      onClick={() => setPickedIdx(i)}
                      title={shot.name + " · score " + (shot.score != null ? Math.round(shot.score) : "—")}
                    >
                      <span className="bo2-frame__num">#{i + 1}</span>
                      {isFinal && <span className="bo2-frame__check"><Icon name="check" size={11} strokeWidth={2.8}/></span>}
                      <span className="bo2-frame__score">{shot.score != null ? Math.round(shot.score) : "—"}</span>
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="modal__foot">
        <span className="modal__foot-left mono">
          {step === "upload"  && "Step 1 of 3 · Upload"}
          {step === "analyze" && "Step 2 of 3 · Analyze"}
          {step === "review"  && finalCard && (
            haveSelection
              ? `Keeping ${finalCard.name} · ${losers.length} moved to Trash`
              : `Keeping frame ${finalIdx + 1} · ${shots.length - 1} moved to trash`
          )}
        </span>
        <div className="modal__foot-actions">
          {step === "review" && !haveSelection && (
            <button className="btn btn--ghost" onClick={() => { setStep("upload"); setShots([]); setPickedIdx(null); }}>
              <Icon name="arrowLeft" size={12}/> Start over
            </button>
          )}
          <button className="btn btn--secondary" onClick={onClose} disabled={busyKeep}>Cancel</button>
          {step === "review" && finalCard && (
            <button className="btn btn--primary" onClick={doKeep} disabled={busyKeep}>
              {busyKeep ? "Working…" : (haveSelection ? `Keep this one (trash ${losers.length})` : `Keep frame ${finalIdx + 1}`)}
            </button>
          )}
        </div>
      </div>
    </ModalBo>
  );
}
