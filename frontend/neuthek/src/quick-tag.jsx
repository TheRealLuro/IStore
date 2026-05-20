// QuickTagInput — minimal popover for the gallery card "Tags…" menu
// item. The full TagPicker (search + color editor + tag list) is too
// heavy for the "I just want to slap one tag on this file" path; this
// component is one input + Enter to attach.
//
// Backend `POST /images/{id}/tags` accepts `{ label }` and either
// creates-and-attaches OR resolves an existing same-case-folded tag,
// so we don't need to round-trip /tags/ first. Existing attached tags
// render as tiny chips with an × button to detach without leaving the
// popover.
import React, { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import { attachImageTag, detachImageTag } from "@/api/tags";

/**
 * @param {object} props
 * @param {string} props.imageId   image to attach the tag to
 * @param {Array<{id:number,label:string,color?:string}>} props.attached  rows currently on the image
 * @param {() => void} props.onClose  outside-click / Escape close
 */
export function QuickTagInput({ imageId, attached = [], onClose }) {
  const qc = useQueryClient();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const popRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  // Outside-click + Escape close. We attach to `pointerdown` (not
  // `mousedown`) and stop propagation on our own popover; otherwise
  // the gallery marquee tool — which captures `pointerdown` on the
  // grid — eats the event before we get a chance to decide.
  useEffect(() => {
    const onDown = (e) => {
      if (popRef.current && !popRef.current.contains(e.target)) {
        onClose && onClose();
      }
    };
    const onKey = (e) => { if (e.key === "Escape") onClose && onClose(); };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const onAttach = async () => {
    const label = value.trim();
    if (!label || busy) return;
    setBusy(true);
    try {
      await attachImageTag(imageId, { label });
      setValue("");
      // Invalidate the gallery + file caches so the new chip shows
      // immediately on the card. We don't close on success — the user
      // can keep typing more tags without re-opening the popup.
      qc.invalidateQueries({ queryKey: ["images"] });
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["tags"] });
      // Keep focus for the next tag.
      inputRef.current?.focus();
    } catch (e) {
      toast.error(e?.detail || e?.message || "Could not attach tag");
    } finally {
      setBusy(false);
    }
  };

  const onDetach = async (tagId) => {
    if (busy) return;
    setBusy(true);
    try {
      await detachImageTag(imageId, tagId);
      qc.invalidateQueries({ queryKey: ["images"] });
      qc.invalidateQueries({ queryKey: ["files"] });
    } catch (e) {
      toast.error(e?.detail || e?.message || "Could not remove tag");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      ref={popRef}
      className="quicktag"
      // Stop pointer events from reaching the marquee tool / card
      // body. Without these, opening the popover instantly closes
      // it because the grid marquee fires on the same event.
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
      role="dialog"
      aria-label="Add tag"
      data-no-marquee="true"
    >
      {attached.length > 0 && (
        <div className="quicktag__chips">
          {attached.map((t) => (
            <span
              key={t.id}
              className="quicktag__chip"
              data-tone={t.color || undefined}
              title="Click × to remove"
            >
              {t.label}
              <button
                type="button"
                className="quicktag__chip-x"
                aria-label={`Remove ${t.label}`}
                onClick={() => onDetach(t.id)}
                disabled={busy}
              >
                <Icon name="x" size={10} strokeWidth={2.4}/>
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="quicktag__inputrow">
        <Icon name="pin" size={12}/>
        <input
          ref={inputRef}
          className="quicktag__input"
          type="text"
          placeholder="Add a tag…"
          value={value}
          maxLength={40}
          disabled={busy}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              onAttach();
            }
          }}
        />
        {value.trim() && (
          <button
            type="button"
            className="quicktag__add"
            onClick={onAttach}
            disabled={busy}
            title="Add (Enter)"
          >
            <Icon name="check" size={12} strokeWidth={2.6}/>
          </button>
        )}
      </div>
      <div className="quicktag__hint">Enter to add · Esc to close</div>
    </div>
  );
}
