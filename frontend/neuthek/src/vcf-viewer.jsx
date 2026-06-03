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
import React, { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { initials, Icon } from "./icons.jsx";
import toast from "react-hot-toast";
import { fetchMediaBlob, originalMediaUrl } from "@/api/files";
import { safeHref } from "./safe-href.js";
import { ViewerSkeleton, ViewerError, ViewerEmpty, CopyButton } from "./viewer-states.jsx";
// Reuse the drive's RFC-4180/TSV parser for the genomics-VCF variant table
// (it's just a tab-delimited grid) rather than hand-rolling a second splitter.
import { parseDelimited } from "./csv-viewer.jsx";

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

// The `.vcf` extension is overloaded across two unrelated formats:
//   1. vCard          — contacts (`BEGIN:VCARD … END:VCARD`).
//   2. Variant Call   — genomics/bioinformatics (header lines `##fileformat=
//      Format (VCF)      VCFv4.x`, a single `#CHROM\tPOS\t…` column header,
//                        then TAB-delimited variant rows).
// Classify the text so the viewer can render the right thing instead of a
// dead "no contacts" wall for a genomics file. Shared by the drive viewer
// (VcfViewer) and the vault preview so both branch identically.
//   → "vcard"   when any line starts with BEGIN:VCARD (case-insensitive)
//   → "variant" when the text starts with `##fileformat=VCF`, OR a line
//                starts with `#CHROM` immediately followed by a tab
//   → "unknown" otherwise (raw-text fallback)
export function classifyVcf(text) {
  const s = (text || "").replace(/^﻿/, ""); // tolerate a leading BOM
  if (/^[ \t]*BEGIN:VCARD/im.test(s)) return "vcard";
  if (/^##fileformat=VCF/i.test(s)) return "variant";
  if (/^#CHROM\t/im.test(s)) return "variant";
  return "unknown";
}

// Parse a Variant Call Format body into { meta, columns, rows, total }.
// `meta` is the leading `##…` metadata lines; `columns` is the single
// `#CHROM…` header split on tabs; `rows` are the TAB-split variant records
// (capped at `cap`, with `total` reporting the true count for the
// "showing N of M" note). Reuses parseDelimited for the row grid.
const VARIANT_ROW_CAP = 2000;
export function parseVariantVcf(text, cap = VARIANT_ROW_CAP) {
  const normalized = (text || "").replace(/^﻿/, "").replace(/\r\n?/g, "\n");
  const lines = normalized.split("\n");
  const meta = [];
  let columns = [];
  const bodyLines = [];
  for (const line of lines) {
    if (line.startsWith("##")) { meta.push(line); continue; }
    if (line.startsWith("#CHROM")) { columns = line.replace(/^#/, "").split("\t"); continue; }
    if (line.startsWith("#")) continue; // any other comment line
    if (line.trim() === "") continue;   // skip blanks (incl. trailing newline)
    bodyLines.push(line);
  }
  const total = bodyLines.length;
  // Tab-split each kept row with the shared delimited parser (one row per
  // call keeps memory flat and avoids re-joining the capped slice).
  const rows = bodyLines.slice(0, cap).map((l) => parseDelimited(l, "\t")[0] || []);
  // Summarize the most useful `##` keys for the metadata strip header.
  const metaVal = (key) => {
    const hit = meta.find((m) => m.toLowerCase().startsWith(`##${key.toLowerCase()}=`));
    return hit ? hit.slice(hit.indexOf("=") + 1) : "";
  };
  return {
    meta,
    columns,
    rows,
    total,
    fileformat: metaVal("fileformat"),
    source: metaVal("source"),
    reference: metaVal("reference"),
  };
}

// Exported so the vault's import-from-file flow (VLT-7) can reuse the exact
// same vCard parser when mapping an uploaded .vcf into a structured Contact
// vault item — no second, drifting parser.
export function parseVcf(text) {
  // Robustness: strip a leading UTF-8 BOM (some exporters/encoders prepend
  // one — it breaks the anchored BEGIN:VCARD match and yields "no contacts"),
  // normalize CR-only / CRLF endings to \n, THEN unfold folded lines.
  const normalized = (text || "").replace(/^﻿/, "").replace(/\r\n?/g, "\n");
  const lines = unfold(normalized).split("\n");
  const cards = [];
  let cur = null;
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, ""); // drop trailing whitespace/stray CR
    if (!line) continue;
    // startsWith (not the `$`-anchored form) tolerates trailing params/space.
    if (/^BEGIN:VCARD/i.test(line)) { cur = { phones: [], emails: [], addrs: [], urls: [] }; continue; }
    if (/^END:VCARD/i.test(line)) { if (cur) cards.push(cur); cur = null; continue; }
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

// Presentational contact-card list. Takes ALREADY-PARSED vCards (from
// parseVcf) and renders the avatar + name + labelled rows. Split out from
// VcfViewer so other hosts that hold the vCard TEXT but not a fileId —
// notably the encrypted Vault, which decrypts a `.vcf` blob client-side —
// can render the exact same contact card via parseVcf + <VcfCards> with
// no second parser or duplicated markup.
export function VcfCards({ cards }) {
  if (!cards || cards.length === 0) {
    return (
      <ViewerEmpty
        icon="contact"
        title="No contacts found"
        message="This vCard file didn't contain any readable contact entries."
      />
    );
  }
  return (
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
  );
}

// Presentational data table for a Variant Call Format file. Takes the raw
// VCF text, parses it (parseVariantVcf), and renders:
//   • a "Variant Call Format (genomics)" badge + variant count,
//   • a collapsible "Format / metadata" strip listing the leading `##…`
//     lines (with the fileformat/source/reference summarized inline),
//   • a tidy, scrollable, monospaced table: the `#CHROM…` line as sticky
//     column headers, each variant row zebra-striped, horizontal scroll
//     for many sample columns.
// Reuses the drive CSV viewer's table CSS (`csv-viewer__*`) and the same
// data-scroll-x/y shadow attributes so it reads as one design family; the
// row grid is parsed by the shared parseDelimited. Shared by the drive
// viewer and the vault preview — one parser, one markup.
export function VcfVariantTable({ text }) {
  const v = useMemo(() => parseVariantVcf(text), [text]);
  const [metaOpen, setMetaOpen] = useState(false);

  // Same scroll-shadow behavior as CsvViewer (it isn't exported, so mirror
  // the few lines here): toggle data-scroll-x/y on the scroll container so
  // the existing CSS draws the sticky-edge shadows.
  const scrollRef = useRef(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return undefined;
    const update = () => {
      el.dataset.scrollY = el.scrollTop > 0 ? "1" : "0";
      el.dataset.scrollX = el.scrollLeft > 0 ? "1" : "0";
    };
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => { el.removeEventListener("scroll", update); ro.disconnect(); };
  }, []);

  const { meta, columns, rows, total } = v;
  const shown = rows.length;
  const capped = total > shown;
  const summary = [
    v.fileformat && `format ${v.fileformat}`,
    v.source && `source ${v.source}`,
    v.reference && `reference ${v.reference}`,
  ].filter(Boolean).join(" · ");

  // No dedicated stylesheet for `.vcf-variant` (FE-only change, reusing the
  // CSV table CSS) — so the few container/badge rules are inline, using the
  // app's theme tokens. The table itself is the shared `csv-viewer__*` CSS.
  return (
    <div
      className="vcf-variant"
      style={{ display: "flex", flexDirection: "column", minHeight: 0, height: "100%" }}
    >
      <div
        className="vcf-variant__bar"
        style={{
          display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
          padding: "8px 12px", borderBottom: "1px solid var(--line)",
        }}
      >
        <span
          className="vcf-variant__badge"
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "2px 9px", borderRadius: 999, fontSize: 11, fontWeight: 600,
            letterSpacing: "0.02em",
            background: "color-mix(in oklab, var(--accent, #6aa9ff) 16%, transparent)",
            color: "var(--accent, #6aa9ff)",
            border: "1px solid color-mix(in oklab, var(--accent, #6aa9ff) 30%, transparent)",
          }}
        >
          Variant Call Format (genomics)
        </span>
        <span className="vcf-variant__count mono" style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
          {total.toLocaleString()} variant{total === 1 ? "" : "s"}
          {capped ? ` · showing ${shown.toLocaleString()} of ${total.toLocaleString()}` : ""}
        </span>
      </div>

      {meta.length > 0 && (
        // Button-driven collapsible (not <details>) so no browser disclosure
        // triangle competes with our own chevron — the app hides `::-webkit-
        // details-marker` only per-class in CSS, which we don't touch here.
        <div className="vcf-variant__meta" style={{ borderBottom: "1px solid var(--line)", flex: "0 0 auto" }}>
          <button
            type="button"
            className="vcf-variant__meta-summary"
            aria-expanded={metaOpen}
            onClick={() => setMetaOpen((o) => !o)}
            style={{
              display: "flex", alignItems: "center", gap: 7, cursor: "pointer", width: "100%",
              padding: "7px 12px", fontSize: 12, color: "var(--ink-2)", textAlign: "left",
              background: "transparent", border: 0, font: "inherit",
            }}
          >
            <Icon
              name="chevronRight"
              size={12}
              style={{ transform: metaOpen ? "rotate(90deg)" : "none", transition: "transform 120ms ease", flex: "0 0 auto" }}
            />
            <span>Format / metadata</span>
            {summary && (
              <span className="vcf-variant__meta-sum mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                {summary}
              </span>
            )}
          </button>
          {metaOpen && (
            <pre
              className="vcf-variant__meta-body mono"
              style={{
                margin: 0, padding: "8px 12px 12px 31px", fontSize: 11.5, lineHeight: 1.5,
                color: "var(--ink-3)", whiteSpace: "pre-wrap", wordBreak: "break-word",
                maxHeight: 220, overflow: "auto",
                borderTop: "1px solid var(--line-2)", background: "var(--surface-2)",
              }}
            >
              {meta.join("\n")}
            </pre>
          )}
        </div>
      )}

      <div
        className="csv-viewer__body vcf-variant__scroll"
        ref={scrollRef}
        data-scroll-y="0"
        data-scroll-x="0"
      >
        {columns.length === 0 ? (
          // Headerless VCF (matched `##fileformat=VCF` but no `#CHROM` line) —
          // still show the variant rows so nothing is hidden.
          <table className="csv-viewer__table mono">
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td className="csv-viewer__rownum mono">{(i + 1).toLocaleString()}</td>
                  {r.map((c, j) => (<td key={j}>{c}</td>))}
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <table className="csv-viewer__table mono">
            <thead>
              <tr>
                <th scope="col" className="csv-viewer__rownum mono" title="Row number">#</th>
                {columns.map((h, i) => (
                  <th scope="col" key={i} title={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td className="csv-viewer__rownum mono">{(i + 1).toLocaleString()}</td>
                  {columns.map((_, j) => (<td key={j}>{r[j] ?? ""}</td>))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
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

  // Content-aware: the `.vcf` extension is shared by vCard (contacts) and
  // genomics Variant Call Format. Classify first, then only parse vCards for
  // the vcard branch (a large genomics file shouldn't run through the contact
  // parser at all). Hooks stay unconditional — kind is null until text loads.
  const kind = useMemo(() => (text != null ? classifyVcf(text) : null), [text]);
  const cards = useMemo(
    () => (text != null && kind === "vcard" ? parseVcf(text) : null),
    [text, kind],
  );
  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const isVariant = kind === "variant";
  const headIcon = isVariant ? "code" : "contact";
  const headMeta = isVariant
    ? "Variant Call Format"
    : kind === "vcard" && cards
      ? `${cards.length} contact${cards.length === 1 ? "" : "s"}`
      : "";

  return (
    <div className="vcf-viewer" onClick={(e) => e.stopPropagation()}>
      <div className="vcf-viewer__head">
        <span className="vw-head__icon"><Icon name={headIcon} size={15} /></span>
        <span className="vcf-viewer__name">{fileName}</span>
        <span className="vcf-viewer__meta mono">{headMeta}</span>
      </div>
      <div className="vcf-viewer__body">
        {err ? (
          <ViewerError
            title="Couldn't load file"
            message={err}
            onRetry={retry}
            downloadUrl={originalMediaUrl(fileId)}
            downloadName={fileName}
          />
        ) : kind == null ? (
          <ViewerSkeleton lines={[52, "60%", "44%", "50%", "38%"]} className="vcf-skel" />
        ) : kind === "variant" ? (
          <VcfVariantTable text={text} />
        ) : kind === "vcard" ? (
          <VcfCards cards={cards} />
        ) : (
          // Unknown — neither vCard nor VCF variant. Show the raw text so the
          // content is always visible (existing fallback behavior).
          <pre
            className="vcf-viewer__raw mono"
            style={{
              margin: 0, padding: "12px 14px", fontSize: 12.5, lineHeight: 1.55,
              color: "var(--ink-2)", whiteSpace: "pre-wrap", wordBreak: "break-word",
            }}
          >
            {text}
          </pre>
        )}
      </div>
    </div>
  );
}
