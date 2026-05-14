// File preview panel — slides in from the right when a file is selected.
import React, { useState as useStateP2, useEffect as useEffectP2 } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import { AuthedThumb, AuthedImg, useAuthedBlobUrl } from "./auth-image.jsx";
import { getImagePeople, faceCropUrl, redetectFaces } from "@/api/people";
import { EditableName } from "./nameable-chip.jsx";
import { deleteFile, originalUrl, fetchAsBlobUrl, toggleStar } from "@/api/files";
import { PdfPageStack } from "./pdf-stack.jsx";

function fmtBytes(n) {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

const TAG_SUGGESTIONS = [
  "Favorite", "To review", "Shared", "Archived", "Private", "WIP",
  "Work", "Personal", "Travel", "Family", "Reference", "Receipt",
];

export function PreviewPanel({ file, onClose, onOpenAccount, onRename, user }) {
  const qc = useQueryClient();
  const [tags, setTags] = useStateP2([]);
  const [draft, setDraft] = useStateP2("");
  const [showSuggest, setShowSuggest] = useStateP2(false);
  const [lightbox, setLightbox] = useStateP2(false);
  const [starred, setStarred] = useStateP2(false);

  // Esc closes lightbox first, then preview
  useEffectP2(() => {
    if (!file) return;
    const onKey = (e) => {
      if (e.key === "Escape") {
        if (lightbox) setLightbox(false);
        else onClose && onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [file, lightbox, onClose]);

  // Re-seed tags + star state when the file changes. Star state mirrors
  // the backend column (`file.is_starred`) — the local `starred` is just
  // an optimistic UI flag so the toggle feels instant.
  useEffectP2(() => {
    if (!file) return;
    setTags(Array.isArray(file.tags) ? file.tags : []);
    setStarred(!!file.is_starred);
  }, [file?.id, file?.is_starred]);

  // Real people for this image. Fires only when an image is selected and
  // is gated on the user's face_recognition consent server-side — when
  // consent is off, the endpoint returns []. We render nothing in that case.
  const isImageFile = file?.type === "image";
  const { data: imagePeople = [] } = useQuery({
    queryKey: ["image-people", file?.id],
    queryFn: () => getImagePeople(file.id),
    enabled: !!file && isImageFile,
    staleTime: 30_000,
  });

  // D8 — user-signal re-detect cascade. Tracks busy/result so the UI
  // can swap between "Mark as containing a person" → "Looking…" →
  // "Found N" → "Couldn't find anyone, add manually?" copy.
  const [redetectState, setRedetectState] = useStateP2("idle"); // idle | running | empty
  const handleRedetect = async () => {
    if (!file || redetectState === "running") return;
    setRedetectState("running");
    try {
      const r = await redetectFaces(file.id);
      if (r.persisted > 0) {
        toast.success(`Found ${r.persisted} face${r.persisted === 1 ? "" : "s"} (${r.stage}).`);
        qc.invalidateQueries({ queryKey: ["image-people", file.id] });
        qc.invalidateQueries({ queryKey: ["people"] });
        setRedetectState("idle");
      } else {
        setRedetectState("empty");
      }
    } catch (e) {
      toast.error(e?.detail || "Could not run re-detect.");
      setRedetectState("idle");
    }
  };

  const handleToggleStar = async () => {
    if (!file) return;
    // Optimistic: flip the local flag immediately, hit the backend, roll
    // back on failure. Always invalidate the files cache on success so
    // the sidebar / gallery pick up the new state.
    const next = !starred;
    setStarred(next);
    try {
      await toggleStar(file.id);
      qc.invalidateQueries({ queryKey: ["files"] });
    } catch (e) {
      setStarred(!next);
      toast.error(e?.detail || "Could not save star");
    }
  };

  const handleDownload = async () => {
    if (!file) return;
    try {
      const blob = await fetchAsBlobUrl(originalUrl(file.id));
      const a = document.createElement("a");
      a.href = blob;
      a.download = file.name || "download";
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Revoke a moment later so the browser has time to start the download.
      setTimeout(() => URL.revokeObjectURL(blob), 4000);
    } catch (e) {
      toast.error(e?.detail || "Could not download file");
    }
  };

  const handleDelete = async () => {
    if (!file) return;
    if (!window.confirm(`Move "${file.name}" to trash?`)) return;
    try {
      await deleteFile(file.id);
      toast.success(`Deleted "${file.name}"`);
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["storage"] });
      qc.invalidateQueries({ queryKey: ["geo"] });
      qc.invalidateQueries({ queryKey: ["account-trash"] });
      onClose && onClose();
    } catch (e) {
      toast.error(e?.detail || "Could not delete file");
    }
  };

  const isImage = file?.type === "image";
  const isVideo = file?.type === "video";
  const isDoc = file?.type === "doc";
  // Hook call must stay unconditional — pass null when the file isn't
  // a PDF so the underlying fetch is skipped. PDF preview UX:
  //   - hero pane shows a non-scrolling page-1 render (the raster
  //     stored as `file.thumb` when our PyMuPDF rasterizer ran);
  //   - clicking the hero opens a full-page modal with the real PDF
  //     in an iframe, where the browser handles scroll/zoom/text-select.
  // The blob URL is only needed once the modal opens, so we gate the
  // fetch behind `pdfModal`.
  const isPdf = !!(file && isDoc && (
    (file.mime_type_original || "").toLowerCase() === "application/pdf"
    || (file.ext || "").toLowerCase() === "pdf"
  ));
  const [pdfModal, setPdfModal] = useStateP2(false);
  // Close the PDF modal when the panel is closed externally or the
  // user navigates to a different file.
  useEffectP2(() => { setPdfModal(false); }, [file?.id]);
  // Esc closes the PDF modal before falling through to the panel-close
  // handler. We already have an Esc listener for lightbox + preview;
  // it doesn't know about pdfModal, so add a guard here.
  useEffectP2(() => {
    if (!pdfModal) return;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setPdfModal(false); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [pdfModal]);
  if (!file) return null;

  const addTag = (t) => {
    const v = t.trim();
    if (!v) return;
    setTags(prev => prev.includes(v) ? prev : [...prev, v]);
    setDraft("");
  };
  const removeTag = (t) => setTags(prev => prev.filter(x => x !== t));

  return (
    <React.Fragment>
    <div className="preview-backdrop" onClick={onClose}/>
    {lightbox && file.thumb && (
      <div className="lightbox" onClick={() => setLightbox(false)}>
        <button className="lightbox__close" aria-label="Close" onClick={(e) => { e.stopPropagation(); setLightbox(false); }}>
          <Icon name="x" size={18}/>
        </button>
        <AuthedImg url={file.thumb} className="lightbox__img" alt={file.name} onClick={(e) => e.stopPropagation()}/>
      </div>
    )}
    {pdfModal && isPdf && (
      <div className="lightbox" onClick={() => setPdfModal(false)}>
        <div className="pdf-modal" onClick={(e) => e.stopPropagation()}>
          <div className="pdf-modal__head">
            <span className="pdf-modal__icon">
              <Icon name="document" size={14}/>
            </span>
            <div className="pdf-modal__name">{file.name}</div>
            <span className="pdf-modal__size">{file.size}</span>
            <button
              type="button"
              className="btn-icon"
              onClick={handleDownload}
              aria-label="Download"
              title="Download"
            >
              <Icon name="download" size={14}/>
            </button>
            <button
              type="button"
              className="btn-icon"
              onClick={() => setPdfModal(false)}
              aria-label="Close document"
              title="Close"
            >
              <Icon name="x" size={14}/>
            </button>
          </div>
          <div className="pdf-modal__body">
            {/* Server-rasterized page stack (PyMuPDF → JPEG per page,
                lazy-loaded into a themed scroll container). Replaces the
                old iframe-into-PDFium approach so the scrollbar belongs
                to us and every page is visible in one continuous scroll
                instead of paginated under PDFium's `view=Fit` mode. */}
            <PdfPageStack fileId={file.id}/>
          </div>
        </div>
      </div>
    )}
    <aside className="preview" aria-label="File details" onClick={(e) => e.stopPropagation()}>
      <div className="preview__head">
        <div className="preview__head-title mono">DETAILS</div>
        <div style={{ display: "flex", gap: 4 }}>
          <button
            className="btn-icon"
            aria-label={starred ? "Unstar" : "Star"}
            aria-pressed={starred}
            onClick={handleToggleStar}
            style={starred ? { color: "var(--accent, #f5a623)" } : undefined}
            title={starred ? "Starred" : "Star"}
          >
            <Icon name="star" size={15} strokeWidth={starred ? 0 : 1.6}
                  style={starred ? { fill: "currentColor" } : undefined}/>
          </button>
          <button className="btn-icon" onClick={onClose} aria-label="Close">
            <Icon name="x" size={15}/>
          </button>
        </div>
      </div>

      {isPdf ? (
        // Hero shows the non-scrolling first-page raster (the same
        // image used on the gallery card thumb). Clicking opens a full
        // scrollable browser-native PDF modal. When the raster isn't
        // available (PyMuPDF wasn't installed when the file uploaded —
        // run /admin → Generate PDF thumbnails to backfill), we fall
        // back to the icon and still let the click open the modal.
        file.thumb ? (
          // Fill the hero area with the page-1 raster (cover, anchored
          // to the top so the page header is what the user sees). The
          // previous `contain` left grey letterbox bars that made the
          // page look like a small floating PDF in the corner of the
          // pane. Cover with top alignment matches Drive's doc preview
          // style and reads as "page". The pdf-page modifier swaps in
          // a white background so the bottom edge of a short page
          // doesn't show the app's surface color underneath.
          <AuthedThumb
            url={file.thumb}
            className="preview__hero preview__hero--pdf-page"
            onClick={() => setPdfModal(true)}
            role="button"
            aria-label="Open document"
            title="Click to open document"
            style={{
              cursor: "pointer",
              backgroundSize: "cover",
              backgroundRepeat: "no-repeat",
              backgroundPosition: "center top",
              backgroundColor: "#fff",
            }}
          />
        ) : (
          <button
            type="button"
            onClick={() => setPdfModal(true)}
            className="preview__hero"
            aria-label="Open document"
            title="Click to open document"
            style={{ display: "grid", placeItems: "center", color: "var(--ink-3)", background: "var(--surface-2)", border: 0, cursor: "pointer" }}
          >
            <div className="thumb-icon">
              <Icon name="document" size={42} strokeWidth={1.3}/>
              <span className="mono">PDF</span>
            </div>
          </button>
        )
      ) : file.thumb ? (
        <AuthedThumb
          url={file.thumb}
          className="preview__hero"
          onClick={isImage ? () => setLightbox(true) : undefined}
          role={isImage ? "button" : undefined}
          aria-label={isImage ? "View full size" : undefined}
        />
      ) : (
        <div className="preview__hero" style={{ display: "grid", placeItems: "center", color: "var(--ink-3)" }}>
          <div className="thumb-icon">
            <Icon name={isVideo ? "video" : "document"} size={42} strokeWidth={1.3}/>
            <span className="mono">{file.ext}</span>
          </div>
        </div>
      )}

      <div className="preview__body">
        <div className="preview__title">{file.name}</div>
        {file.topic ? (
          <div className="preview__topic">
            <span className="kicker" style={{ marginRight: 8 }}><Icon name="sparkles" size={10} style={{ verticalAlign: "-1px" }}/> AI</span>
            {file.topic}
          </div>
        ) : file.pendingSummary ? (
          <div className="preview__topic">
            <span className="kicker" style={{ marginRight: 8 }}><Icon name="sparkles" size={10} style={{ verticalAlign: "-1px" }}/> AI</span>
            <span className="skel skel--text" style={{ width: "60%", display: "inline-block" }} aria-label="Generating summary"/>
          </div>
        ) : null}
        <div className="preview__meta">
          <span>{file.when}</span>
          <span style={{ color: "var(--ink-4)" }}>·</span>
          <span className="mono">{file.size}</span>
          <span style={{ color: "var(--ink-4)" }}>·</span>
          <span className="mono">{file.ext}</span>
        </div>

        {/* Rich AI description block — the long-form `summary` text the
            C2 multi-model pipeline produces. This is what makes the
            file searchable, so we surface it prominently. Concept-tag
            chips below give the user immediate scannable keywords.
            When the backend is still summarizing (fresh upload, force
            backfill in flight), render a shimmer placeholder instead
            of an empty slot — the user can see the pipeline is still
            working rather than wondering why nothing's there. */}
        {file.aiContent ? (
          <div className="preview__section">
            <div className="preview__section-label">Description</div>
            <div style={{
              fontSize: 13, lineHeight: 1.55, color: "var(--ink-2)",
              padding: "0 12px",
            }}>
              {file.aiContent}
            </div>
            {Array.isArray(file.signals?.concepts) && file.signals.concepts.length > 0 && (
              <div className="ptags" style={{ marginTop: 10, padding: "0 12px" }}>
                {file.signals.concepts.slice(0, 8).map((c) => (
                  <span key={c} className="ptag" data-tone="muted">{c}</span>
                ))}
              </div>
            )}
          </div>
        ) : file.pendingSummary ? (
          <div className="preview__section">
            <div className="preview__section-label">
              Description
              <span className="kicker" style={{ marginLeft: 8, color: "var(--ink-3)" }}>generating…</span>
            </div>
            <div style={{ padding: "0 12px" }}>
              <div className="skel skel--text" style={{ width: "92%" }}/>
              <div className="skel skel--text" style={{ width: "84%", marginTop: 6 }}/>
              <div className="skel skel--text" style={{ width: "67%", marginTop: 6 }}/>
            </div>
          </div>
        ) : null}

        {isImage && imagePeople.length > 0 && (
          <div className="preview__section">
            <div className="preview__section-label">People in this photo</div>
            <div className="preview__faces">
              {imagePeople.map((p) => {
                const labelled = !!p.person_display_name;
                return (
                  <div className="face-chip" key={p.face_id}>
                    <AuthedThumb
                      url={faceCropUrl(p.face_id)}
                      className="face-chip__avatar"
                      placeholder={{ background: "var(--surface-2)" }}
                    />
                    <EditableName
                      name={p.person_display_name}
                      personId={p.person_id}
                      clusterId={p.cluster_id}
                      className={"face-chip__name" + (labelled ? "" : " face-chip__name--unnamed")}
                      invalidate={[["image-people", file.id], ["people"]]}
                    />
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* D8 — when the default pipeline found no faces, let the user
            insist there's a person here. Click runs the cascade
            (RetinaFace 0.3 → 0.15 → mediapipe). On the still-empty
            terminal state, surface a "manual add" hint — the actual
            user-drawn-box flow is a follow-up. */}
        {isImage && imagePeople.length === 0 && (
          <div className="preview__section">
            <div className="preview__section-label">People in this photo</div>
            {redetectState === "empty" ? (
              <div style={{ fontSize: 12.5, color: "var(--ink-3)", lineHeight: 1.5 }}>
                No faces found, even after re-running detection at a lower
                threshold and the mediapipe fallback.{" "}
                <button
                  type="button"
                  onClick={() => toast("Manual face-add is coming soon — draw a box around the face you want to label.")}
                  style={{ background: "none", border: 0, padding: 0, color: "var(--ink)", textDecoration: "underline", cursor: "pointer", font: "inherit" }}
                >
                  Need to add manually?
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={handleRedetect}
                disabled={redetectState === "running"}
                className="btn btn--ghost btn--sm"
                style={{ alignSelf: "flex-start" }}
              >
                <Icon name={redetectState === "running" ? "refresh" : "users"} size={12}/>
                {redetectState === "running" ? "Looking…" : "Mark as containing a person"}
              </button>
            )}
          </div>
        )}

        <div className="preview__section">
          <div className="preview__section-label">Tags</div>
          <div className="ptags">
            {tags.map(t => {
              const tone =
                /favorite|fav/i.test(t) ? "ink" :
                /review/i.test(t) ? "warn" :
                /shared/i.test(t) ? "info" :
                /private|archived/i.test(t) ? "muted" :
                /wip|work/i.test(t) ? "ok" : undefined;
              return (
                <span key={t} className="ptag" data-tone={tone}>
                  {t}
                  <button className="ptag__x" onClick={() => removeTag(t)} aria-label={`Remove ${t}`}>
                    <Icon name="x" size={9} strokeWidth={2.4}/>
                  </button>
                </span>
              );
            })}
            <div className="ptag-add">
              <input
                value={draft}
                placeholder={tags.length ? "Add tag" : "Add a tag…"}
                onFocus={() => setShowSuggest(true)}
                onBlur={() => setTimeout(() => setShowSuggest(false), 160)}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && draft.trim()) addTag(draft);
                  if (e.key === "Backspace" && !draft && tags.length) removeTag(tags[tags.length - 1]);
                }}
              />
              {showSuggest && (
                <div className="ptag-pop" onMouseDown={(e) => e.preventDefault()}>
                  {TAG_SUGGESTIONS.filter(s => !tags.includes(s) && s.toLowerCase().includes(draft.toLowerCase())).slice(0, 8).map(s => (
                    <button key={s} className="ptag-pop__item" onClick={() => addTag(s)}>
                      <Icon name="plus" size={10}/> {s}
                    </button>
                  ))}
                  {draft.trim() && !TAG_SUGGESTIONS.includes(draft.trim()) && (
                    <button className="ptag-pop__item ptag-pop__item--new" onClick={() => addTag(draft)}>
                      <Icon name="sparkles" size={10}/> Create "{draft.trim()}"
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>

          {Array.isArray(file.sharedWith) && file.sharedWith.length > 0 && (
            <div className="sharedwith-block" style={{ marginTop: 10 }}>
              <div className="sharedwith-block__head">Shared with</div>
              {file.sharedWith.map((p, i) => (
                <div key={i} className="sharedwith-block__row">
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span className="sharedwith__avatar" style={{ width: 22, height: 22, fontSize: 10 }}>
                      {p.name.split(" ").map(s => s[0]).slice(0,2).join("")}
                    </span>
                    <strong>{p.name}</strong>
                  </div>
                  <span>{p.when}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="preview__section">
          <div className="kv">
            <div className="kv__row"><span className="kv__k">Type</span><span className="kv__v mono">{file.ext}</span></div>
            <div className="kv__row"><span className="kv__k">Size</span><span className="kv__v mono">{file.size}</span></div>
            <div className="kv__row"><span className="kv__k">Uploaded</span><span className="kv__v">{file.when}</span></div>
            {isImage && file.width && file.height && (
              <div className="kv__row"><span className="kv__k">Dimensions</span><span className="kv__v mono">{file.width} × {file.height}</span></div>
            )}
            {file.gps && (file.gps.place || file.gps.lat != null) && (
              <div className="kv__row">
                <span className="kv__k">Location</span>
                {/* User-facing location: show the reverse-geocoded
                    place name (e.g. "Salt Lake City, Utah") so the
                    preview reads like a human description. Coords are
                    kept on the data (file.gps.lat/lng) for map pin
                    rendering but deliberately not surfaced here —
                    nobody recognizes a file by its decimal degrees. */}
                <span className="kv__v" style={{ textAlign: "right" }}>
                  {file.gps.place || "Looking up location…"}
                </span>
              </div>
            )}
            {file.scene_label && (
              <div className="kv__row"><span className="kv__k">Scene</span><span className="kv__v">{file.scene_label}</span></div>
            )}
            {file.indoor_outdoor && (
              <div className="kv__row"><span className="kv__k">Setting</span><span className="kv__v">{file.indoor_outdoor}</span></div>
            )}
          </div>
        </div>

        <div className="preview__section">
          <div className="preview__section-label">Storage</div>
          <div className="kv">
            {file.byte_size_original != null && (
              <div className="kv__row">
                <span className="kv__k">Original size</span>
                <span className="kv__v mono">{fmtBytes(file.byte_size_original)}</span>
              </div>
            )}
            {file.byte_size_served != null && (
              <div className="kv__row">
                <span className="kv__k">Served size</span>
                <span className="kv__v mono">
                  {fmtBytes(file.byte_size_served)}
                  {file.byte_size_original ? (
                    <span style={{ color: "var(--ink-3)" }}>
                      {" "}
                      ({Math.round((1 - file.byte_size_served / file.byte_size_original) * 100)}% smaller)
                    </span>
                  ) : null}
                </span>
              </div>
            )}
            {file.codec && (
              <div className="kv__row">
                <span className="kv__k">Encoding</span>
                <span className="kv__v mono">
                  {file.codec.toUpperCase()}{file.quality ? ` · q ${file.quality}` : ""}
                </span>
              </div>
            )}
            {file.original_expires_at && (
              <div className="kv__row">
                <span className="kv__k">Original retained until</span>
                <span className="kv__v">
                  {(() => {
                    try {
                      const d = new Date(file.original_expires_at);
                      const dateStr = d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
                      const daysLeft = Math.max(0, Math.round((d.getTime() - Date.now()) / 86_400_000));
                      return `${dateStr} · ${daysLeft} ${daysLeft === 1 ? "day" : "days"} left`;
                    } catch { return file.original_expires_at; }
                  })()}
                </span>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="preview__foot">
        <button className="btn btn--secondary" style={{ flex: 1 }} onClick={handleDownload}>
          <Icon name="download" size={14}/> Download
        </button>
        <button
          className="btn-icon"
          aria-label="Move to trash"
          title="Delete"
          onClick={handleDelete}
        >
          <Icon name="trash" size={15}/>
        </button>
      </div>
    </aside>
    </React.Fragment>
  );
}

// Named export above; legacy `window.PreviewPanel` removed.
