// VCF / vCard viewer — parses one or more VCARD blocks and renders
// them as clean contact cards. Supports vCard 2.1 + 3.0 + 4.0 line
// folding, basic property parameters (`TEL;TYPE=CELL:…`), and the
// most common fields (FN, N, ORG, TITLE, TEL, EMAIL, ADR, URL,
// NOTE, BDAY, PHOTO).
//
// Embedded base64 photos render inline; external URI photos render
// as a `<img>` direct src. Falls back to a circle initials avatar
// when no photo is present.
//
// Layout is a proper contact card: a large avatar (photo or initials),
// the full name as a heading, an org/title subline, then one labelled
// row per datum (phone / email / address / url / birthday) each with a
// small monochrome line-icon and a copy-to-clipboard button. Multiple
// vCards in one file stack as a clean list.
import React, { useState, useEffect, useMemo, useCallback } from "react";
import { initials, Icon } from "./icons.jsx";
import toast from "react-hot-toast";
import { fetchMediaBlob, originalMediaUrl } from "@/api/files";
import { safeHref } from "./safe-href.js";
import { ViewerSkeleton, ViewerError, ViewerEmpty, CopyButton } from "./viewer-states.jsx";

function unfold(text) {
  return text.replace(/\r?\n[ \t]/g, "");
}

function unescape(s) {
  if (typeof s !== "string") return "";
  return s.replace(/\\n/gi, "\n").replace(/\\,/g, ",").replace(/\\;/g, ";").replace(/\\\\/g, "\\");
}

// Title-case a raw vCard TYPE token ("CELL" → "Cell", "WORK" → "Work").
function prettyLabel(t) {
  if (!t) return "";
  return t.charAt(0).toUpperCase() + t.slice(1).toLowerCase();
}

// Exported so the vault's import-from-file flow (VLT-7) can reuse the exact
// same vCard parser when mapping an uploaded .vcf into a structured Contact
// vault item — no second, drifting parser.
export function parseVcf(text) {
  const lines = unfold(text).split(/\r?\n/);
  const cards = [];
  let cur = null;
  for (const line of lines) {
    if (!line) continue;
    if (/^BEGIN:VCARD$/i.test(line)) { cur = { phones: [], emails: [], addrs: [], urls: [] }; continue; }
    if (/^END:VCARD$/i.test(line)) { if (cur) cards.push(cur); cur = null; continue; }
    if (!cur) continue;
    const colon = line.indexOf(":");
    if (colon === -1) continue;
    const left = line.slice(0, colon);
    const value = line.slice(colon + 1);
    const parts = left.split(";");
    const name = parts.shift().toUpperCase();
    const params = {};
    for (const p of parts) {
      const eq = p.indexOf("=");
      if (eq === -1) params[p.toUpperCase()] = true;
      else params[p.slice(0, eq).toUpperCase()] = p.slice(eq + 1);
    }
    const labelOf = () => {
      const t = params.TYPE;
      if (!t) return "";
      if (typeof t === "string") return prettyLabel(t.split(",")[0]);
      return prettyLabel(Object.keys(params).find((k) => k !== "ENCODING" && k !== "MEDIATYPE" && k !== "PREF") || "");
    };
    if (name === "FN") cur.fn = unescape(value);
    else if (name === "N") {
      const [family, given] = value.split(";");
      cur.n = { family: unescape(family || ""), given: unescape(given || "") };
    }
    else if (name === "ORG") cur.org = unescape(value.split(";").filter(Boolean).join(" · "));
    else if (name === "TITLE") cur.title = unescape(value);
    else if (name === "TEL") cur.phones.push({ value, label: labelOf() });
    else if (name === "EMAIL") cur.emails.push({ value, label: labelOf() });
    else if (name === "URL") cur.urls.push({ value, label: labelOf() });
    else if (name === "ADR") {
      // ADR fields: po-box;ext;street;locality;region;postcode;country
      const f = value.split(";").map(unescape);
      const pretty = [f[2], f[3], f[4], f[5], f[6]].filter(Boolean).join(", ");
      if (pretty) cur.addrs.push({ value: pretty, label: labelOf() });
    }
    else if (name === "BDAY") cur.bday = value;
    else if (name === "NOTE") cur.note = unescape(value);
    else if (name === "PHOTO") {
      const enc = (params.ENCODING || "").toUpperCase();
      const media = params.MEDIATYPE || params.TYPE || "image/jpeg";
      if (enc === "B" || enc === "BASE64") {
        cur.photo = `data:${media};base64,${value}`;
      } else if (/^https?:/i.test(value)) {
        cur.photo = value;
      } else if (value.startsWith("data:")) {
        cur.photo = value;
      }
    }
  }
  return cards;
}

