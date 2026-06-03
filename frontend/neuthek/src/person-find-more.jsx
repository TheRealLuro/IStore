// "Find more photos of this person" — candidate review modal (Sprint I #7 / D8).
//
// The user opens a named person and clicks "Find more photos." We call
// POST /people/{id}/find-more (instant pgvector KNN over existing face
// embeddings) and present the ranked candidate faces that were never
// auto-assigned. The user keeps the ones that are really this person and
// drops the rest, then confirms — each kept candidate is assigned via the
// existing reassignFace(face_id, personId) endpoint (PATCH /people/faces/
// {id}), which also retrains the person's centroid server-side.
//
// Visual language matches the rest of the app: a centered overlay + card
// modeled on `.facefix-modal` (see styles-find-more.css), the same
// AuthedThumb the gallery uses for protected image/crop bytes, and the
// app's monochrome buttons. Scoped entirely to `.findmore-*`.

import React, { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import { AuthedThumb } from "./auth-image.jsx";
import {
  findMorePhotosOfPerson,
  reassignFace,
  faceCropUrl,
} from "@/api/people";
import { servedUrl } from "@/api/files";

// How many confirm calls to run at once. reassignFace recomputes the
// person centroid on every call, so we keep concurrency modest to avoid
// hammering the API (and the single inference path) — the grid caps at 60
// candidates, so this drains quickly enough to feel instant.
const CONFIRM_CONCURRENCY = 4;

/** Run `tasks` (array of () => Promise) with bounded concurrency.
 *  Resolves to { ok, failed } counts. Never rejects — a single failed
 *  assign is counted, not thrown, so a partial success still updates the
 *  view for everything that landed. */
async function runBounded(tasks, limit) {
  let ok = 0;
  let failed = 0;
  let i = 0;
  async function worker() {
    while (i < tasks.length) {
      const idx = i++;
      try {
        await tasks[idx]();
        ok += 1;
      } catch {
        failed += 1;
      }
    }
  }
  const workers = [];
  for (let w = 0; w < Math.min(limit, tasks.length); w++) {
    workers.push(worker());
  }
  await Promise.all(workers);
  return { ok, failed };
}

export function FindMorePhotosModal({ personId, personName, open, onClose }) {
  const qc = useQueryClient();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [resp, setResp] = useState(null);
  // face_id -> candidate, kept locally so per-card reject can splice
  // without a refetch. Selection state is a Set of face_ids the user
  // wants to ADD (defaults to all candidates).
  const [candidates, setCandidates] = useState([]);
  const [selected, setSelected] = useState(() => new Set());
  const [confirming, setConfirming] = useState(false);

  const who = personName || "this person";

  // Fetch candidates whenever the modal opens for a person.
  useEffect(() => {
    if (!open || !personId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setResp(null);
    setCandidates([]);
    setSelected(new Set());
    findMorePhotosOfPerson(personId)
      .then((r) => {
        if (cancelled) return;
        setResp(r);
        setCandidates(r.candidates || []);
        // Default-select every candidate: the user reviews and DESELECTS
        // the wrong ones, which is faster than opting each in when the
        // KNN is already high-precision.
        setSelected(new Set((r.candidates || []).map((c) => c.face_id)));
      })
      .catch((e) => {
        if (!cancelled) setError(e?.detail || "Could not search for more photos");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, personId]);

  // Close on Escape (but not while a confirm batch is in flight — let it
  // finish so we don't leave a half-applied set without feedback).
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape" && !confirming) {
        e.stopPropagation();
        onClose?.();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, confirming, onClose]);

  const selectedCount = selected.size;
  const allSelected = candidates.length > 0 && selectedCount === candidates.length;

  const toggleOne = (faceId) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(faceId)) next.delete(faceId);
      else next.add(faceId);
      return next;
    });
  };

  const toggleAll = () => {
    setSelected((prev) =>
      prev.size === candidates.length
        ? new Set()
        : new Set(candidates.map((c) => c.face_id)),
    );
  };

  // Per-card "not this person" — drop it from the review list. A rejected
  // candidate simply stays an unlabeled face (nothing to persist); we
  // don't call the "not a person" endpoint here because the face may well
  // be a real DIFFERENT person, just not this one.
  const rejectOne = (faceId) => {
    setCandidates((prev) => prev.filter((c) => c.face_id !== faceId));
    setSelected((prev) => {
      const next = new Set(prev);
      next.delete(faceId);
      return next;
    });
  };

  const handleConfirm = async () => {
    const toAdd = candidates.filter((c) => selected.has(c.face_id));
    if (toAdd.length === 0) return;
    setConfirming(true);
    const tasks = toAdd.map(
      (c) => () => reassignFace(c.face_id, personId),
    );
    const { ok, failed } = await runBounded(tasks, CONFIRM_CONCURRENCY);
    setConfirming(false);

    if (ok > 0) {
      // Refresh everything a new assignment touches: the people list
      // (counts + this person's sample), the person-filtered gallery, the
      // facet counts, and the per-image people overlays.
      qc.invalidateQueries({ queryKey: ["people"] });
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["facets"] });
      qc.invalidateQueries({ queryKey: ["image-people"] });
    }
    if (failed === 0) {
      toast.success(
        ok === 1 ? `Added 1 photo to ${who}` : `Added ${ok} photos to ${who}`,
      );
      onClose?.();
    } else if (ok > 0) {
      toast.success(`Added ${ok}; ${failed} couldn’t be added`);
      // Keep the modal open so the user sees what's left, but drop the
      // ones that succeeded from the grid.
      const addedIds = new Set(toAdd.slice(0, ok).map((c) => c.face_id));
      setCandidates((prev) => prev.filter((c) => !addedIds.has(c.face_id)));
      setSelected((prev) => {
        const next = new Set(prev);
        for (const id of addedIds) next.delete(id);
        return next;
      });
    } else {
      toast.error("Couldn’t add those photos");
    }
  };

  if (!open) return null;

  return (
    <div
      className="findmore-modal__overlay"
      onMouseDown={(e) => {
        // Click on the dim backdrop closes (unless a batch is running).
        if (e.target === e.currentTarget && !confirming) onClose?.();
      }}
    >
      <div
        className="findmore-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="findmore-modal-title"
      >
        <header className="findmore-modal__head">
          <div className="findmore-modal__head-text">
            <div className="findmore-modal__kicker">FIND MORE PHOTOS</div>
            <div id="findmore-modal-title" className="findmore-modal__title">
              More photos of {who}
            </div>
          </div>
          <button
            type="button"
            className="findmore-modal__close"
            aria-label="Close"
            onClick={() => !confirming && onClose?.()}
            disabled={confirming}
          >
            <Icon name="x" size={16} />
          </button>
        </header>

        {/* Sub-line: what we found / are doing. */}
        <div className="findmore-modal__sub">
          {loading ? (
            <span>Searching your library…</span>
          ) : error ? (
            <span className="findmore-modal__sub--err">{error}</span>
          ) : candidates.length > 0 ? (
            <span>
              {candidates.length} likely{" "}
              {candidates.length === 1 ? "match" : "matches"} — uncheck anyone
              who isn’t {who}, then add the rest.
            </span>
          ) : resp && resp.anchor_count === 0 ? (
            <span>
              We don’t have a clear face for {who} yet. Open a photo of them
              and confirm a face first, then try again.
            </span>
          ) : (
            <span>No new matches found — {who} looks fully grouped.</span>
          )}
        </div>

        {/* Select-all toggle (only when there's something to act on). */}
        {!loading && !error && candidates.length > 0 && (
          <div className="findmore-modal__toolbar">
            <label className="findmore-modal__selectall">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={toggleAll}
                disabled={confirming}
              />
              <span>
                {allSelected ? "Deselect all" : "Select all"} ·{" "}
                {selectedCount} selected
              </span>
            </label>
            {resp && resp.unscanned_count > 0 && (
              <span className="findmore-modal__hint" title="Some photos haven’t been face-scanned yet. Run People → backfill to search them too.">
                <Icon name="info" size={12} /> {resp.unscanned_count} photo
                {resp.unscanned_count === 1 ? "" : "s"} not yet scanned
              </span>
            )}
          </div>
        )}

        {/* Candidate grid. */}
        <div className="findmore-modal__body">
          {loading ? (
            <div className="findmore-modal__loading">
              <span className="findmore-modal__spinner" aria-hidden="true" />
              <span>Matching faces…</span>
            </div>
          ) : candidates.length > 0 ? (
            <div className="findmore-grid">
              {candidates.map((c) => {
                const isSel = selected.has(c.face_id);
                const pct = Math.round((c.similarity || 0) * 100);
                return (
                  <div
                    key={c.face_id}
                    className={
                      "findmore-card" + (isSel ? " findmore-card--on" : "")
                    }
                    onClick={() => !confirming && toggleOne(c.face_id)}
                    role="button"
                    aria-pressed={isSel}
                    title={
                      isSel
                        ? "Selected — will be added"
                        : "Click to add this photo"
                    }
                  >
                    {/* Source photo thumbnail. */}
                    <div className="findmore-card__thumb">
                      <AuthedThumb
                        url={servedUrl(c.image_id, { maxDim: 320 })}
                        className="findmore-card__thumb-img"
                        placeholder={{ background: "var(--surface-3)" }}
                      />
                      {/* Face-crop chip (bottom-left) so the user can see
                          which face in the photo matched. */}
                      <AuthedThumb
                        url={faceCropUrl(c.face_id)}
                        className="findmore-card__face"
                        placeholder={{ background: "var(--surface-2)" }}
                      />
                      {/* Similarity badge (top-left). */}
                      <span className="findmore-card__score">{pct}%</span>
                      {/* Selection check (top-right). */}
                      <span
                        className={
                          "findmore-card__check" +
                          (isSel ? " findmore-card__check--on" : "")
                        }
                        aria-hidden="true"
                      >
                        {isSel ? <Icon name="check" size={13} /> : null}
                      </span>
                      {/* Reject (remove from list) — hover-reveal. */}
                      <button
                        type="button"
                        className="findmore-card__reject"
                        aria-label="Not this person — remove from results"
                        title="Not this person"
                        disabled={confirming}
                        onClick={(e) => {
                          e.stopPropagation();
                          rejectOne(c.face_id);
                        }}
                      >
                        <Icon name="x" size={12} />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            // Empty / no-anchor state — the message lives in the sub-line
            // above; here we show a calm icon panel.
            <div className="findmore-modal__empty">
              <Icon name="users" size={26} strokeWidth={1.4} />
              <div>
                {resp && resp.anchor_count === 0
                  ? "No face to match against yet."
                  : "Nothing new to add."}
              </div>
            </div>
          )}
        </div>

        {/* Footer actions. */}
        <footer className="findmore-modal__foot">
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => !confirming && onClose?.()}
            disabled={confirming}
          >
            {selectedCount > 0 ? "Cancel" : "Done"}
          </button>
          {candidates.length > 0 && (
            <button
              type="button"
              className="btn btn--primary"
              onClick={handleConfirm}
              disabled={confirming || selectedCount === 0}
            >
              {confirming ? (
                <>
                  <span
                    className="findmore-modal__spinner findmore-modal__spinner--sm"
                    aria-hidden="true"
                  />
                  Adding…
                </>
              ) : selectedCount === 0 ? (
                "Select photos to add"
              ) : (
                <>
                  <Icon name="check" size={14} /> Add {selectedCount} to {who}
                </>
              )}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}
