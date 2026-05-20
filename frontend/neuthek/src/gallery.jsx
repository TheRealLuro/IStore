// Gallery view + empty state + search results.
//
// Wired to the real backend through `useFiles()` — see app.jsx, which fetches
// from /images/ via React Query and passes the resulting list (mapped to the
// neuthek shape) down to GalleryView. Folders, faces, storage stats are still
// mock for now (todo.md C/E).
import React, { useState as useStateG, useMemo as useMemoG, useRef as useRefG, useEffect as useEffectG } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { Icon, initials as defaultInitials } from "./icons.jsx";
import NeuthekMark from "./NeuthekMark.jsx";
import { TAGS } from "./data.jsx";
import { getStorageUsage } from "@/api/storage";
import { listPeople, faceCropUrl } from "@/api/people";
import { listFolders, moveImageToFolder } from "@/api/folders";
import { deleteFile, originalUrl } from "@/api/files";
import { tokens } from "@/api/client";
import { eraseImageCaches } from "./cache-eraser.js";
import { AuthedThumb } from "./auth-image.jsx";
import { useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { RenameFolderModal, DeleteFolderModal } from "./folder-modals.jsx";
import { EditableName } from "./nameable-chip.jsx";
import { TagPicker, tagChipStyle } from "./tag-picker.jsx";
import { QuickTagInput } from "./quick-tag.jsx";
import { ShareModal } from "./share-modal.jsx";
import { fileTypeInfo } from "./file-types.js";
import { API_BASE_URL } from "@/api/client";

/** Fetch the folder-zip endpoint with a Bearer token, drop the
 *  resulting blob into a hidden `<a download>`, click it, revoke.
 *  Centralized so the FolderCard menu + any future bulk-action can
 *  share one code path. */
async function downloadFolderAsZip(folder) {
  if (!folder?.id) return;
  toast.success(`Building ${folder.name || "folder"}.zip…`);
  // Direct navigation download. The previous fetch+blob+createObjectURL
  // path required buffering the ENTIRE zip in browser memory before
  // the user got a save dialog — for a folder with multi-GB videos
  // the tab would stall or OOM and the user saw "doesn't work."
  //
  // Direct navigation hands the response to the browser's native
  // download manager, which streams bytes to disk. The session
  // cookie rides along automatically on the top-level GET because
  // it's set with SameSite=Lax — Lax allows cookies on top-level
  // navigations even when the request comes from a different
  // top-level frame (just not on cross-origin POST/iframe).
  //
  // We open in a HIDDEN iframe rather than `window.location.href = ...`
  // because the latter would replace the current page if the response
  // sets `Content-Disposition: attachment` slowly — the iframe
  // approach keeps the user on the gallery while the download runs.
  try {
    const url = `${API_BASE_URL}/folders/${folder.id}/download.zip`;
    // Quick auth pre-check so we surface 401/404 as a toast instead
    // of dumping the user into a silent failed iframe. HEAD would be
    // cheaper but the backend doesn't implement HEAD for zip — a
    // GET with `Range: bytes=0-0` only fetches one byte before the
    // browser drops the connection.
    let legacy = null;
    try { legacy = localStorage.getItem("neuthek.jwt"); } catch {}
    const probe = await fetch(url, {
      method: "GET",
      credentials: "include",
      headers: {
        Range: "bytes=0-0",
        ...(legacy ? { Authorization: `Bearer ${legacy}` } : {}),
      },
    });
    if (!probe.ok && probe.status !== 206) {
      let detail = "Download failed";
      try { detail = (await probe.json())?.detail || detail; } catch {}
      toast.error(detail);
      return;
    }
    // Abort the probe immediately — we only needed the status code.
    try { probe.body?.cancel(); } catch {}
    // Hand to the native download manager. A hidden iframe means the
    // browser handles the streaming + save dialog without taking the
    // user off the page.
    const iframe = document.createElement("iframe");
    iframe.style.display = "none";
    iframe.src = url;
    document.body.appendChild(iframe);
    setTimeout(() => iframe.remove(), 60_000);
    return;
  } catch (e) {
    toast.error("Download failed");
  }
}

// Custom MIME used for HTML5 drag-and-drop of files. Internal-only —
// the SAME bundle reads and writes it; no other tab / extension /
// downstream system consumes the value. Renamed to `x-neuthek-image`
// alongside the project-wide istore→neuthek rebrand. A tab open
// across the deploy will lose drag-and-drop once until reload, which
// is acceptable for a single-author self-hosted app.
const DND_MIME = "application/x-neuthek-image";

export function Sidebar({ view, onView, onUpload, onAccount, attentionCount = 0, onAttention, user, counts }) {
  const u = user || { name: "Alex Rivera", email: "alex@example.com" };
  const initialsFn = defaultInitials;
  // Wired counts (image/video/document) come from the file query; people /
  // map / shared / trash counts are still mock until those features wire up.
  const c = counts || {};
  const navItems = [
    { group: "LIBRARY", items: [
      { id: "gallery", label: "All files",   icon: "library", count: c.all ?? 0 },
      { id: "photos",  label: "Photos",      icon: "image",   count: c.image ?? 0 },
      { id: "videos",  label: "Videos",      icon: "video",   count: c.video ?? 0 },
      { id: "docs",    label: "Documents",   icon: "document",count: c.document ?? 0 },
    ]},
    { group: "VIEWS", items: [
      { id: "starred", label: "Starred",     icon: "star",    count: c.starred ?? 0 },
      { id: "people",  label: "People",      icon: "users",   count: c.people ?? 0 },
      { id: "places",  label: "Map",         icon: "map",     count: c.geo ?? 0 },
      // Shared has no backend yet (todo.md G1) — keep the row visible
      // so the navigation shape doesn't shift when sharing ships, but
      // never show a fake count.
      { id: "shared",  label: "Shared",      icon: "share",   count: c.shared ?? 0 },
      { id: "trash",   label: "Trash",       icon: "trash",   count: c.trash ?? 0 },
    ]},
  ];

  // Real storage usage from /storage/usage. Gated on the real `user` prop
  // (not the mock fallback `u`) so the query doesn't fire when the parent
  // renders Sidebar before auth is settled.
  const { data: usage } = useQuery({
    queryKey: ["storage"],
    queryFn: getStorageUsage,
    enabled: !!user?.email,
    staleTime: 60_000,
  });
  const used = usage?.used_bytes ?? 0;
  const quota = usage?.quota_bytes ?? 200 * 1024 * 1024 * 1024;
  const fmt = (n) => {
    if (!n && n !== 0) return "0 GB";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    if (n < 1024 ** 3) return (n / 1024 / 1024).toFixed(1) + " MB";
    if (n < 1024 ** 4) return (n / 1024 ** 3).toFixed(1) + " GB";
    return (n / 1024 ** 4).toFixed(2) + " TB";
  };
  const pct = quota > 0 ? Math.min(100, (used / quota) * 100) : 0;
  // The bar is a two-tier render: outer track represents the quota, an
  // inner fill spans `pct%` and visually represents how much of quota is
  // used. The categories live as segments inside the fill, scaled to
  // their share of *used* (so they fill the inner container without
  // creating an illusion of quota usage).
  const segOfUsed = (cat) => {
    const bytes = usage?.by_category?.[cat] ?? 0;
    return used > 0 ? Math.max(0, (bytes / used) * 100) : 0;
  };
  // For very-small-but-nonzero usage, show a sliver so the visual is not
  // visually indistinguishable from "0%". 1 px of width at common bar
  // sizes ≈ 0.5%, so 0.6 is a reasonable floor.
  const visualPct = used > 0 ? Math.max(0.6, pct) : 0;

  return (
    <nav className="sidebar" aria-label="Primary">
      <div className="sidebar__brand">
        <span className="sidebar__brand-mark" style={{ background: "transparent", padding: 0 }}>
          <NeuthekMark size={20}/>
        </span>
        neuthek
      </div>

      <button className="btn btn--primary" style={{ margin: "0 6px 4px", width: "calc(100% - 12px)" }} onClick={onUpload}>
        <Icon name="upload" size={14}/> Upload
      </button>

      {navItems.map(g => (
        <div className="side-section" key={g.group}>
          <div className="side-section__label">{g.group}</div>
          {g.items.map(it => (
            <button key={it.id} className="side-link" data-active={view === it.id} onClick={() => onView(it.id)}>
              <Icon name={it.icon} size={15} className="side-link__icon"/>
              <span>{it.label}</span>
              {it.count > 0 && <span className="side-link__count">{it.count}</span>}
            </button>
          ))}
        </div>
      ))}

      <div className="side-bottom">
        {attentionCount > 0 && (
          <button className="attention" onClick={onAttention}
                  title="Open Settings → Privacy">
            <span className="attention__dot"/>
            <span>
              <strong>
                {attentionCount} {attentionCount === 1 ? "setting" : "settings"}
              </strong>{" "}
              {attentionCount === 1 ? "needs" : "need"} your attention
            </span>
          </button>
        )}
        <div className="storage">
          <div className="storage__head">
            <span><strong>{fmt(used)}</strong> of {fmt(quota)}</span>
            <span>{pct < 1 && used > 0 ? "<1" : pct.toFixed(0)}%</span>
          </div>
          <div className="storage__bar">
            <div style={{
              width: visualPct + "%",
              height: "100%",
              display: "flex",
              gap: 1,
            }}>
              <div className="storage__bar-seg"            style={{ width: segOfUsed("image") + "%" }}/>
              <div className="storage__bar-seg" data-tone="2" style={{ width: segOfUsed("video") + "%" }}/>
              <div className="storage__bar-seg" data-tone="3" style={{ width: segOfUsed("document") + "%" }}/>
              <div className="storage__bar-seg" data-tone="4" style={{ width: segOfUsed("other") + "%" }}/>
            </div>
          </div>
          <div className="storage__legend">
            <div className="storage__legend-item">Photos</div>
            <div className="storage__legend-item" data-tone="2">Videos</div>
            <div className="storage__legend-item" data-tone="3">Docs</div>
            <div className="storage__legend-item" data-tone="4">Other</div>
          </div>
        </div>
        <button className="user-pill" onClick={onAccount}>
          <span className="user-pill__avatar">{initialsFn(u.name)}</span>
          <div style={{ flex: 1, minWidth: 0, textAlign: "left" }}>
            <div className="user-pill__name">{u.name}</div>
            <div className="user-pill__email mono">{u.email}</div>
          </div>
          <Icon name="settings" size={14} className="user-pill__icon"/>
        </button>
      </div>
    </nav>
  );
}

function PeopleStrip({ onPerson }) {
  // Real people only — no mock fallback. When consent isn't granted or no
  // faces have been clustered yet, the strip simply doesn't render.
  const { data } = useQuery({
    queryKey: ["people"],
    queryFn: listPeople,
    staleTime: 60_000,
  });
  const named = (data?.persons || []).map(p => ({
    id: "p" + p.id,
    personId: p.id,
    clusterId: null,
    name: p.display_name,
    img: p.sample_face_id ? faceCropUrl(p.sample_face_id) : null,
    count: p.face_count,
  }));
  const unnamed = (data?.unlabeled_clusters || []).map(c => ({
    id: "c" + c.cluster_id,
    personId: null,
    clusterId: c.cluster_id,
    name: null,
    img: faceCropUrl(c.sample_face_id),
    count: c.face_count,
  }));
  const faces = [...named, ...unnamed];
  if (faces.length === 0) return null;
  return (
    <div className="people">
      {faces.map(f => (
        <div className="person" key={f.id}>
          <button
            type="button"
            onClick={() => onPerson && onPerson(f)}
            style={{ background: "none", border: 0, padding: 0, cursor: "pointer" }}
            aria-label={f.name ? `View ${f.name}` : "View this cluster"}
          >
            {!f.img ? (
              <div className="person__avatar" style={{ background: "var(--surface-2)" }}/>
            ) : (
              <AuthedThumb
                url={f.img}
                className="person__avatar"
                placeholder={{ background: "var(--surface-2)" }}
              />
            )}
          </button>
          <EditableName
            name={f.name}
            personId={f.personId}
            clusterId={f.clusterId}
            className={"person__name" + (f.name ? "" : " person__name--unnamed")}
            unnamedPlaceholder="Unnamed"
            invalidate={[["people"]]}
          />
        </div>
      ))}
    </div>
  );
}

// icon for non-image file types
const TYPE_ICON = {
  image: "image", video: "video", doc: "document", code: "code",
  contact: "users", password: "shield", gamesave: "game", iot: "wifi",
};

// Compact list-view row used by the bar layout. Shows just the bits a
// user actually scans for in dense lists: type icon, name, AI topic
// (when present), modified date, size. Same multi-select check as the
// card so toggling between layouts doesn't lose the user's selection.
function FileRow({ f, selected, multiSelected, onClick, onMultiSelectToggle, onRename }) {
  return (
    <div
      className="filerow"
      data-selected={selected}
      data-multi={multiSelected}
      data-card={f.id}
      onClick={onClick}
      onDoubleClick={(e) => { e.stopPropagation(); onRename && onRename(f); }}
      title="Click to preview · double-click to rename"
    >
      <button
        type="button"
        className="filerow__check"
        aria-label={multiSelected ? "Deselect" : "Select"}
        aria-pressed={!!multiSelected}
        data-on={!!multiSelected}
        onClick={(e) => {
          e.stopPropagation();
          onMultiSelectToggle && onMultiSelectToggle(f.id);
        }}
      >
        <Icon name="check" size={11} strokeWidth={2.6}/>
      </button>
      <div className="filerow__icon" data-type={f.type}>
        <Icon name={TYPE_ICON[f.type] || "document"} size={14} strokeWidth={1.6}/>
      </div>
      <div className="filerow__name">{f.name}</div>
      <div className="filerow__topic">
        {f.topic ? (
          <>
            <span className="kicker" style={{ marginRight: 6, fontSize: 9 }}>AI</span>
            {f.topic}
          </>
        ) : f.pendingSummary ? (
          <span className="skel skel--text" style={{ width: 140, display: "inline-block" }}/>
        ) : null}
      </div>
      <div className="filerow__when">{f.when}</div>
      <div className="filerow__size mono">{f.size}</div>
      <div className="filerow__type mono">{f.ext}</div>
    </div>
  );
}

function FileCard({ f, selected, onClick, query, onRename, onShare, multiSelected, onMultiSelectToggle }) {
  const qc = useQueryClient();
  const [menuOpen, setMenuOpen] = useStateG(false);
  // §C1.6 — tag picker anchored to the menu's "Tags…" row. We track
  // visibility separately from `menuOpen` so picking a tag doesn't
  // collapse the cardmenu under it.
  const [tagPickerOpen, setTagPickerOpen] = useStateG(false);
  const cardRef = React.useRef(null);
  const handleDelete = async () => {
    if (!window.confirm(`Move "${f.name}" to trash?`)) return;
    try {
      await deleteFile(f.id);
      toast.success(`Deleted "${f.name}"`);
      // §A5 — purge every FE cache + blob handle for the deleted id
      // so a stale-while-revalidate render can't briefly show it.
      await eraseImageCaches(qc, [f.id]);
    } catch (e) {
      toast.error(e?.detail || "Could not delete file");
    }
  };
  const isImage = f.type === "image";
  const highlight = (text) => {
    if (!query || !text) return text;
    const re = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig");
    return text.split(re).map((part, i) =>
      part.toLowerCase() === query.toLowerCase()
        ? <mark key={i}>{part}</mark>
        : part
    );
  };
  React.useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e) => { if (!cardRef.current?.contains(e.target)) setMenuOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [menuOpen]);
  const tagDefs = TAGS;
  const isShared = (f.tags || []).includes("shared");
  const sharedNames = (f.sharedWith || (isShared ? [
    { name: "Léa Bonneau" }, { name: "Marcus Reid" }
  ] : []));
  return (
    <div ref={cardRef} className="card" data-selected={selected} data-multi={multiSelected}
         data-card={f.id}
         onClick={(e) => {
           // The check circle at the top-left of the thumb owns its own
           // click handler; if it stopPropagation's, we never get here.
           // Plain card body click → open the preview, same as before.
           onClick && onClick(e);
         }}
         draggable
         onDragStart={(e) => {
           e.dataTransfer.effectAllowed = "move";
           e.dataTransfer.setData(DND_MIME, f.id);
         }}
         onDoubleClick={(e) => { e.stopPropagation(); onRename && onRename(f); }}
         title="Double-click to rename · drag onto a folder to move">
      <AuthedThumb
        url={f.thumb || null}
        className={"card__thumb" + (isImage || f.thumb ? "" : " card__thumb--doc")}
        placeholder={{ background: "var(--surface-2)" }}
      >
        {!f.thumb && (
          <div className="thumb-icon">
            {/* Prefer the file-type catalog (extension → glyph) so
                video/audio/CSV/ICS/VCF files render the right icon
                even when the backend has them grouped under "doc". */}
            <Icon name={fileTypeInfo(f.ext).icon || TYPE_ICON[f.type] || "document"} size={32} strokeWidth={1.3}/>
            <span className="mono" style={{ fontSize: 11 }}>{fileTypeInfo(f.ext).label || f.ext}</span>
          </div>
        )}
        {/* Real multi-select toggle. Sits inside the thumb so it floats
            over the image; the button stops propagation so clicking it
            doesn't also fire the card's preview-open handler. The
            outer card carries `data-multi="true"` while selected so
            the CSS can paint the persistent check + outline. */}
        <button
          type="button"
          className="card__check"
          aria-label={multiSelected ? "Deselect" : "Select"}
          aria-pressed={!!multiSelected}
          data-on={!!multiSelected}
          onClick={(e) => {
            e.stopPropagation();
            onMultiSelectToggle && onMultiSelectToggle(f.id);
          }}
        >
          <Icon name="check" size={12} strokeWidth={2.6}/>
        </button>
        <div className="card__type">{f.ext}</div>
        <button className="card__menu-btn"
                data-open={menuOpen}
                data-doc={!isImage}
                onClick={(e) => { e.stopPropagation(); setMenuOpen(o => !o); }}
                aria-label="Quick actions">
          <Icon name="menu" size={14} strokeWidth={2}/>
        </button>
      </AuthedThumb>
      {menuOpen && (
        <div className="cardmenu" onClick={(e) => e.stopPropagation()}>
          <div className="cardmenu__group">
            <button className="cardmenu__item" onClick={() => { setMenuOpen(false); onRename && onRename(f); }}>
              <span className="cardmenu__icon"><Icon name="edit" size={14}/></span>Rename<span className="cardmenu__shortcut">F2</span>
            </button>
            <button
              className="cardmenu__item"
              onClick={(e) => {
                // Stop bubble — the card's outer onClick would otherwise
                // fire after we close the menu, opening the preview as
                // soon as Share is clicked.
                e.stopPropagation();
                setMenuOpen(false);
                onShare && onShare(f);
              }}
            >
              <span className="cardmenu__icon"><Icon name="users" size={14}/></span>Share…
            </button>
            <button
              className="cardmenu__item"
              onClick={(e) => {
                e.stopPropagation();
                setMenuOpen(false);
                setTagPickerOpen(true);
              }}
            >
              <span className="cardmenu__icon"><Icon name="pin" size={14}/></span>Tags…
            </button>
            <button
              className="cardmenu__item"
              onClick={async () => {
                setMenuOpen(false);
                // Wire Download to the real /images/:id/original endpoint.
                // JWT is required, so we fetch the bytes via authed XHR and
                // hand the user a blob: URL on an <a download>. Avoids the
                // need for a query-param token + signed-URL handshake.
                try {
                  // Cookie auth — credentials include sends the
                  // HttpOnly session cookie. Legacy Bearer kept for
                  // users mid-migration; will go away after we ship
                  // a cookie variant of the TOTP login.
                  let legacy = null;
                  try { legacy = localStorage.getItem("neuthek.jwt"); } catch {}
                  const res = await fetch(originalUrl(f.id), {
                    credentials: "include",
                    headers: legacy ? { Authorization: `Bearer ${legacy}` } : {},
                  });
                  if (!res.ok) throw new Error(await res.text() || "download failed");
                  const blob = await res.blob();
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = f.name || `file-${f.id}`;
                  document.body.appendChild(a);
                  a.click();
                  a.remove();
                  setTimeout(() => URL.revokeObjectURL(url), 1000);
                } catch (err) {
                  toast.error(err?.message || "Could not download");
                }
              }}
            >
              <span className="cardmenu__icon"><Icon name="download" size={14}/></span>Download
            </button>
          </div>
          <div className="cardmenu__sep"/>
          <button className="cardmenu__item cardmenu__item--danger"
                  onClick={() => { setMenuOpen(false); handleDelete(); }}>
            <span className="cardmenu__icon"><Icon name="trash" size={14}/></span>Delete<span className="cardmenu__shortcut">⌫</span>
          </button>
        </div>
      )}
      <div className="card__body">
        <div className="card__name">{highlight(f.name)}</div>
        <div className="card__meta">
          <span>{f.when}</span>
          <span className="card__meta-dot">·</span>
          <span className="mono">{f.size}</span>
        </div>
        {(f.tags || []).length > 0 && (
          <div className="tag-row">
            {(f.tags || []).map(tagId => {
              const def = tagDefs.find(t => t.id === tagId);
              if (!def) return null;
              return <span key={tagId} className="tag" data-tone={def.tone}>{def.label}</span>;
            })}
          </div>
        )}
        {isShared && sharedNames.length > 0 && (
          <div className="sharedwith">
            <span className="sharedwith__avatars">
              {sharedNames.slice(0, 3).map((p, i) => (
                <span key={i} className="sharedwith__avatar">
                  {p.name.split(" ").map(s => s[0]).slice(0,2).join("")}
                </span>
              ))}
            </span>
            <span>
              Shared with {sharedNames[0].name.split(" ")[0]}
              {sharedNames.length > 1 ? ` +${sharedNames.length - 1}` : ""}
            </span>
          </div>
        )}
        {query && f.topic && (
          <div className="card__summary">
            <span className="kicker" style={{ marginRight: 6, fontSize: 9 }}>AI</span>
            {highlight(f.topic)}
          </div>
        )}
        {/* When summarization is still queued/running for a file, show a
            shimmer placeholder in the same slot the AI topic occupies so
            the gallery clearly signals "this card is waiting on AI" —
            instead of looking like a card whose summary silently failed. */}
        {f.pendingSummary && !f.topic && (
          <div className="card__summary" aria-label="Generating summary">
            <span className="kicker" style={{ marginRight: 6, fontSize: 9 }}>AI</span>
            <span className="skel skel--text" style={{ width: "70%", display: "inline-block", verticalAlign: "middle" }}/>
          </div>
        )}
      </div>

      {/* Quick-tag popover. Lighter than the full TagPicker — just one
          input + Enter to attach, with existing tags shown as
          dismissable chips. Anchored top-right near the menu button.
          The popup uses pointerdown (not mousedown) for outside-click
          AND stops propagation on its own pointer events so the
          gallery marquee tool doesn't eat the open click. */}
      {tagPickerOpen && (
        <div
          className="quicktag-anchor"
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
          data-no-marquee="true"
        >
          <QuickTagInput
            imageId={f.id}
            attached={f.tagRows || []}
            onClose={() => setTagPickerOpen(false)}
          />
        </div>
      )}

    </div>
  );
}