// Pretty-print a vCard BDAY (which is usually `YYYYMMDD` or
// `YYYY-MM-DD`, sometimes `--MMDD` with no year) for display.
function fmtBday(raw) {
  if (!raw) return raw;
  const m = String(raw).match(/^(?:(\d{4})|-{1,2})-?(\d{2})-?(\d{2})$/);
  if (!m) return raw;
  const [, y, mo, d] = m;
  const month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(mo) - 1];
  if (!month) return raw;
  return y ? `${month} ${Number(d)}, ${y}` : `${month} ${Number(d)}`;
}

// ---- small monochrome line-icons (house style: 24 viewBox, stroke
// currentColor, 1.6 weight). Kept local so we don't have to touch the
// shared icons module for the few glyphs it lacks (phone/mail/globe…).
const G = ({ children, size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {children}
  </svg>
);
const PhoneIcon = (p) => <G {...p}><path d="M6.5 3h3l1.5 4.5-2 1.5a12 12 0 0 0 5 5l1.5-2L20 17.5v3a1.5 1.5 0 0 1-1.6 1.5A16.5 16.5 0 0 1 3 6.6 1.5 1.5 0 0 1 4.5 5z"/></G>;
const MailIcon = (p) => <G {...p}><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m4 7 8 6 8-6"/></G>;
const GlobeIcon = (p) => <G {...p}><circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18z"/></G>;
const PinIcon = (p) => <G {...p}><path d="M12 21s-7-7.5-7-12a7 7 0 0 1 14 0c0 4.5-7 12-7 12z"/><circle cx="12" cy="9" r="2.5"/></G>;
const GiftIcon = (p) => <G {...p}><rect x="3" y="8" width="18" height="13" rx="1.5"/><path d="M3 12h18"/><path d="M12 8v13"/><path d="M12 8C12 5 10.5 3.5 9 3.5S6.5 5 8 6.5 12 8 12 8z"/><path d="M12 8c0-3 1.5-4.5 3-4.5S17.5 5 16 6.5 12 8 12 8z"/></G>;
const NoteIcon = (p) => <G {...p}><path d="M5 4h14v12l-4 4H7a2 2 0 0 1-2-2z"/><path d="M15 20v-4h4"/><path d="M8 9h8"/><path d="M8 13h5"/></G>;

function ContactRow({ icon, label, href, text, copy, unsafe }) {
  return (
    <li className="vcf-row">
      <span className="vcf-row__icon">{icon}</span>
      <div className="vcf-row__body">
        {href ? (
          <a className="vcf-row__val" href={href} target={href.startsWith("http") ? "_blank" : undefined} rel="noreferrer noopener">{text}</a>
        ) : (
          <span className="vcf-row__val" title={unsafe ? "Link uses an unsafe scheme and was disabled" : undefined}>{text}</span>
        )}
        {label && <span className="vcf-row__label">{label}</span>}
      </div>
      <CopyButton text={copy ?? text} title="Copy" />
    </li>
  );
}

export function VcfViewer({ fileId, fileName }) {
  const [text, setText] = useState(null);
  const [err, setErr] = useState(null);
  // Bumped by Retry to re-run the effect.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setText(null);
    setErr(null);
    (async () => {
      try {
        const blob = await fetchMediaBlob(originalMediaUrl(fileId));
        const body = await blob.text();
        if (!cancelled) setText(body);
      } catch (e) {
        if (cancelled) return;
        setErr(e?.message || "Could not load file");
        toast.error("Couldn't load contact card");
      }
    })();
    return () => { cancelled = true; };
  }, [fileId, attempt]);

  const cards = useMemo(() => (text ? parseVcf(text) : null), [text]);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  return (
    <div className="vcf-viewer" onClick={(e) => e.stopPropagation()}>
      <div className="vcf-viewer__head">
        <span className="vw-head__icon"><Icon name="contact" size={15} /></span>
        <span className="vcf-viewer__name">{fileName}</span>
        <span className="vcf-viewer__meta mono">
          {cards ? `${cards.length} contact${cards.length === 1 ? "" : "s"}` : ""}
        </span>
      </div>
      <div className="vcf-viewer__body">
        {err ? (
          <ViewerError
            title="Couldn't load contact card"
            message={err}
            onRetry={retry}
            downloadUrl={originalMediaUrl(fileId)}
            downloadName={fileName}
          />
        ) : !cards ? (
          <ViewerSkeleton lines={[52, "60%", "44%", "50%", "38%"]} className="vcf-skel" />
        ) : cards.length === 0 ? (
          <ViewerEmpty icon="contact" title="No contacts found" message="This vCard file didn't contain any readable contact entries." />
        ) : (
          <ul className="vcf-viewer__list">
            {cards.map((c, i) => {
              const fullName = c.fn || [c.n?.given, c.n?.family].filter(Boolean).join(" ") || "(no name)";
              const sub = [c.title, c.org].filter(Boolean).join(" · ");
              return (
                <li key={i} className="vcf-card">
                  <div className="vcf-card__top">
                    <div className="vcf-card__avatar">
                      {c.photo ? (
                        <img src={c.photo} alt={fullName} />
                      ) : (
                        <span>{initials(fullName)}</span>
                      )}
                    </div>
                    <div className="vcf-card__heading">
                      <div className="vcf-card__name">{fullName}</div>
                      {sub && <div className="vcf-card__org">{sub}</div>}
                    </div>
                  </div>

                  <ul className="vcf-card__rows">
                    {c.phones.map((p, j) => (
                      <ContactRow key={`p${j}`} icon={<PhoneIcon />} label={p.label}
                                  href={`tel:${p.value.replace(/\s+/g, "")}`} text={p.value} />
                    ))}
                    {c.emails.map((e, j) => (
                      <ContactRow key={`e${j}`} icon={<MailIcon />} label={e.label}
                                  href={`mailto:${e.value}`} text={e.value} />
                    ))}
                    {c.addrs.map((a, j) => (
                      <ContactRow key={`a${j}`} icon={<PinIcon />} label={a.label}
                                  href={`https://maps.google.com/?q=${encodeURIComponent(a.value)}`} text={a.value} />
                    ))}
                    {c.urls.map((u, j) => {
                      // Scheme allow-list: a vcard with `URL:javascript:…`
                      // would otherwise execute on click and exfiltrate the
                      // JWT from localStorage. `safeHref` returns null for
                      // anything that isn't http/https/mailto/tel/sms — we
                      // render the raw text in that case so the user can
                      // still SEE what was in the file without it being
                      // clickable.
                      const href = safeHref(u.value);
                      return (
                        <ContactRow key={`u${j}`} icon={<GlobeIcon />} label={u.label}
                                    href={href} text={u.value} unsafe={!href} />
                      );
                    })}
                    {c.bday && (
                      <ContactRow icon={<GiftIcon />} label="Birthday" text={fmtBday(c.bday)} />
                    )}
                  </ul>
                  {c.note && (
                    <div className="vcf-card__note">
                      <span className="vcf-card__note-icon"><NoteIcon /></span>
                      <span>{c.note}</span>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
