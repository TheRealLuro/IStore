// File preview panel — slides in from the right when a file is selected.
import React, { useState as useStateP2, useEffect as useEffectP2 } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import { AuthedThumb, AuthedImg } from "./auth-image.jsx";
import { getImagePeople, faceCropUrl } from "@/api/people";
import { deleteFile, originalUrl, fetchAsBlobUrl } from "@/api/files";

const TAG_SUGGESTIONS = [
  "Favorite", "To review", "Shared", "Archived", "Private", "WIP",
  "Work", "Personal", "Travel", "Family", "Reference", "Receipt",
];

// Star state lives in localStorage for now — backend hasn't shipped a real
// `is_starred` column yet (see todo.md). Persisting per-image-id under a
// single key is enough to make the toggle feel real across reloads.
const STAR_KEY = "neuthek.starred";
function readStars() {
  try {
    const raw = localStorage.getItem(STAR_KEY);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch { return new Set(); }
}
function writeStars(set) {
  try { localStorage.setItem(STAR_KEY, JSON.stringify(Array.from(set))); } catch {}
}

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

  // Re-seed tags + star state when the file changes. We don't roll random
  // starter tags anymore — only show what the backend actually has.
  useEffectP2(() => {
    if (!file) return;
    setTags(Array.isArray(file.tags) ? file.tags : []);
    setStarred(readStars().has(file.id));
  }, [file?.id]);

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

  const toggleStar = () => {
    if (!file) return;
    const set = readStars();
    if (set.has(file.id)) set.delete(file.id);
    else set.add(file.id);
    writeStars(set);
    setStarred(set.has(file.id));
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
      onClose && onClose();
    } catch (e) {
      toast.error(e?.detail || "Could not delete file");
    }
  };

  if (!file) return null;
  const isImage = file.type === "image";
  const isVideo = file.type === "video";
  const isDoc = file.type === "doc";

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
    <aside className="preview" aria-label="File details" onClick={(e) => e.stopPropagation()}>
      <div className="preview__head">
        <div className="preview__head-title mono">DETAILS</div>
        <div style={{ display: "flex", gap: 4 }}>
          <button
            className="btn-icon"
            aria-label={starred ? "Unstar" : "Star"}
            aria-pressed={starred}
            onClick={toggleStar}
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

      {isImage && file.thumb ? (
        <AuthedThumb url={file.thumb} className="preview__hero"
             onClick={() => setLightbox(true)} role="button" aria-label="View full size"/>
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
        {file.topic && <div className="preview__topic">
          <span className="kicker" style={{ marginRight: 8 }}><Icon name="sparkles" size={10} style={{ verticalAlign: "-1px" }}/> AI</span>
          {file.topic}
        </div>}
        <div className="preview__meta">
          <span>{file.when}</span>
          <span style={{ color: "var(--ink-4)" }}>·</span>
          <span className="mono">{file.size}</span>
          <span style={{ color: "var(--ink-4)" }}>·</span>
          <span className="mono">{file.ext}</span>
        </div>

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
                    <span className={"face-chip__name" + (labelled ? "" : " face-chip__name--unnamed")}>
                      {p.person_display_name || "Tap to name"}
                    </span>
                  </div>
                );
              })}
            </div>
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
            {file.gps && (
              <div className="kv__row">
                <span className="kv__k">Location</span>
                <span className="kv__v mono">{file.gps.lat.toFixed(4)}, {file.gps.lng.toFixed(4)}</span>
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
            <div className="kv__row"><span className="kv__k">Encrypted at rest</span><span className="kv__v"><Icon name="check" size={13} strokeWidth={2.4}/></span></div>
            <div className="kv__row"><span className="kv__k">Region</span><span className="kv__v">US-West-2</span></div>
            <div className="kv__row"><span className="kv__k">Backups</span><span className="kv__v">90 days</span></div>
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
