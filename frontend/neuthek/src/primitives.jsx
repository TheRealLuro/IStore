// Reusable primitives: Modal, Switch, Checkbox, Toast.
import React, { useEffect, useRef } from "react";
import { Icon } from "./icons.jsx";

// Selector for the focusable descendants used by the focus trap below.
// Excludes anything explicitly removed from the tab order (tabindex="-1").
const FOCUSABLE_SEL =
  'a[href], button:not([disabled]), textarea:not([disabled]), ' +
  'input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Modal({ open, onClose, size = "md", children, labelledBy }) {
  // Ref to the dialog container so we can move focus into it on open and
  // keep Tab / Shift-Tab cycling within it while it's mounted.
  const dialogRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") { onClose && onClose(); return; }
      // Focus trap. Without this the dialog is just a high-z-index sibling
      // of the page chrome — the topbar search input and other background
      // controls stay in the DOM and in the tab order, so Tab / Shift-Tab
      // can walk focus OUT of the dialog and onto e.g. the global search
      // box. Typing then lands in search instead of the field the user
      // clicked (reported: "clicking password in Settings sometimes puts
      // what I type in the search bar"). Keep focus inside by wrapping at
      // the edges; if focus has already escaped, pull it back in.
      if (e.key !== "Tab") return;
      const root = dialogRef.current;
      if (!root) return;
      const nodes = Array.from(root.querySelectorAll(FOCUSABLE_SEL))
        .filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (nodes.length === 0) {
        // Nothing focusable inside — keep focus on the dialog itself
        // rather than letting it spill to the background.
        e.preventDefault();
        root.focus();
        return;
      }
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      const active = document.activeElement;
      const inside = root.contains(active);
      if (e.shiftKey) {
        if (!inside || active === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (!inside || active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // On open, move focus into the dialog if it isn't already there. This
  // both gives the trap a starting point inside the dialog and stops a
  // lingering focus on a background control (e.g. the search input the
  // user just left) from capturing the next keystrokes.
  useEffect(() => {
    if (!open) return;
    const root = dialogRef.current;
    if (!root) return;
    // Defer one frame so the dialog body (and any autoFocus child) has
    // mounted; an autoFocus input keeps its focus, otherwise we focus
    // the dialog container.
    const id = requestAnimationFrame(() => {
      if (!root.contains(document.activeElement)) {
        const firstFocusable = root.querySelector(FOCUSABLE_SEL);
        (firstFocusable || root).focus();
      }
    });
    return () => cancelAnimationFrame(id);
  }, [open]);

  if (!open) return null;
  const sizeCls = size === "lg" ? " modal--lg" : size === "xl" ? " modal--xl" : "";
  return (
    <>
      <div className="scrim" onClick={onClose}></div>
      <div
        ref={dialogRef}
        className={"modal" + sizeCls}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
      >
        {children}
      </div>
    </>
  );
}

export function ModalClose({ onClose }) {
  return (
    <button className="modal__close" onClick={onClose} aria-label="Close">
      <Icon name="x" size={16} />
    </button>
  );
}

export function Switch({ on, onChange, ariaLabel }) {
  return (
    <button
      role="switch"
      aria-checked={on}
      aria-label={ariaLabel}
      className="switch"
      data-on={on}
      onClick={() => onChange && onChange(!on)}
    />
  );
}

export function Check({ checked, onChange, label, sub }) {
  return (
    <button type="button" className="check" data-checked={checked} onClick={() => onChange && onChange(!checked)}>
      <span className="check__box"><Icon name="check" size={11} strokeWidth={2.6}/></span>
      <span className="check__label">
        {label}
        {sub && <div className="check__sub">{sub}</div>}
      </span>
    </button>
  );
}

/**
 * Collapsible section. Two modes:
 *
 *  • DEFAULT (toggle) — click the header to open / close. Closed state
 *    hides the body but keeps the chevron + section label visible so
 *    the user knows what's there. State is local. `defaultOpen` seeds
 *    the initial open state. This is the mode every NON-Settings caller
 *    uses, so the toggle + chevron path is preserved untouched.
 *
 *  • STATIC (`alwaysOpen`) — no collapse affordance at all. The header
 *    renders as a plain, non-interactive label block (a quiet uppercase
 *    eyebrow + optional count chip, NO chevron, NOT a button) and the
 *    body is ALWAYS shown. This is the mode the Settings panels use:
 *    the user asked for sections to be always-visible and organized,
 *    rather than collapsed behind a chevron. Hooks stay unconditional
 *    (the `open` state still exists, it's just forced true here) so the
 *    component obeys the rules of hooks in both modes.
 *
 * `count` (optional) renders a small numeric pill next to the
 * label, e.g. "Library maintenance · 6" — useful when the body is
 * a list of action rows and the count tells the user how much is
 * inside at a glance.
 */
export function Collapsible({ label, defaultOpen = false, count, children, id, alwaysOpen = false }) {
  const [open, setOpen] = React.useState(!!defaultOpen);
  const bodyId = id ? `${id}-body` : undefined;
  const isOpen = alwaysOpen || open;

  // Static mode: a quiet header label (no toggle, no chevron) with the
  // body always rendered. Used by Settings so every section is visible
  // and the page reads as an organized, scannable column.
  if (alwaysOpen) {
    return (
      <div className="collapsible collapsible--static" data-open="true">
        <div className="collapsible__head collapsible__head--static">
          <span className="collapsible__label">{label}</span>
          {(count !== undefined && count !== null) && (
            <span className="collapsible__count">{count}</span>
          )}
        </div>
        <div id={bodyId} className="collapsible__body">
          {children}
        </div>
      </div>
    );
  }

  // Default mode: interactive disclosure with a rotating chevron.
  return (
    <div className="collapsible" data-open={isOpen ? "true" : "false"}>
      <button
        type="button"
        className="collapsible__head"
        aria-expanded={isOpen}
        aria-controls={bodyId}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="collapsible__label">{label}</span>
        {(count !== undefined && count !== null) && (
          <span className="collapsible__count">{count}</span>
        )}
        <span className="collapsible__chev" aria-hidden="true">
          <Icon name="chevronRight" size={12}/>
        </span>
      </button>
      {isOpen && (
        <div id={bodyId} className="collapsible__body">
          {children}
        </div>
      )}
    </div>
  );
}

// Named exports above; legacy `window.Primitives` access removed.