// File-type filter chips
function TypeChips({ active, onChange, files }) {
  // Order: real backend categories first (Photos / Videos / Audio /
  // Code / Documents), then forward-looking mock categories. The
  // chip self-hides when no file of that type exists in the current
  // batch (count === 0), so users on a docs-only library don't
  // see Photos / Videos chips at all.
  //
  // 2026-05 — added `audio` + `code` chips. Audio files used to fall
  // through to "doc" in the frontend mapping; code files used to
  // share the document icon. Both are now distinct types.
  const types = [
    { id: "all",      label: "All",       icon: "library" },
    { id: "image",    label: "Photos",    icon: "image" },
    { id: "video",    label: "Videos",    icon: "video" },
    { id: "audio",    label: "Audio",     icon: "audio" },
    { id: "code",     label: "Code",      icon: "code" },
    { id: "doc",      label: "Documents", icon: "document" },
    { id: "contact",  label: "Contacts",  icon: "users" },
    { id: "password", label: "Vaults",    icon: "shield" },
    { id: "gamesave", label: "Game saves",icon: "game" },
    { id: "iot",      label: "Home data", icon: "wifi" },
  ];
  return (
    <div className="typechips">
      {types.map(t => {
        const count = t.id === "all" ? files.length : files.filter(f => f.type === t.id).length;
        if (t.id !== "all" && count === 0) return null;
        return (
          <button key={t.id} className="typechip" data-active={active === t.id}
                  onClick={() => onChange(t.id)}>
            <Icon name={t.icon} size={12}/>
            {t.label}
            <span className="typechip__count">{count}</span>
          </button>
        );
      })}
    </div>
  );
}

