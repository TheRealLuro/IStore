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

// Named exports above; legacy `window.Primitives` access removed.
