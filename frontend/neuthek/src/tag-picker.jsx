// §C1.6 — tag picker popover.
//
// Used from the file card menu ("Tags…"), the folder card menu, and
// the gallery's tag-filter strip. One component, three callers — they
// differ only in what `onAttach` / `onDetach` do (write to image vs.
// folder vs. push to the URL filter).
//
// Behaviour:
//   - Lists every user-owned tag with a chip tint + image/folder count.
//   - Type to filter; "Enter" on a non-matching label creates the tag
//     on the backend and immediately attaches it.
//   - Click a tag → toggle on/off for the current target.
//   - Each chip has a tiny color-picker on hover for re-tinting; the
//     change persists via PATCH /tags/{id}.
//
// All mutations go through React Query so the gallery's tag chips +
// folder chips re-render after a change without a manual reload.

import React, { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Icon } from "./icons.jsx";
import toast from "react-hot-toast";
import {
  TAG_COLORS,
  attachFolderTag,
  attachImageTag,
  createTag,
  detachFolderTag,
  detachImageTag,
  listTags,
  updateTag,
} from "@/api/tags";

// Chip tints. Each entry maps a TagColor name → CSS values. Picked
// to be readable in both light and dark themes (mid-saturation,
// contrast-tested against `var(--surface)`).
const TONE_BG = {
  gray: "rgba(150,150,150,0.18)",
  red: "rgba(248,113,113,0.20)",
  orange: "rgba(251,146,60,0.20)",
  amber: "rgba(250,204,21,0.22)",
  yellow: "rgba(234,179,8,0.20)",
  lime: "rgba(132,204,22,0.20)",
  green: "rgba(74,222,128,0.20)",
  emerald: "rgba(52,211,153,0.20)",
  teal: "rgba(45,212,191,0.20)",
  cyan: "rgba(34,211,238,0.20)",
  sky: "rgba(56,189,248,0.20)",
  blue: "rgba(96,165,250,0.20)",
  indigo: "rgba(129,140,248,0.20)",
  violet: "rgba(167,139,250,0.20)",
  purple: "rgba(192,132,252,0.22)",
  fuchsia: "rgba(232,121,249,0.20)",
  pink: "rgba(244,114,182,0.20)",
  rose: "rgba(251,113,133,0.20)",
};

export function tagChipStyle(color) {
  const bg = (color && TONE_BG[color]) || "var(--surface-3)";
  return {
    background: bg,
    color: "var(--ink)",
    fontSize: 11,
    padding: "2px 8px",
    borderRadius: 999,
    border: "1px solid var(--line)",
    display: "inline-flex",
    alignItems: "center",
    gap: 4,
    cursor: "pointer",
    whiteSpace: "nowrap",
  };
}

/**
 * @param {object} props
 * @param {{ image_id?: string, folder_id?: string }} props.target
 * @param {number[]} props.attached  ids currently on the target
 * @param {() => void} props.onClose
 * @param {() => void} props.onChanged  fired after attach/detach/create
 */