function FolderCard({ folder, onEnter, onRequestRename, onRequestDelete }) {
  const qc = useQueryClient();
  const [menuPos, setMenuPos] = useStateG(null); // { left, top } in viewport coords
  const [hover, setHover] = useStateG(false);
  const ref = React.useRef(null);
  const btnRef = React.useRef(null);

  // Close on outside click / scroll / resize. The menu lives in a portal,
  // so contains() needs to walk both the card and the menu element. We
  // tag the menu via data-folder-menu-for so it's easy to identify.
  React.useEffect(() => {
    if (!menuPos) return;
    const onDoc = (e) => {
      const insideCard = ref.current?.contains(e.target);
      const insideMenu = e.target.closest?.(`[data-folder-menu-for="${folder.id}"]`);
      if (!insideCard && !insideMenu) setMenuPos(null);
    };
    const onClose = () => setMenuPos(null);
    document.addEventListener("mousedown", onDoc);
    window.addEventListener("scroll", onClose, true);
    window.addEventListener("resize", onClose);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      window.removeEventListener("scroll", onClose, true);
      window.removeEventListener("resize", onClose);
    };
  }, [menuPos, folder.id]);

  const openMenu = (e) => {
    e.stopPropagation();
    if (menuPos) { setMenuPos(null); return; }
    const r = btnRef.current?.getBoundingClientRect();
    if (!r) return;
    // Anchor the menu's top-right corner just below the action button so
    // it never overflows the card horizontally and never gets clipped by
    // ancestor stacking contexts (which was the original layering bug).
    // We pin via `right` (distance from viewport right edge) instead of
    // `left` + transform — the .cardmenu CSS animation defines its own
    // transform, which would override anything we set inline.
    setMenuPos({ right: window.innerWidth - r.right, top: r.bottom + 4 });
  };

  const onDragOver = (e) => {
    if (e.dataTransfer.types.includes(DND_MIME)) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      setHover(true);
    }
  };
  const onDragLeave = () => setHover(false);
  const onDrop = async (e) => {
    e.preventDefault();
    setHover(false);
    const id = e.dataTransfer.getData(DND_MIME);
    if (!id) return;
    try {
      await moveImageToFolder(id, folder.id);
      toast.success(`Moved to "${folder.name}"`);
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["folders"] });
    } catch (err) {
      toast.error(err?.detail || "Could not move file");
    }
  };

  return (
    <div
      ref={ref}
      className="fcard"
      data-drophover={hover ? "true" : "false"}
      // `data-card` opts the folder out of the gallery marquee tool —
      // before this attribute landed, mousedown on a folder card
      // started a selection-rectangle drag and the click never
      // resolved to onEnter, so single-clicking a folder felt broken.
      data-card={`folder-${folder.id}`}
      onClick={() => onEnter?.(folder)}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      style={{
        cursor: "pointer",
        outline: hover ? "2px solid var(--ink-2)" : "none",
        outlineOffset: 2,
      }}
    >
      <div className="fcard__icon"><Icon name="folder" size={18}/></div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="fcard__name">{folder.name}</div>
        <div className="fcard__meta">
          {folder.item_count ?? 0} {((folder.item_count ?? 0) === 1) ? "item" : "items"}
          {folder.subfolder_count > 0 && (
            <> · {folder.subfolder_count} {folder.subfolder_count === 1 ? "folder" : "folders"}</>
          )}
        </div>
      </div>
      <button
        ref={btnRef}
        className="btn-icon"
        aria-label="Folder actions"
        onClick={openMenu}
        style={{ width: 28, height: 28 }}
      >
        <Icon name="moreH" size={14}/>
      </button>
      {menuPos && createPortal(
        <div
          className="cardmenu"
          data-folder-menu-for={folder.id}
          style={{
            position: "fixed",
            right: menuPos.right,
            top: menuPos.top,
            zIndex: 9999,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="cardmenu__group">
            <button className="cardmenu__item" onClick={() => { setMenuPos(null); onEnter?.(folder); }}>
              <span className="cardmenu__icon"><Icon name="arrowRight" size={14}/></span>Open
            </button>
            <button className="cardmenu__item" onClick={() => { setMenuPos(null); onRequestRename?.(folder); }}>
              <span className="cardmenu__icon"><Icon name="pencil" size={14}/></span>Rename
            </button>
            <button
              className="cardmenu__item"
              onClick={() => {
                setMenuPos(null);
                // Authed zip download. fetchMediaBlob attaches the
                // Bearer token + decodes the response into a blob URL
                // we can hand to a hidden <a download>. The server
                // streams the archive so very large folders don't
                // block the API event loop.
                downloadFolderAsZip(folder).catch(() => {});
              }}
            >
              <span className="cardmenu__icon"><Icon name="download" size={14}/></span>Download as .zip
            </button>
          </div>
          <div className="cardmenu__sep"/>
          <button
            className="cardmenu__item cardmenu__item--danger"
            onClick={() => { setMenuPos(null); onRequestDelete?.(folder); }}
          >
            <span className="cardmenu__icon"><Icon name="trash" size={14}/></span>Delete
          </button>
        </div>,
        document.body,
      )}
    </div>
  );
}

export function GalleryView({
  files, query, sort, sortDir = "desc", onSelect, selected,
  showPeopleStrip = true, showFolders = true,
  typeFilter = "all", onTypeFilter, onRename,
  folderId = null, onEnterFolder,
  view = "gallery",
  peopleFilter = null, onClearPeopleFilter, onPersonPick,
  // Multi-select. `multiSelected` is a Set<string> of file ids; the
  // card check button toggles entries via `onMultiSelectToggle`.
  // Passing both as nullable means screens that don't want multi-
  // select (people picker, etc.) just don't pass them.
  multiSelected = null, onMultiSelectToggle,
  // "grid" (default tile cards) | "list" (compact bar rows). Driven
  // by the topbar view toggle.
  layoutMode = "grid",
}) {
  // §C1.3 — folders show in the all-files view AND under the
  // image/video/document type pills (each filtered by
  // contains_type so only folders carrying that kind survive).
  // Dedicated views (Starred / People / Map / Shared / Trash) and
  // mock-only type pills (contact / password / gamesave / iot) still
  // hide folders so the gallery doesn't mix unrelated content.
  // Whitelist of REAL (server-emitted) categories. When the user
  // selects one of these, we still show folders alongside the
  // filtered file list — selecting a mock-only category (contact /
  // password / etc.) hides folders because the backend doesn't
  // produce those types.
  const REAL_TYPE_FILTERS = new Set(["all", "image", "video", "audio", "code", "doc"]);
  const foldersAllowed = view === "gallery" && REAL_TYPE_FILTERS.has(typeFilter);
  const [renameTarget, setRenameTarget] = useStateG(null);
  const [deleteTarget, setDeleteTarget] = useStateG(null);
  // Hoisted share target. Before this lived inside FileCard, where the
  // modal's overlay click bubbled up to the card's onClick handler →
  // preview opened → preview's OWN ShareModal opened underneath. The
  // user saw a flicker between two stacked modals. Living up here, the
  // modal renders OUTSIDE every card's DOM tree, so card-level click
  // handlers don't fire on its overlay.
  const [shareTarget, setShareTarget] = useStateG(null);
  const filtered = useMemoG(() => {
    let list = files;
    if (typeFilter !== "all") list = list.filter(f => f.type === typeFilter);
    // When a search query is active the upstream already supplied
    // server-ranked semantic hits (CLIP cosine + FTS over summary,
    // topic, points, filename) — we preserve that order and only
    // re-apply the type-pill filter on top. The previous client-side
    // substring filter has been retired: it dropped legitimate semantic
    // matches like "classroom" → a whiteboard photo whose summary
    // mentions "lecture room", which the server's CLIP pass surfaces
    // correctly.
    // Search-result ordering (when `query` is set) is server-ranked by
    // CLIP cosine + FTS — preserve it. Otherwise apply the topbar
    // sort key + direction.
    if (!query) {
      const dir = sortDir === "asc" ? 1 : -1;
      const cmp = (() => {
        if (sort === "name") {
          return (a, b) => a.name.localeCompare(b.name) * dir;
        }
        if (sort === "size") {
          return (a, b) => ((a.byte_size_served ?? 0) - (b.byte_size_served ?? 0)) * dir;
        }
        // Default "recent" — server sends newest-first; multiply by
        // dir so the "asc" toggle reverses it without re-fetching.
        return (a, b) => {
          const at = a.uploaded_at ? Date.parse(a.uploaded_at) : 0;
          const bt = b.uploaded_at ? Date.parse(b.uploaded_at) : 0;
          return (at - bt) * dir;
        };
      })();
      list = [...list].sort(cmp);
    }
    return list;
  }, [files, query, sort, sortDir, typeFilter]);

  // Real folders for the current scope (root or inside a folder).
  // §C1.3 — when the type pill is set to a category that matches a
  // backend `Image.category` value (image / video / document), pass it
  // as `?contains_type=…` so the backend hides folders whose subtree
  // doesn't carry any of the selected file type. The mock-only chips
  // (contact / password / gamesave / iot) currently have no matching
  // files in storage so we skip the filter for them — the FE-side
  // empty state surfaces that they're a preview.
  const FE_TYPE_TO_BACKEND = { image: "image", video: "video", doc: "document" };
  const containsType = FE_TYPE_TO_BACKEND[typeFilter] || null;
  const { data: realFolders } = useQuery({
    // Cache-key includes the contains_type so flipping the type pill
    // refetches a fresh listing instead of serving the previous one.
    queryKey: ["folders", folderId ?? null, containsType],
    queryFn: () => listFolders(folderId ?? null, containsType),
    staleTime: 30_000,
    enabled: foldersAllowed,
  });
  const visibleFolders = useMemoG(() => {
    return (realFolders || []).map(fo => ({
      id: fo.id,
      name: fo.name,
      count: fo.item_count,
      item_count: fo.item_count,
      subfolder_count: fo.subfolder_count ?? 0,
      when: "—",
      types: [],
    }));
  }, [realFolders]);

  // todo.md section E hasn't shipped yet — these asset types only exist in
  // the mock dataset right now. We surface a banner so users (and dev-mode
  // viewers) know the chip is a preview, not real data.
  const MOCK_TYPES = new Set(["contact", "password", "gamesave", "iot"]);
  const isMockType = MOCK_TYPES.has(typeFilter);

  return (
    <div className="gallery">
      {peopleFilter?.personId && (
        <div style={{
          margin: "0 0 14px",
          padding: "8px 12px",
          display: "flex", alignItems: "center", gap: 10,
          background: "var(--surface)",
          border: "1px solid var(--line)",
          borderRadius: 12,
          fontSize: 13,
        }}>
          <Icon name="users" size={13}/>
          <span>Photos of <strong>{peopleFilter.name || "this person"}</strong></span>
          <span style={{ flex: 1 }}/>
          <button
            type="button"
            onClick={() => onClearPeopleFilter && onClearPeopleFilter()}
            className="btn btn--ghost btn--sm"
          >
            <Icon name="x" size={11}/> Back to People
          </button>
        </div>
      )}
      <TypeChips active={typeFilter} onChange={onTypeFilter || (()=>{})} files={files}/>

      {isMockType && (
        <div
          role="note"
          style={{
            margin: "0 0 14px",
            padding: "10px 14px",
            border: "1px dashed var(--line)",
            borderRadius: 12,
            background: "var(--surface)",
            color: "var(--ink-2)",
            fontSize: 12.5,
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <Icon name="info" size={13} />
          <span>
            <strong>Preview only.</strong> {typeFilter === "contact" ? "Contacts"
              : typeFilter === "password" ? "Password vaults"
              : typeFilter === "gamesave" ? "Game saves"
              : "Home telemetry"}{" "}
            aren't wired to the backend yet — these rows are mock data while we
            build out the multi-asset platform (todo.md section E).
          </span>
        </div>
      )}

      {!query && showPeopleStrip && typeFilter === "all" && (
        <PeopleStrip onPerson={onPersonPick}/>
      )}

      {!query && showFolders && foldersAllowed && visibleFolders.length > 0 && (
        <div style={{ marginBottom: 18 }}>
          <div className="kicker" style={{ marginBottom: 10 }}>Folders</div>
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
            gap: 10
          }}>
            {visibleFolders.map(fo => (
              <FolderCard
                key={fo.id}
                folder={fo}
                onEnter={onEnterFolder}
                onRequestRename={setRenameTarget}
                onRequestDelete={setDeleteTarget}
              />
            ))}
          </div>
        </div>
      )}
      <RenameFolderModal
        open={!!renameTarget}
        folder={renameTarget}
        onClose={() => setRenameTarget(null)}
      />
      <DeleteFolderModal
        open={!!deleteTarget}
        folder={deleteTarget}
        onClose={() => setDeleteTarget(null)}
      />

      {!query && <div className="kicker" style={{ marginBottom: 10 }}>Files</div>}

      {filtered.length === 0 ? (
        query ? (
          <div className="empty">
            <div className="empty__icon"><Icon name="search" size={26} strokeWidth={1.4}/></div>
            <div className="empty__title">No results for "{query}"</div>
            <div className="empty__body">Try a broader term, or search by topic — "sunset", "portrait", "lake".</div>
          </div>
        ) : (
          // No search active + no files at this scope. If the scope has
          // folders (already rendered above), don't fall through to a
          // big "search" hero — a quiet one-line note keeps the page
          // calm. The gallery's folder strip is the focal point.
          <div style={{ padding: "20px 4px", fontSize: 12.5, color: "var(--ink-3)" }}>
            No individual files here yet — open a folder above, or drop files anywhere on the page to upload.
          </div>
        )
      ) : (
        <MarqueeGrid
          layoutMode={layoutMode}
          filtered={filtered}
          selected={selected}
          multiSelected={multiSelected}
          onSelect={onSelect}
          onMultiSelectToggle={onMultiSelectToggle}
          onRename={onRename}
          onShare={setShareTarget}
          query={query}
        />
      )}
      {/* Hoisted ShareModal — lives outside every card's DOM tree so
          its overlay click can't bubble into a card's onClick handler.
          Same `<ShareModal>` preview.jsx uses; one canonical surface. */}
      {shareTarget && (
        <ShareModal
          imageId={shareTarget.id}
          imageName={shareTarget.name}
          onClose={() => setShareTarget(null)}
        />
      )}
    </div>
  );
}

// Drag-rectangle ("marquee") multi-select.
//
// Perf-sensitive: the first version called getBoundingClientRect on
// every card on every pointermove and forced a React re-render per
// id toggled, which thrashed the gallery on 100+ card grids. This
// rewrite is built around three constraints:
//
//   1. Snapshot every card's rect ONCE at dragstart. Cards don't
//      move during a drag, so re-measuring them per frame is wasted
//      work. The cache keys off `data-card`.
//   2. Throttle pointermove to one rAF tick. Native pointermove can
//      fire 1000 Hz on some hardware; we only care about per-frame
//      visual state.
//   3. Paint the rubber-band rectangle by mutating a DOM ref
//      directly. The selection diff still flows through React (so
//      cards re-render with `data-multi`), but the rectangle's
//      width/height/position bypass setState — that single change
//      took the per-move cost from ~12 ms to ~0.5 ms on a 200-card
//      grid.
function MarqueeGrid({
  layoutMode, filtered, selected, multiSelected, onSelect, onMultiSelectToggle, onRename, onShare, query,
}) {
  const gridRef = useRefG(null);
  const rectElRef = useRefG(null);
  const dragRef = useRefG(null);

  useEffectG(() => {
    const grid = gridRef.current;
    if (!grid) return;
    let rafId = 0;
    let scrollRafId = 0;
    let lastClientX = 0;
    let lastClientY = 0;

    // The grid lives inside a scrollable container (usually the
    // gallery main column). Walk up until we find one with a real
    // scrollable axis so auto-scroll targets the right element.
    // Falls back to `window` when the page itself scrolls.
    const findScroller = () => {
      let n = grid.parentElement;
      while (n) {
        const cs = getComputedStyle(n);
        const sy = cs.overflowY;
        if ((sy === "auto" || sy === "scroll") && n.scrollHeight > n.clientHeight) {
          return n;
        }
        n = n.parentElement;
      }
      return window;
    };

    // The drag-start zone is broader than just the grid itself —
    // listening only on `.gallery__grid` meant the user had to
    // pixel-aim into the strips between cards. We bind to the
    // closest `.gallery` ancestor (or fall back to the scroller),
    // which covers the type chips' row gap, the empty space below
    // the last card, the padding around the grid, etc.
    //
    // The dragstart filter inside `onDown` still excludes cards,
    // buttons, inputs, chips, and the type/filter strips so we
    // don't hijack their click handlers.
    const findDragZone = () => {
      let n = grid.parentElement;
      while (n) {
        if (n.classList && n.classList.contains("gallery")) return n;
        n = n.parentElement;
      }
      // No .gallery wrapper? fall back to the scroller. (Window is
      // not a valid EventTarget candidate for our purposes since
      // it'd also catch sidebar / header clicks.)
      const sc = findScroller();
      return sc === window ? grid : sc;
    };
    const dragZone = findDragZone();

    const showRect = (rect) => {
      const el = rectElRef.current;
      if (!el) return;
      if (!rect) { el.style.display = "none"; return; }
      el.style.display = "block";
      el.style.transform = `translate(${rect.l}px, ${rect.t}px)`;
      el.style.width = rect.w + "px";
      el.style.height = rect.h + "px";
    };

    const measureCards = () => {
      // Cache each card's bounding box in DOCUMENT coordinates —
      // not viewport-relative — so the math survives a scroll
      // mid-drag without re-measuring. We add scrollY/scrollX of
      // the chosen scroller. Includes a soft inflate (`inset: -2`)
      // so a drag that barely grazes a card still picks it up.
      const box = grid.getBoundingClientRect();
      const scroller = findScroller();
      const sx = scroller === window ? window.scrollX : scroller.scrollLeft;
      const sy = scroller === window ? window.scrollY : scroller.scrollTop;
      const baseLeft = box.left + sx;
      const baseTop = box.top + sy;
      const out = [];
      grid.querySelectorAll("[data-card]").forEach((card) => {
        const id = card.getAttribute("data-card");
        if (!id) return;
        const cb = card.getBoundingClientRect();
        out.push({
          id,
          l: cb.left + sx - baseLeft - 2,
          t: cb.top + sy - baseTop - 2,
          r: cb.left + cb.width + sx - baseLeft + 2,
          b: cb.top + cb.height + sy - baseTop + 2,
        });
      });
      return { box, scroller, baseLeft, baseTop, cards: out };
    };

    // Convert a pointer event's clientX/Y → coordinates in the grid's
    // document space (so they stay valid across page scrolls).
    const ptrToGridCoords = (clientX, clientY, scroller, baseLeft, baseTop) => {
      const sx = scroller === window ? window.scrollX : scroller.scrollLeft;
      const sy = scroller === window ? window.scrollY : scroller.scrollTop;
      return { x: clientX + sx - baseLeft, y: clientY + sy - baseTop };
    };

    // Auto-scroll loop — runs continuously while the pointer sits
    // within `EDGE_PX` of the viewport top/bottom. Speed ramps up
    // the closer the pointer is to the edge. Uses scrollBy on the
    // chosen scroller so the page or an internal scroll container
    // both work. Re-fires computeFrame after each scroll tick so the
    // selection rectangle (drawn in document coords) re-projects
    // through the pointer's last known position and the cards that
    // scrolled into the rectangle get picked up.
    const EDGE_PX = 60;
    const MAX_SPEED = 24;
    const tickAutoScroll = () => {
      scrollRafId = 0;
      const drag = dragRef.current;
      if (!drag) return;
      const vh = window.innerHeight;
      const yTop = lastClientY;
      const yBot = vh - lastClientY;
      let dy = 0;
      if (yTop < EDGE_PX) {
        dy = -Math.ceil(((EDGE_PX - yTop) / EDGE_PX) * MAX_SPEED);
      } else if (yBot < EDGE_PX) {
        dy = Math.ceil(((EDGE_PX - yBot) / EDGE_PX) * MAX_SPEED);
      }
      if (dy !== 0) {
        const sc = drag.scroller;
        if (sc === window) window.scrollBy(0, dy);
        else sc.scrollTop += dy;
        // Re-run the selection math against the new scroll position.
        // We synthesize a "move" using the last clientX/Y so cards
        // that scrolled into the rectangle get picked up even though
        // the pointer hasn't moved.
        scheduleCompute();
      }
      if (yTop < EDGE_PX || yBot < EDGE_PX) {
        scrollRafId = requestAnimationFrame(tickAutoScroll);
      }
    };

    const onDown = (e) => {
      if (e.button !== 0) return;
      const target = e.target;
      // Wider exclusion list — covers everything in the gallery
      // header (type chips, filter strip, search, sort toolbar)
      // so clicking on those buttons / chips / dropdowns doesn't
      // accidentally start a marquee drag. The data-no-marquee
      // attribute is an escape hatch for any custom widget that
      // wants to opt out without enumerating it here.
      if (target.closest(
        "[data-card], [data-no-marquee], button, a, input, textarea, " +
        "select, label, [contenteditable], [role='switch'], [role='menu'], " +
        ".chip, .tag, .pill, .filter-strip, .type-chips, .toolbar, " +
        "details, summary"
      )) return;
      // Suppress the marquee while ANY popup is open. Without this
      // check, clicking OUTSIDE a popup (to close it) would also
      // start a marquee drag — the user sees a selection rectangle
      // appear when they were trying to dismiss the popup. The
      // popup's own outside-click handler still runs on the same
      // pointerdown and dismisses it; we just skip starting the
      // drag so the UX feels right.
      if (document.querySelector('[role="dialog"][data-no-marquee="true"]')) return;
      e.preventDefault();
      dragZone.setPointerCapture?.(e.pointerId);
      const { box, scroller, baseLeft, baseTop, cards } = measureCards();
      const { x: startX, y: startY } = ptrToGridCoords(e.clientX, e.clientY, scroller, baseLeft, baseTop);
      const additive = !!(e.shiftKey || e.metaKey || e.ctrlKey);
      const initial = new Set(multiSelected ? Array.from(multiSelected) : []);
      dragRef.current = {
        startX, startY, additive, initial, lastIds: new Set(),
        box, scroller, baseLeft, baseTop, cards,
        active: false,
      };
      lastClientX = e.clientX;
      lastClientY = e.clientY;
      // Global drag class — disables text selection across the
      // whole page (sidebar, header, anywhere the pointer crosses)
      // and switches the cursor to a crosshair so it's obvious
      // what mode we're in. Clears any text that was already
      // selected when the drag started.
      document.body.classList.add("dragging-marquee");
      try { window.getSelection?.()?.removeAllRanges(); } catch {}
    };

    const scheduleCompute = () => {
      if (!rafId) rafId = requestAnimationFrame(computeFrame);
    };

    const computeFrame = () => {
      rafId = 0;
      const drag = dragRef.current;
      if (!drag) return;
      const { startX, startY, scroller, baseLeft, baseTop, cards } = drag;
      const { x, y } = ptrToGridCoords(lastClientX, lastClientY, scroller, baseLeft, baseTop);
      const l = Math.min(startX, x);
      const t = Math.min(startY, y);
      const w = Math.abs(x - startX);
      const h = Math.abs(y - startY);
      if (!drag.active && w < 4 && h < 4) return;
      drag.active = true;
      showRect({ l, t, w, h });

      // Intersect against cached card rects — pure JS math, no DOM
      // reads, no getBoundingClientRect. Cards stay in document
      // coords so this works the same whether or not the page
      // scrolled.
      const want = new Set();
      const r = l + w;
      const b = t + h;
      for (const c of cards) {
        if (c.r < l || c.l > r || c.b < t || c.t > b) continue;
        want.add(c.id);
      }
      const last = drag.lastIds;
      for (const id of want) {
        if (!last.has(id)) {
          if (!(drag.additive && drag.initial.has(id))) {
            onMultiSelectToggle && onMultiSelectToggle(id);
          }
        }
      }
      for (const id of last) {
        if (!want.has(id)) {
          if (!(drag.additive && drag.initial.has(id))) {
            onMultiSelectToggle && onMultiSelectToggle(id);
          }
        }
      }
      drag.lastIds = want;
    };

    const onMove = (e) => {
      if (!dragRef.current) return;
      lastClientX = e.clientX;
      lastClientY = e.clientY;
      scheduleCompute();
      // If the pointer entered the edge zone, kick off auto-scroll.
      // The rAF guard means we never stack multiple loops.
      const vh = window.innerHeight;
      if ((e.clientY < EDGE_PX || vh - e.clientY < EDGE_PX) && !scrollRafId) {
        scrollRafId = requestAnimationFrame(tickAutoScroll);
      }
    };

    const onUp = () => {
      dragRef.current = null;
      if (rafId) cancelAnimationFrame(rafId);
      rafId = 0;
      if (scrollRafId) cancelAnimationFrame(scrollRafId);
      scrollRafId = 0;
      showRect(null);
      document.body.classList.remove("dragging-marquee");
    };

    dragZone.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      dragZone.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      if (rafId) cancelAnimationFrame(rafId);
      if (scrollRafId) cancelAnimationFrame(scrollRafId);
      document.body.classList.remove("dragging-marquee");
    };
  }, [multiSelected, onMultiSelectToggle]);

  return (
    <div
      ref={gridRef}
      className={layoutMode === "list" ? "gallery__list" : "gallery__grid"}
      style={{ position: "relative", userSelect: "none", WebkitUserSelect: "none" }}
    >
      {layoutMode === "list" ? filtered.map(f => (
        <FileRow
          key={f.id}
          f={f}
          selected={selected === f.id}
          multiSelected={!!multiSelected?.has?.(f.id)}
          onClick={() => onSelect(f)}
          onMultiSelectToggle={onMultiSelectToggle}
          onRename={onRename}
          onShare={onShare}
        />
      )) : filtered.map(f => (
        <FileCard
          key={f.id}
          f={f}
          query={query}
          selected={selected === f.id}
          multiSelected={!!multiSelected?.has?.(f.id)}
          onClick={() => onSelect(f)}
          onMultiSelectToggle={onMultiSelectToggle}
          onRename={onRename}
          onShare={onShare}
        />
      ))}
      {/* Marquee rectangle. Painted via direct DOM mutation
          (`ref.style.transform`) so dragging across 200 cards doesn't
          force a React re-render per pointermove tick. `display: none`
          is the resting state so the unused rectangle never paints. */}
      <div
        ref={rectElRef}
        aria-hidden="true"
        style={{
          display: "none",
          position: "absolute",
          left: 0,
          top: 0,
          width: 0,
          height: 0,
          willChange: "transform, width, height",
          background: "color-mix(in oklab, var(--accent, #5b8def) 16%, transparent)",
          border: "1.5px solid var(--accent, #5b8def)",
          borderRadius: 6,
          boxShadow: "0 0 0 1px color-mix(in oklab, var(--accent, #5b8def) 35%, transparent)",
          pointerEvents: "none",
          zIndex: 5,
        }}
      />
    </div>
  );
}


