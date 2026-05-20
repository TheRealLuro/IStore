// Reusable primitives: Modal, Switch, Checkbox, Toast.
import React, { useEffect } from "react";
import { Icon } from "./icons.jsx";

export function Modal({ open, onClose, size = "md", children, labelledBy }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") onClose && onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  const sizeCls = size === "lg" ? " modal--lg" : size === "xl" ? " modal--xl" : "";
  return (
    <>
      <div className="scrim" onClick={onClose}></div>
      <div className={"modal" + sizeCls} role="dialog" aria-modal="true" aria-labelledby={labelledBy}>
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
 * Collapsible section for Settings. Click the header to open / close.
 * Closed state hides the body but keeps the chevron + section label
 * visible so the user knows what's there. State is local — caller
 * controls the `defaultOpen` initial value; for tabs with many
 * sections, the convention is "first section open, rest closed."
 *
 * `count` (optional) renders a small numeric pill next to the
 * label, e.g. "Library maintenance · 6" — useful when the body is
 * a list of action rows and the count tells the user how much is
 * inside without expanding.
 */
export function Collapsible({ label, defaultOpen = false, count, children, id }) {
  const [open, setOpen] = React.useState(!!defaultOpen);
  const bodyId = id ? `${id}-body` : undefined;
  return (
    <div className="collapsible" data-open={open ? "true" : "false"}>
      <button
        type="button"
        className="collapsible__head"
        aria-expanded={open}
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
      {open && (
        <div id={bodyId} className="collapsible__body">
          {children}
        </div>
      )}
    </div>
  );
}

// Named exports above; legacy `window.Primitives` access removed.
