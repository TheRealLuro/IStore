// Upload modal — drop zone + queued file rows with progress.
//
// Wired to the real backend: each queued file is POSTed via uploadWithProgress
// from src/api/uploadWithProgress.ts. React Query is invalidated on completion
// so the gallery refreshes automatically.
import React, { useState as useStateU, useEffect as useEffectU, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import {
  Modal as ModalU,
  ModalClose as ModalCloseU,
} from "./primitives.jsx";
import { uploadFileWithProgress } from "@/api/uploadWithProgress";

function fmtBytes(n) {
  if (!n && n !== 0) return "—";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
  return (n / 1024 / 1024 / 1024).toFixed(2) + " GB";
}

function extOf(name) {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "?" : name.slice(dot + 1).toUpperCase();
}

export function UploadModal({ open, onClose }) {
  const qc = useQueryClient();
  const [hover, setHover] = useStateU(false);
  const [queue, setQueue] = useStateU([]);
  const fileInputRef = useRef(null);

  useEffectU(() => {
    if (!open) { setQueue([]); return; }
  }, [open]);

  // Drives a real upload for every queued file. Each row's `progress` is the
  // percentage from XHR onprogress; on success/failure we mark `done`/`error`.
  const enqueue = (files) => {
    const arr = Array.from(files || []);
    if (!arr.length) return;
    const rows = arr.map((f, i) => ({
      id: "u" + Date.now() + i,
      name: f.name,
      size: fmtBytes(f.size),
      bytes: f.size,
      ext: extOf(f.name),
      progress: 0,           // 0-100, true byte progress
      phase: "uploading",    // "uploading" | "processing" | "done" | "error"
      done: false,
      error: null,
      uploadedBytes: 0,
      _file: f,
    }));
    setQueue((q) => [...q, ...rows]);
    rows.forEach((row) => {
      const { promise } = uploadFileWithProgress(row._file, (p) => {
        const pct = Math.round((p.uploadedBytes / Math.max(1, p.totalBytes)) * 100);
        setQueue((q) =>
          q.map((it) =>
            it.id === row.id
              ? { ...it, progress: pct, phase: p.phase, uploadedBytes: p.uploadedBytes }
              : it,
          ),
        );
      });
      promise
        .then(() => {
          setQueue((q) =>
            q.map((it) =>
              it.id === row.id
                ? { ...it, progress: 100, phase: "done", done: true }
                : it,
            ),
          );
          qc.invalidateQueries({ queryKey: ["files"] });
          qc.invalidateQueries({ queryKey: ["storage"] });
          qc.invalidateQueries({ queryKey: ["facets"] });
        })
        .catch((err) => {
          setQueue((q) =>
            q.map((it) =>
              it.id === row.id
                ? { ...it, phase: "error", error: err.message || "Upload failed" }
                : it,
            ),
          );
          toast.error(`${row.name}: ${err.message || "upload failed"}`);
        });
    });
  };

  const onPick = () => fileInputRef.current?.click();

  const allDone = queue.length > 0 && queue.every(q => q.done || q.error);

  return (
    <ModalU open={open} onClose={onClose} size="lg" labelledBy="upl-title">
      <div className="modal__head">
        <h2 id="upl-title">
          <span className="modal__head-icon"><Icon name="upload" size={16}/></span>
          Upload files
        </h2>
        <p>Drop files here, or pick from your device. Originals are kept at full resolution.</p>
        <ModalCloseU onClose={onClose}/>
      </div>
      <div className="modal__body">
        <div className="dropzone" data-active={hover}
             onDragOver={(e) => { e.preventDefault(); setHover(true); }}
             onDragLeave={() => setHover(false)}
             onDrop={(e) => { e.preventDefault(); setHover(false); enqueue(e.dataTransfer.files); }}>
          <div className="dropzone__icon"><Icon name="cloud" size={36} strokeWidth={1.4}/></div>
          <div className="dropzone__title">Drop files to upload</div>
          <div className="dropzone__sub">JPG · PNG · HEIC · MP4 · MOV · PDF · DOCX · up to 5 GB each</div>
          <div className="dropzone__or">— or —</div>
          <button className="btn btn--secondary" onClick={onPick}>Choose from device</button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            style={{ display: "none" }}
            onChange={(e) => { enqueue(e.target.files); e.target.value = ""; }}
          />
        </div>

        {queue.length > 0 && (
          <div className="upload-list">
            {queue.map(it => {
              // Bar fill: while uploading, follow real byte progress.
              // While processing (bytes done, server still working),
              // animate an indeterminate stripe so the user sees motion
              // instead of a static 99/100% — reads as "still going."
              const barClass = "upload-row__bar-fill"
                + (it.phase === "processing" ? " upload-row__bar-fill--indeterminate" : "");
              const phaseLabel = it.error
                ? `error — ${it.error}`
                : it.phase === "done"
                  ? "done"
                  : it.phase === "processing"
                    ? "processing on server…"
                    : `${Math.round(it.progress)}% · ${fmtBytes(it.uploadedBytes)} / ${it.size}`;
              return (
                <div className="upload-row" key={it.id}>
                  <div className="upload-row__icon">{it.ext}</div>
                  <div className="upload-row__body">
                    <div className="upload-row__name">{it.name}</div>
                    <div className="upload-row__bar" data-phase={it.phase}>
                      <div className={barClass}
                           style={it.phase === "processing"
                             ? { width: "100%" }
                             : { width: it.progress + "%" }}/>
                    </div>
                    <div className="upload-row__meta">{phaseLabel}</div>
                  </div>
                  <div className={"upload-row__status" + (it.phase === "done" ? " upload-row__status--done" : "")}>
                    {it.error
                      ? <Icon name="alert" size={14}/>
                      : it.phase === "done"
                        ? <Icon name="check" size={14} strokeWidth={2.6}/>
                        : it.phase === "processing"
                          ? "…"
                          : Math.round(it.progress) + "%"}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
      <div className="modal__foot">
        <span className="modal__foot-left">
          {queue.length === 0 ? "No files queued" :
           allDone ? `${queue.filter(q => q.done).length} of ${queue.length} uploaded` :
           `${queue.filter(q => q.done).length} of ${queue.length} done`}
        </span>
        <div className="modal__foot-actions">
          <button className="btn btn--secondary" onClick={onClose}>{allDone ? "Close" : "Cancel"}</button>
          {!allDone && queue.length > 0 && <button className="btn btn--ghost">Pause all</button>}
          {allDone && <button className="btn btn--primary" onClick={onClose}>Done</button>}
        </div>
      </div>
    </ModalU>
  );
}

// Named export above; legacy `window.UploadModal` removed.