export function EmptyGallery({ onUpload }) {
  return (
    <div className="empty">
      <div className="empty__icon"><Icon name="cloud" size={32} strokeWidth={1.3}/></div>
      <div className="empty__title">Welcome to neuthek</div>
      <div className="empty__body">
        Drop in your first photos, videos or documents. Everything is encrypted at rest.
        AI features stay off until you turn them on.
      </div>
      <div className="empty__actions">
        <button className="btn btn--primary btn--lg" onClick={onUpload}>
          <Icon name="upload" size={14}/> Upload your first files
        </button>
        <button className="btn btn--secondary btn--lg">
          <Icon name="folderPlus" size={14}/> New folder
        </button>
      </div>
      <div className="empty__divider">Or get started</div>
      <div style={{
        display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10,
        textAlign: "left"
      }}>
        {[
          { i: "camera", t: "Connect device", d: "Auto-import from your phone." },
          { i: "users", t: "Enable People", d: "Group photos by face. Optional." },
          { i: "share", t: "Invite a friend", d: "Share a folder securely." },
        ].map((it, i) => (
          <div key={i} style={{
            padding: 16, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14
          }}>
            <Icon name={it.i} size={18} strokeWidth={1.5} style={{ color: "var(--ink-2)" }}/>
            <div style={{ fontSize: 13, fontWeight: 500, marginTop: 8 }}>{it.t}</div>
            <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 4, lineHeight: 1.45 }}>{it.d}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Named exports above; legacy `window.GalleryParts` removed.
