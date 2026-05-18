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
import { nameCluster, renamePerson, reassignDetection } from "@/api/people";
import { updateMe } from "@/api/auth";

// §C4.2 — when the user types "Me" but their account has no display
// name set, the server returns 422 with this structured detail. The
// FE catches it and prompts for a display name inline before
// retrying.
function isMissingDisplayName(err) {
  // Errors from the api client surface as { detail: {...} } when the
  // server returns a JSON-body 4xx. The detail can be the raw 422
  // object or the FastAPI-wrapped variant.
  const d = err?.detail;
  if (!d) return false;
  if (typeof d === "object" && d.code === "missing_display_name") return true;
  return false;
}

export function EditableName({
  // Current name (null/empty for unlabeled clusters).
  name,
  // Exactly one of these three should be set:
  detectionId, // re-target one face_detection — leaves siblings alone
  personId,    // an existing Person row → renamePerson (cascades)
  clusterId,   // unlabeled cluster → nameCluster (becomes a Person)
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
  // §C4.2 — when the rename triggers the missing-display-name 422,
  // we surface an inline prompt asking for the user's name. The
  // pendingRename remembers what they were trying to do so we can
  // retry after the display name is set.
  const [needsName, setNeedsName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [pendingRename, setPendingRename] = useState(null);
  const inputRef = useRef(null);
  const nameInputRef = useRef(null);

  useEffect(() => {
    if (needsName && nameInputRef.current) {
      nameInputRef.current.focus();
    }
  }, [needsName]);

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

  // Issue the actual server-side rename. Factored out so the
  // missing-display-name retry path can call it again after the
  // user fills in their display name.
  const runRename = async (next) => {
    if (detectionId != null) {
      await reassignDetection(detectionId, { display_name: next });
    } else if (personId != null) {
      await renamePerson(personId, next);
    } else if (clusterId != null) {
      await nameCluster(clusterId, next);
    } else {
      throw new Error("Need detectionId, personId, or clusterId");
    }
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
      await runRename(next);
      toast.success(`Saved "${next}"`);
      for (const key of invalidate) qc.invalidateQueries({ queryKey: key });
      setEditing(false);
    } catch (e) {
      if (isMissingDisplayName(e)) {
        // §C4.2 — surface the inline display-name prompt instead of
        // an opaque error. Stash the pending rename so we can retry
        // once the user fills in their name.
        setPendingRename(next);
        setNeedsName(true);
      } else {
        toast.error(
          typeof e?.detail === "string"
            ? e.detail
            : e?.detail?.message || "Could not save name",
        );
      }
    } finally {
      setBusy(false);
    }
  };

  // §C4.2 — submit the inline display-name prompt and retry the
  // original rename. Two server calls: PATCH /users/me, then the
  // original name endpoint.
  const submitDisplayName = async () => {
    const dn = nameDraft.trim();
    if (!dn) {
      toast.error("Type a name first");
      return;
    }
    setBusy(true);
    try {
      await updateMe({ display_name: dn });
      // Now re-issue the rename — "Me" will resolve server-side to
      // the display name we just set.
      await runRename(pendingRename || "Me");
      toast.success(`Welcome, ${dn}.`);
      qc.invalidateQueries({ queryKey: ["me"] });
      for (const key of invalidate) qc.invalidateQueries({ queryKey: key });
      setNeedsName(false);
      setNameDraft("");
      setPendingRename(null);
      setEditing(false);
    } catch (e) {
      toast.error(
        typeof e?.detail === "string"
          ? e.detail
          : e?.detail?.message || "Could not set display name",
      );
    } finally {
      setBusy(false);
    }
  };

  const cancelDisplayName = () => {
    setNeedsName(false);
    setNameDraft("");
    setPendingRename(null);
    setEditing(false);
  };

  // §C4.2 — display-name prompt. Shown only after the user typed
  // "Me" and the server told us there's no display name set yet.
  // Inline so the rename flow never bounces them away to settings.
  if (needsName) {
    return (
      <span
        onClick={(e) => e.stopPropagation()}
        style={{
          display: "inline-flex",
          flexDirection: "column",
          gap: 6,
          padding: 8,
          border: "1px solid var(--ink-3)",
          borderRadius: 8,
          background: "var(--surface)",
          maxWidth: 260,
        }}
      >
        <span style={{ fontSize: 12, color: "var(--ink-2)", lineHeight: 1.4 }}>
          What's your name? We'll use it instead of "Me" in AI summaries.
        </span>
        <input
          ref={nameInputRef}
          value={nameDraft}
          disabled={busy}
          onChange={(e) => setNameDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submitDisplayName();
            else if (e.key === "Escape") cancelDisplayName();
          }}
          placeholder="e.g. Jakub"
          aria-label="Your display name"
          style={{
            padding: "4px 8px",
            border: "1px solid var(--line)",
            borderRadius: 6,
            font: "inherit",
            background: "var(--surface)",
            color: "var(--ink)",
          }}
        />
        <span style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
          <button
            type="button"
            disabled={busy}
            onClick={cancelDisplayName}
            className="btn btn--ghost btn--sm"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={busy || !nameDraft.trim()}
            onClick={submitDisplayName}
            className="btn btn--primary btn--sm"
          >
            Save name
          </button>
        </span>
      </span>
    );
  }

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
