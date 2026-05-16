// Inline rename for face chips (preview panel + PeopleStrip).
//
// The chip displays the current name as text; clicking switches it
// into an `<input>` you can type into. Enter / blur commits, Esc
// cancels. The component figures out whether to call `nameCluster`
// (for an unlabeled cluster) or `renamePerson` (for a row with a
// person_id) based on which IDs the caller passes in.
//
// Visual is intentionally minimal — we expose the className the caller
// uses on the existing label so the chip styling stays the same in
// both consumers.
import React, { useState, useRef, useEffect } from "react";
import toast from "react-hot-toast";
import { useQueryClient } from "@tanstack/react-query";
import { nameCluster, renamePerson } from "@/api/people";

export function EditableName({
  // Current name (null/empty for unlabeled clusters).
  name,
  // Exactly one of these two should be set:
  personId,   // an existing Person row → renamePerson
  clusterId,  // unlabeled cluster → nameCluster (becomes a Person)
  // Classes for the static + editing states. Caller controls the chip
  // shell; we only own the label / input swap.
  className,
  unnamedPlaceholder = "Tap to name",
  // React Query cache keys to invalidate after commit. Default covers
  // both consumers; callers can override.
  invalidate = [["image-people"], ["people"]],
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const begin = (e) => {
    e?.stopPropagation();
    if (busy) return;
    setDraft(name || "");
    setEditing(true);
  };

  const cancel = () => {
    setDraft("");
    setEditing(false);
  };

  const commit = async () => {
    if (busy) return;
    const next = draft.trim();
    if (!next || next === (name || "").trim()) {
      cancel();
      return;
    }
    setBusy(true);
    try {
      if (personId != null) {
        await renamePerson(personId, next);
      } else if (clusterId != null) {
        await nameCluster(clusterId, next);
      } else {
        throw new Error("Need personId or clusterId");
      }
      toast.success(`Saved "${next}"`);
      for (const key of invalidate) qc.invalidateQueries({ queryKey: key });
      setEditing(false);
    } catch (e) {
      toast.error(e?.detail || "Could not save name");
    } finally {
      setBusy(false);
    }
  };

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={draft}
        disabled={busy}
        onChange={(e) => setDraft(e.target.value)}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          else if (e.key === "Escape") cancel();
        }}
        onBlur={commit}
        className={className}
        aria-label="Name this person"
        style={{
          padding: "2px 6px",
          border: "1px solid var(--ink-3)",
          borderRadius: 6,
          font: "inherit",
          background: "var(--surface)",
          color: "var(--ink)",
          minWidth: 80,
          maxWidth: 160,
        }}
        placeholder={unnamedPlaceholder}
      />
    );
  }

  const labelled = !!name;
  return (
    <span
      className={className}
      onClick={begin}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") begin(e); }}
      title={labelled ? "Click to rename" : "Click to name this person"}
      style={{ cursor: "pointer" }}
    >
      {labelled ? name : unnamedPlaceholder}
    </span>
  );
}
