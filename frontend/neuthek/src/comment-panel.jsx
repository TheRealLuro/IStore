// §G2 — left-side comment panel that appears alongside the full-
// display surfaces (image lightbox, PDF modal, code modal). Closes
// implicitly when the parent surface closes. State (draft input,
// active reply) resets on file-id change so a comment in flight
// for one image doesn't leak into another.
//
// Layout intent: clean and not cluttered. One column. Each comment
// is a card (avatar + byline + relative time + body + actions).
// Replies indent one level — no deep nesting tree, just the standard
// "thread under a root" pattern most chat apps use.
//
// Hidden state when the parent surface closes: the conversation
// stays on the server. Re-opening the file in full display fetches
// the thread fresh.
import React, { useState, useEffect, useRef, useMemo } from "react";
import toast from "react-hot-toast";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Icon } from "./icons.jsx";
import {
  listComments,
  createComment,
  editComment,
  deleteComment,
} from "@/api/comments";

function relTime(iso) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  const diff = Math.max(0, (Date.now() - t) / 1000);
  if (diff < 45) return "just now";
  if (diff < 90) return "1m ago";
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 7200) return "1h ago";
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  if (diff < 172800) return "yesterday";
  if (diff < 604800) return `${Math.round(diff / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

function authorLabel(c) {
  if (c.deleted_at) return "—";
  return c.author_display_name || c.author_email?.split("@")[0] || "Former user";
}

function authorInitial(c) {
  const label = authorLabel(c);
  return label === "—" ? "·" : label.charAt(0).toUpperCase();
}

// Props:
//   open        — render at all? (controlled by parent: any full-display
//                 surface being open)
//   expanded    — render full 360px panel (true) or collapsed bubble (false)?
//                 The parent owns this so e.g. starting video playback can
//                 force the panel to collapse without losing thread state.
//   onToggleExpanded(next) — fired when the user clicks the bubble (open)
//                 or the collapse button (close).
export function CommentPanel({
  fileId,
  currentUserId,
  ownerUserId,
  open,
  expanded = true,
  onToggleExpanded,
  // When true, the COLLAPSED state renders nothing instead of the lone
  // floating bubble — used when an external tool rail (image lightbox)
  // already provides the comments bubble, so we don't show two. The
  // expanded panel is unchanged. Defaults false so every other surface
  // keeps its self-contained bubble + panel behavior.
  hideBubble = false,
}) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const [replyTo, setReplyTo] = useState(null); // {id, authorLabel}
  const [editingId, setEditingId] = useState(null);
  const [editDraft, setEditDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const composeRef = useRef(null);

  // Reset transient state when the file changes (one preview to the
  // next) so a half-typed comment on photo A doesn't bleed into B.
  useEffect(() => {
    setDraft("");
    setReplyTo(null);
    setEditingId(null);
    setEditDraft("");
  }, [fileId]);

  // Fire the query whenever the surface is open at all (bubble OR
  // expanded), so the bubble's count badge stays accurate without
  // a separate fetch. Collapsing the panel to a bubble doesn't
  // re-fetch; the cache is keyed on fileId.
  const { data: comments = [], isLoading } = useQuery({
    queryKey: ["comments", fileId],
    queryFn: () => listComments(fileId),
    enabled: !!fileId && !!open,
    staleTime: 10_000,
  });

  // Thread the flat list by parent_id. Roots in chronological order;
  // replies grouped under each root in chronological order. Replies
  // to replies (depth > 1) collapse up to depth 1 — keeps the panel
  // legible at any width.
  const threaded = useMemo(() => {
    const byParent = new Map();
    for (const c of comments) {
      const key = c.parent_id || 0;
      if (!byParent.has(key)) byParent.set(key, []);
      byParent.get(key).push(c);
    }
    for (const arr of byParent.values()) {
      arr.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
    }
    const roots = byParent.get(0) || [];

    // Walk the tree depth-first, collapsing nested replies to depth 1.
    const out = [];
    const walkReplies = (rootId) => {
      const queue = [...(byParent.get(rootId) || [])];
      while (queue.length) {
        const r = queue.shift();
        out.push({ ...r, _depth: 1 });
        // Push this reply's children onto the queue so they render
        // flat under the same root (collapsed nesting).
        const kids = byParent.get(r.id) || [];
        for (const k of kids) queue.push(k);
      }
    };
    for (const root of roots) {
      out.push({ ...root, _depth: 0 });
      walkReplies(root.id);
    }
    return out;
  }, [comments]);

  const submit = async () => {
    const body = draft.trim();
    if (!body || busy) return;
    setBusy(true);
    try {
      await createComment(fileId, {
        body,
        parent_id: replyTo?.id ?? null,
      });
      setDraft("");
      setReplyTo(null);
      qc.invalidateQueries({ queryKey: ["comments", fileId] });
    } catch (e) {
      toast.error(e?.detail || e?.message || "Could not post comment");
    } finally {
      setBusy(false);
    }
  };

  const startEdit = (c) => {
    setEditingId(c.id);
    setEditDraft(c.body);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditDraft("");
  };

  const submitEdit = async () => {
    const body = editDraft.trim();
    if (!body || busy) return;
    setBusy(true);
    try {
      await editComment(editingId, body);
      qc.invalidateQueries({ queryKey: ["comments", fileId] });
      setEditingId(null);
      setEditDraft("");
    } catch (e) {
      toast.error(e?.detail || e?.message || "Could not edit comment");
    } finally {
      setBusy(false);
    }
  };

  const removeComment = async (c) => {
    if (busy) return;
    if (!confirm("Delete this comment?")) return;
    setBusy(true);
    try {
      await deleteComment(c.id);
      qc.invalidateQueries({ queryKey: ["comments", fileId] });
    } catch (e) {
      toast.error(e?.detail || e?.message || "Could not delete comment");
    } finally {
      setBusy(false);
    }
  };

  if (!open) return null;

  // Collapsed state: a small floating bubble pinned to the left
  // edge. Click expands the panel. Count badge surfaces unread-ish
  // signal (currently total comments — once we add read-state we'll
  // wire that here instead). When `hideBubble` is set, an external rail
  // owns the bubble, so we render nothing in the collapsed state.
  if (!expanded) {
    if (hideBubble) return null;
    return (
      <button
        type="button"
        className="comment-bubble"
        onClick={(e) => {
          e.stopPropagation();
          onToggleExpanded && onToggleExpanded(true);
        }}
        aria-label={`Open comments (${comments.length})`}
        title={`Comments (${comments.length})`}
      >
        <Icon name="message" size={18} />
        {comments.length > 0 && (
          <span className="comment-bubble__badge">{comments.length}</span>
        )}
      </button>
    );
  }

  return (
    <aside
      className="comment-panel"
      onClick={(e) => e.stopPropagation()}
      aria-label="Comments"
    >
      <header className="comment-panel__head">
        <Icon name="message" size={14} />
        <span className="comment-panel__title">Comments</span>
        <span className="comment-panel__count">{comments.length}</span>
        {/* Collapse to bubble. The thread state stays in TanStack
            Query cache so reopening is instant — no refetch. */}
        <button
          type="button"
          className="btn-icon comment-panel__collapse"
          onClick={() => onToggleExpanded && onToggleExpanded(false)}
          aria-label="Collapse comments"
          title="Collapse to bubble"
        >
          <Icon name="chevronLeft" size={13} />
        </button>
      </header>

      <div className="comment-panel__list">
        {isLoading && (
          <div className="comment-panel__empty">Loading…</div>
        )}
        {!isLoading && threaded.length === 0 && (
          <div className="comment-panel__empty">
            <div className="comment-panel__empty-icon">
              <Icon name="message" size={22} strokeWidth={1.4} />
            </div>
            <div className="comment-panel__empty-title">No comments yet</div>
            <div className="comment-panel__empty-body">
              Start the conversation. Comments are visible to you and
              anyone you've shared this file with.
            </div>
          </div>
        )}
        {threaded.map((c) => {
          const canEdit = !c.deleted_at && c.user_id === currentUserId;
          const canDelete =
            !c.deleted_at &&
            (c.user_id === currentUserId || ownerUserId === currentUserId);
          const isEditing = editingId === c.id;
          return (
            <div
              key={c.id}
              className={`comment ${c._depth ? "comment--reply" : ""} ${c.deleted_at ? "comment--deleted" : ""}`}
            >
              <div className="comment__avatar">{authorInitial(c)}</div>
              <div className="comment__main">
                <div className="comment__head">
                  <span className="comment__author">{authorLabel(c)}</span>
                  <span className="comment__time" title={c.created_at}>
                    {c.deleted_at ? "deleted" : relTime(c.created_at)}
                  </span>
                </div>
                {c.deleted_at ? (
                  <div className="comment__body comment__body--deleted">
                    Comment deleted
                  </div>
                ) : isEditing ? (
                  <div className="comment__edit">
                    <textarea
                      value={editDraft}
                      onChange={(e) => setEditDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submitEdit();
                        if (e.key === "Escape") cancelEdit();
                      }}
                      rows={3}
                      autoFocus
                    />
                    <div className="comment__edit-actions">
                      <button type="button" className="btn btn--ghost btn--sm" onClick={cancelEdit}>
                        Cancel
                      </button>
                      <button
                        type="button"
                        className="btn btn--primary btn--sm"
                        onClick={submitEdit}
                        disabled={busy || !editDraft.trim()}
                      >
                        Save
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="comment__body">{c.body}</div>
                )}
                {!c.deleted_at && !isEditing && (
                  <div className="comment__actions">
                    <button
                      type="button"
                      className="comment__action"
                      onClick={() => {
                        // Reply to the ROOT, not the nested reply —
                        // matches the flat-thread model above.
                        const rootId = c._depth ? c.parent_id : c.id;
                        setReplyTo({ id: rootId, label: authorLabel(c) });
                        composeRef.current?.focus();
                      }}
                    >
                      Reply
                    </button>
                    {canEdit && (
                      <button
                        type="button"
                        className="comment__action"
                        onClick={() => startEdit(c)}
                      >
                        Edit
                      </button>
                    )}
                    {canDelete && (
                      <button
                        type="button"
                        className="comment__action comment__action--danger"
                        onClick={() => removeComment(c)}
                      >
                        Delete
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <footer className="comment-panel__compose">
        {replyTo && (
          <div className="comment-panel__reply-banner">
            <span>
              Replying to <strong>{replyTo.label}</strong>
            </span>
            <button
              type="button"
              className="btn-icon"
              onClick={() => setReplyTo(null)}
              aria-label="Cancel reply"
              title="Cancel reply"
            >
              <Icon name="x" size={11} />
            </button>
          </div>
        )}
        <textarea
          ref={composeRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
          placeholder={replyTo ? "Write a reply…" : "Add a comment…"}
          rows={3}
        />
        <div className="comment-panel__compose-actions">
          <span className="comment-panel__hint">⌘↵ to send</span>
          <button
            type="button"
            className="btn btn--primary btn--sm"
            onClick={submit}
            disabled={busy || !draft.trim()}
          >
            {replyTo ? "Reply" : "Comment"}
          </button>
        </div>
      </footer>
    </aside>
  );
}