export function TagPicker({ target, attached = [], onClose, onChanged }) {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);
  const popoverRef = useRef(null);

  useEffect(() => { inputRef.current?.focus(); }, []);
  // Outside-click + Escape close. The popover is rendered in-place
  // (no portal) so the host modal's stacking context keeps it on top.
  useEffect(() => {
    const onDoc = (e) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target)) {
        onClose && onClose();
      }
    };
    const onKey = (e) => { if (e.key === "Escape") onClose && onClose(); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const { data: tags = [] } = useQuery({
    queryKey: ["tags"],
    queryFn: listTags,
    staleTime: 10_000,
  });

  const attachedSet = useMemo(() => new Set(attached), [attached]);
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return tags;
    return tags.filter((t) => t.label.toLowerCase().includes(needle));
  }, [tags, q]);

  const exactMatch = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return null;
    return tags.find((t) => t.label.toLowerCase() === needle) || null;
  }, [tags, q]);

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ["tags"] });
    qc.invalidateQueries({ queryKey: ["files"] });
    qc.invalidateQueries({ queryKey: ["folders"] });
    // Facets back the filter dropdown's Tags chip group + their
    // counts. Without this, attaching a brand-new tag persisted on
    // the file but the chip didn't appear in the filter dropdown
    // until the user reloaded the page.
    qc.invalidateQueries({ queryKey: ["facets"] });
    onChanged && onChanged();
  };

  const toggle = async (tag) => {
    if (busy) return;
    setBusy(true);
    try {
      if (attachedSet.has(tag.id)) {
        if (target.image_id) await detachImageTag(target.image_id, tag.id);
        else if (target.folder_id) await detachFolderTag(target.folder_id, tag.id);
      } else {
        if (target.image_id) await attachImageTag(target.image_id, { tag_id: tag.id });
        else if (target.folder_id) await attachFolderTag(target.folder_id, { tag_id: tag.id });
      }
      invalidateAll();
    } catch (e) {
      toast.error(e?.detail || e?.message || "Could not update tag");
    } finally {
      setBusy(false);
    }
  };

  const createAndAttach = async () => {
    if (busy) return;
    const label = q.trim();
    if (!label) return;
    setBusy(true);
    try {
      if (target.image_id) {
        await attachImageTag(target.image_id, { label });
      } else if (target.folder_id) {
        await attachFolderTag(target.folder_id, { label });
      } else {
        await createTag({ label });
      }
      setQ("");
      invalidateAll();
    } catch (e) {
      toast.error(e?.detail || e?.message || "Could not create tag");
    } finally {
      setBusy(false);
    }
  };

  const recolor = async (tag, color) => {
    try {
      await updateTag(tag.id, { color });
      invalidateAll();
    } catch (e) {
      toast.error(e?.detail || e?.message || "Could not recolor tag");
    }
  };

  return (
    <div
      ref={popoverRef}
      role="dialog"
      aria-label="Tag picker"
      style={{
        position: "absolute",
        zIndex: 200,
        width: 280,
        maxHeight: 360,
        background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: 12,
        boxShadow: "var(--shadow-2)",
        padding: 8,
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div style={{ position: "relative" }}>
        <input
          ref={inputRef}
          className="input"
          placeholder="Type to filter or create…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              if (exactMatch) toggle(exactMatch);
              else createAndAttach();
            }
          }}
          style={{ fontSize: 12.5, padding: "6px 10px" }}
        />
      </div>
      <div style={{ overflowY: "auto", display: "flex", flexDirection: "column", gap: 2 }}>
        {filtered.length === 0 && q.trim() && (
          <button
            type="button"
            className="cardmenu__item"
            onClick={createAndAttach}
            disabled={busy}
            style={{ fontSize: 12 }}
          >
            <span className="cardmenu__icon"><Icon name="plus" size={12}/></span>
            Create "{q.trim()}"
          </button>
        )}
        {filtered.map((t) => {
          const on = attachedSet.has(t.id);
          return (
            <div
              key={t.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 6px",
                borderRadius: 6,
                background: on ? "var(--surface-3)" : "transparent",
              }}
            >
              <button
                type="button"
                onClick={() => toggle(t)}
                disabled={busy}
                style={{
                  flex: 1,
                  textAlign: "left",
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  fontSize: 12.5,
                  color: "var(--ink)",
                  background: "transparent",
                  border: 0,
                  cursor: "pointer",
                  padding: 0,
                }}
                aria-pressed={on}
              >
                <span style={tagChipStyle(t.color)}>{t.label}</span>
                {(t.image_count > 0 || t.folder_count > 0) && (
                  <span style={{ fontSize: 11, color: "var(--ink-3)" }}>
                    {(t.image_count || 0) + (t.folder_count || 0)}
                  </span>
                )}
                {on && (
                  <span style={{ marginLeft: "auto", color: "var(--ink-3)" }}>
                    <Icon name="check" size={12} strokeWidth={2.6}/>
                  </span>
                )}
              </button>
              {/* Tiny color-picker — 5 chips so the popover doesn't
                  bloat. Full palette lives in the standalone Manage
                  Tags view (future). */}
              <details style={{ position: "relative" }}>
                <summary
                  style={{
                    listStyle: "none",
                    width: 14, height: 14,
                    borderRadius: "50%",
                    background: TONE_BG[t.color] || "var(--ink-4)",
                    cursor: "pointer",
                    border: "1px solid var(--line)",
                  }}
                  aria-label={`Recolor tag ${t.label}`}
                  title="Recolor"
                />
                <div
                  style={{
                    position: "absolute",
                    right: 0, top: 18,
                    display: "grid",
                    gridTemplateColumns: "repeat(6, 1fr)",
                    gap: 4,
                    padding: 6,
                    background: "var(--surface)",
                    border: "1px solid var(--line)",
                    borderRadius: 8,
                    boxShadow: "var(--shadow-2)",
                    zIndex: 5,
                  }}
                >
                  {TAG_COLORS.map((c) => (
                    <button
                      key={c}
                      type="button"
                      onClick={() => recolor(t, c)}
                      title={c}
                      style={{
                        width: 16, height: 16,
                        borderRadius: "50%",
                        background: TONE_BG[c],
                        border: c === t.color ? "2px solid var(--ink)" : "1px solid var(--line)",
                        cursor: "pointer",
                      }}
                    />
                  ))}
                  <button
                    type="button"
                    onClick={() => recolor(t, null)}
                    title="No color"
                    style={{
                      width: 16, height: 16,
                      borderRadius: "50%",
                      background: "transparent",
                      border: "1px dashed var(--ink-3)",
                      cursor: "pointer",
                    }}
                  />
                </div>
              </details>
            </div>
          );
        })}
      </div>
    </div>
  );
}
