// Gallery view + empty state + search results.
//
// Wired to the real backend through `useFiles()` — see app.jsx, which fetches
// from /images/ via React Query and passes the resulting list (mapped to the
// neuthek shape) down to GalleryView. Folders, faces, storage stats are still
// mock for now (todo.md C/E).
import React, { useState as useStateG, useMemo as useMemoG } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { Icon, initials as defaultInitials } from "./icons.jsx";
import { TAGS } from "./data.jsx";
import { getStorageUsage } from "@/api/storage";
import { listPeople, faceCropUrl } from "@/api/people";
import { listFolders, moveImageToFolder } from "@/api/folders";
import { deleteFile } from "@/api/files";
import { AuthedThumb } from "./auth-image.jsx";
import { useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { RenameFolderModal, DeleteFolderModal } from "./folder-modals.jsx";
import { EditableName } from "./nameable-chip.jsx";

// Custom MIME used for HTML5 drag-and-drop of files. The legacy frontend
// used the same key so we keep it for parity with anything else listening.
// Custom MIME used for HTML5 drag-and-drop of files. The literal value
// stays as `x-istore-image` for cross-tab compatibility with any open
// sessions still running the previous build; the brand renamed but the
// wire string is internal-only.
const DND_MIME = "application/x-istore-image";

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
        <span className="sidebar__brand-mark"><Icon name="logo" size={14} strokeWidth={1.8}/></span>
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
  image: "image", video: "video", doc: "document",
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
  const cardRef = React.useRef(null);
  const handleDelete = async () => {
    if (!window.confirm(`Move "${f.name}" to trash?`)) return;
    try {
      await deleteFile(f.id);
      toast.success(`Deleted "${f.name}"`);
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["storage"] });
      qc.invalidateQueries({ queryKey: ["geo"] });
      qc.invalidateQueries({ queryKey: ["account-trash"] });
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
            <Icon name={TYPE_ICON[f.type] || "document"} size={32} strokeWidth={1.3}/>
            <span className="mono" style={{ fontSize: 11 }}>{f.ext}</span>
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
            <button className="cardmenu__item" onClick={() => { setMenuOpen(false); onShare && onShare(f); }}>
              <span className="cardmenu__icon"><Icon name="users" size={14}/></span>Share…
            </button>
            <button className="cardmenu__item" onClick={() => setMenuOpen(false)}>
              <span className="cardmenu__icon"><Icon name="folder" size={14}/></span>Move to…
            </button>
            <button className="cardmenu__item" onClick={() => setMenuOpen(false)}>
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
    </div>
  );
}

// File-type filter chips
function TypeChips({ active, onChange, files }) {
  const types = [
    { id: "all",      label: "All",       icon: "library" },
    { id: "image",    label: "Photos",    icon: "image" },
    { id: "video",    label: "Videos",    icon: "video" },
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
  peopleFilter = null, onClearPeopleFilter,
  // Multi-select. `multiSelected` is a Set<string> of file ids; the
  // card check button toggles entries via `onMultiSelectToggle`.
  // Passing both as nullable means screens that don't want multi-
  // select (people picker, etc.) just don't pass them.
  multiSelected = null, onMultiSelectToggle,
  // "grid" (default tile cards) | "list" (compact bar rows). Driven
  // by the topbar view toggle.
  layoutMode = "grid",
}) {
  // Folders are an organizational container — they only make sense in
  // the all-files view with the "All" type pill. Specific-type pills
  // (Photos / Videos / Documents) and dedicated views (Starred / People
  // / Map / Shared / Trash) hide them so the gallery stops mixing
  // unrelated content.
  const foldersAllowed = view === "gallery" && typeFilter === "all";
  const [renameTarget, setRenameTarget] = useStateG(null);
  const [deleteTarget, setDeleteTarget] = useStateG(null);
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
  // Type-pill cross-filter (C1.3) is backend-side work — for now we just
  // list whatever the user has at this scope.
  const { data: realFolders } = useQuery({
    queryKey: ["folders", folderId ?? null],
    queryFn: () => listFolders(folderId ?? null),
    staleTime: 30_000,
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

      {!query && showPeopleStrip && typeFilter === "all" && <PeopleStrip/>}

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
        <div className="empty">
          <div className="empty__icon"><Icon name="search" size={26} strokeWidth={1.4}/></div>
          <div className="empty__title">No results for "{query}"</div>
          <div className="empty__body">Try a broader term, or search by topic — "sunset", "portrait", "lake".</div>
        </div>
      ) : (
        <>
          <div className={layoutMode === "list" ? "gallery__list" : "gallery__grid"}>
            {layoutMode === "list" ? filtered.map(f => (
              <FileRow
                key={f.id}
                f={f}
                selected={selected === f.id}
                multiSelected={!!multiSelected?.has?.(f.id)}
                onClick={() => onSelect(f)}
                onMultiSelectToggle={onMultiSelectToggle}
                onRename={onRename}
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
              />
            ))}
          </div>
          {/* Tips footer — fills the empty band that appears below the
              file grid on wide viewports / sparse libraries. Hidden
              when the user is searching (the search results page is
              its own context) and when the library is large enough
              to fill the screen on its own (>= 30 files). */}
          {!query && filtered.length < 30 && layoutMode !== "list" && (
            <GalleryTips />
          )}
        </>
      )}
    </div>
  );
}

// Quick-action footer rendered beneath the file grid when the gallery
// is sparse enough that the bottom of the viewport would otherwise be
// empty. Hidden on dense libraries (≥30 files) where the cards already
// fill the screen, on the list/bar layout (it's its own dense view),
// and on search results (the search context replaces it).
function GalleryTips() {
  const tips = [
    { icon: "upload",     title: "Upload more",      body: "Drag photos, videos, or docs onto this page." },
    { icon: "folderPlus", title: "Make a folder",    body: "Group related files for faster searching." },
    { icon: "users",      title: "Tag people",       body: "Click a face in any photo to label it." },
    { icon: "sparkles",   title: "Try AI search",    body: "Type “whiteboard math” or “sunset.”" },
  ];
  return (
    <div className="gallery__tips">
      {tips.map(t => (
        <div key={t.title} className="gallery__tip">
          <div className="gallery__tip-head">
            <span className="gallery__tip-icon"><Icon name={t.icon} size={13}/></span>
            {t.title}
          </div>
          <div className="gallery__tip-body">{t.body}</div>
        </div>
      ))}
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
