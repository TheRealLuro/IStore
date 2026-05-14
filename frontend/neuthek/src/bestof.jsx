// Best-of-N picker — Apple-clean redesign.
// Big stage with the current pick, criteria pills above, breakdown row,
// then a small thumbnail strip to override the choice.
import React, {
  useState as useStateBo,
  useEffect as useEffectBo,
  useMemo as useMemoBo,
  useRef as useRefBo,
} from "react";
import { Icon } from "./icons.jsx";
import {
  Modal as ModalBo,
  ModalClose as ModalCloseBo,
} from "./primitives.jsx";

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

const CRITERIA = [
  { id: "sharp",  label: "Sharpness" },
  { id: "expr",   label: "Expression" },
  { id: "eyes",   label: "Eyes open" },
  { id: "comp",   label: "Composition" },
  { id: "light",  label: "Lighting" },
];

function seededScore(seed, criterion) {
  let h = 2166136261;
  const s = seed + criterion;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return Math.abs(h % 100);
}

export function BestOfModal({ open, onClose, onPick }) {
  const [step, setStep] = useStateBo("upload"); // upload | analyze | review
  const [shots, setShots] = useStateBo([]);
  const [active, setActive] = useStateBo({ sharp: true, expr: true, eyes: true, comp: false, light: false });
  const [pickedIdx, setPickedIdx] = useStateBo(null);
  const [progress, setProgress] = useStateBo(0);
  const [drag, setDrag] = useStateBo(false);
  const fileInput = useRefBo(null);

  useEffectBo(() => {
    if (!open) return;
    setStep("upload"); setShots([]); setPickedIdx(null); setProgress(0);
  }, [open]);

  const loadSample = (key) => {
    const items = SAMPLE_BURSTS[key].map((src, i) => ({ id: key + "-" + i, src, name: `${key}_${String(i + 1).padStart(2,"0")}.jpg` }));
    setShots(items); setStep("analyze");
  };
  const onFiles = (fileList) => {
    const arr = Array.from(fileList || []).filter(f => f.type.startsWith("image/")).slice(0, 12);
    if (!arr.length) return;
    Promise.all(arr.map((f, i) => new Promise(res => {
      const r = new FileReader();
      r.onload = () => res({ id: "u-" + i + "-" + f.name, src: r.result, name: f.name });
      r.readAsDataURL(f);
    }))).then(items => { setShots(items); setStep("analyze"); });
  };

  useEffectBo(() => {
    if (step !== "analyze") return;
    setProgress(0);
    const ticker = setInterval(() => setProgress(p => Math.min(100, p + 7 + Math.random() * 6)), 70);
    const t = setTimeout(() => { clearInterval(ticker); setProgress(100); setStep("review"); }, 1300);
    return () => { clearInterval(ticker); clearTimeout(t); };
  }, [step]);

  const scored = useMemoBo(() => {
    const enabled = Object.entries(active).filter(([_, v]) => v).map(([k]) => k);
    if (!enabled.length || !shots.length) return shots.map((s, i) => ({ ...s, idx: i, score: 0, breakdown: {} }));
    return shots.map((shot, i) => {
      const breakdown = {};
      let sum = 0;
      for (const c of enabled) {
        const score = 60 + (seededScore(shot.id, c) % 38);
        breakdown[c] = score;
        sum += score;
      }
      return { ...shot, idx: i, score: Math.round(sum / enabled.length), breakdown };
    }).sort((a, b) => b.score - a.score);
  }, [active, shots]);

  const winnerIdx = scored.length ? scored[0].idx : null;
  const finalIdx = pickedIdx == null ? winnerIdx : pickedIdx;
  const finalCard = scored.find(s => s.idx === finalIdx);

  return (
    <ModalBo open={open} onClose={onClose} size="xl" labelledBy="bo2-title">
      <div className="modal__head">
        <h2 id="bo2-title">
          <span className="modal__head-icon"><Icon name="wand" size={15}/></span>
          Pick the best shot
        </h2>
        <p>
          {step === "upload"  && "Drop a burst of similar photos. We'll score each frame and recommend a keeper."}
          {step === "analyze" && "Scoring frames…"}
          {step === "review"  && "Top pick is highlighted. Tap any frame below to override."}
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
            </>
          )}

          {step === "analyze" && (
            <div className="bo-analyzing">
              <div className="bo-analyzing__hero">
                {shots.slice(0, 6).map((s, i) => (
                  <div key={s.id} className="bo-analyzing__tile" style={{ backgroundImage: `url(${s.src})`, animationDelay: (i * 80) + "ms" }}/>
                ))}
                <div className="bo-analyzing__scan"/>
              </div>
              <div style={{ marginTop: 18, fontSize: 13.5, fontWeight: 500 }}>
                <Icon name="sparkles" size={13}/> Analyzing {shots.length} frames…
              </div>
              <div className="bo-progress"><div className="bo-progress__fill" style={{ width: progress + "%" }}/></div>
              <div className="bo-progress-label mono">{Math.round(progress)}% · sharpness · expression · eyes · composition</div>
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
                    {pickedIdx == null ? "Top pick" : "Your override"}
                  </span>
                  <span className="bo2-score-pill">
                    <span className="bo2-score-pill__num">{finalCard.score}</span>
                    <span className="bo2-score-pill__of">/100</span>
                  </span>
                </div>
                <div className="bo2-stage__bottom">
                  <span className="bo2-stage__filename">{finalCard.name}</span>
                  <span className="bo2-stage__counter">
                    {scored.findIndex(s => s.idx === finalIdx) + 1} of {shots.length}
                  </span>
                </div>
              </div>

              <div className="bo2-criteria-label">Score by</div>
              <div className="bo2-criteria">
                {CRITERIA.map(c => (
                  <button
                    key={c.id}
                    className="bo2-crit"
                    data-active={active[c.id]}
                    onClick={() => setActive(a => ({ ...a, [c.id]: !a[c.id] }))}
                  >
                    {active[c.id] && <Icon name="check" size={10} strokeWidth={2.6}/>}
                    {c.label}
                  </button>
                ))}
              </div>

              <div className="bo2-breakdown">
                {Object.entries(finalCard.breakdown).map(([k, v]) => {
                  const label = CRITERIA.find(c => c.id === k)?.label || k;
                  return (
                    <div key={k} className="bo2-bd">
                      <div className="bo2-bd__label">{label}</div>
                      <div className="bo2-bd__row">
                        <span className="bo2-bd__num">{v}</span>
                        <span className="bo2-bd__of">/100</span>
                      </div>
                      <div className="bo2-bd__track"><div className="bo2-bd__fill" style={{ width: v + "%" }}/></div>
                    </div>
                  );
                })}
              </div>

              <div className="bo2-frames-label">
                <span className="bo2-frames-label__title">All frames</span>
                <span className="bo2-frames-label__hint">Tap to override</span>
              </div>
              <div className="bo2-frames">
                {shots.map((shot, i) => {
                  const card = scored.find(s => s.idx === i);
                  const isFinal = i === finalIdx;
                  return (
                    <button
                      key={shot.id}
                      className="bo2-frame"
                      data-pick={isFinal ? "winner" : ""}
                      style={{ backgroundImage: `url(${shot.src})` }}
                      onClick={() => setPickedIdx(i)}
                      title={shot.name + " · score " + (card?.score ?? "—")}
                    >
                      <span className="bo2-frame__num">#{i + 1}</span>
                      {isFinal && <span className="bo2-frame__check"><Icon name="check" size={11} strokeWidth={2.8}/></span>}
                      <span className="bo2-frame__score">{card?.score ?? "—"}</span>
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
          {step === "review"  && finalIdx != null && `Keeping frame ${finalIdx + 1} · ${shots.length - 1} moved to trash`}
        </span>
        <div className="modal__foot-actions">
          {step === "review" && (
            <button className="btn btn--ghost" onClick={() => { setStep("upload"); setShots([]); setPickedIdx(null); }}>
              <Icon name="arrowLeft" size={12}/> Start over
            </button>
          )}
          <button className="btn btn--secondary" onClick={onClose}>Cancel</button>
          {step === "review" && (
            <button className="btn btn--primary" disabled={finalIdx == null}
                    onClick={() => { onPick && onPick(finalIdx); onClose && onClose(); }}>
              Keep frame {finalIdx != null ? finalIdx + 1 : ""}
            </button>
          )}
        </div>
      </div>
    </ModalBo>
  );
}

// Named export above; legacy `window.BestOfModal` removed.
