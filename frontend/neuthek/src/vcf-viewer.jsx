// VCF / vCard viewer — parses one or more VCARD blocks and renders
// them as clean contact cards. Supports vCard 2.1 + 3.0 + 4.0 line
// folding, basic property parameters (`TEL;TYPE=CELL:…`), and the
// most common fields (FN, N, ORG, TITLE, TEL, EMAIL, ADR, URL,
// NOTE, BDAY, PHOTO).
//
// Embedded base64 photos render inline; external URI photos render
// as a `<img>` direct src. Falls back to a circle initials avatar
// when no photo is present.
import React, { useState, useEffect, useMemo } from "react";
import { Icon, initials } from "./icons.jsx";
import toast from "react-hot-toast";
import { fetchMediaBlob, originalMediaUrl } from "@/api/files";
import { safeHref } from "./safe-href.js";

function unfold(text) {
  return text.replace(/\r?\n[ \t]/g, "");
}

function unescape(s) {
  if (typeof s !== "string") return "";
  return s.replace(/\\n/gi, "\n").replace(/\\,/g, ",").replace(/\\;/g, ";").replace(/\\\\/g, "\\");
}

function parseVcf(text) {
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
      if (typeof t === "string") return t.split(",")[0];
      return Object.keys(params).find((k) => k !== "ENCODING" && k !== "MEDIATYPE" && k !== "PREF") || "";
    };
    if (name === "FN") cur.fn = unescape(value);
    else if (name === "N") {
      const [family, given] = value.split(";");
      cur.n = { family: unescape(family || ""), given: unescape(given || "") };
    }
    else if (name === "ORG") cur.org = unescape(value.split(";").join(" · "));
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

export function VcfViewer({ fileId, fileName }) {
  const [text, setText] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let cancelled = false;
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
  }, [fileId]);

  const cards = useMemo(() => (text ? parseVcf(text) : null), [text]);

  return (
    <div className="vcf-viewer" onClick={(e) => e.stopPropagation()}>
      <div className="vcf-viewer__head">
        <span className="vcf-viewer__name">{fileName}</span>
        <span className="vcf-viewer__meta mono">
          {cards ? `${cards.length} contact${cards.length === 1 ? "" : "s"}` : ""}
        </span>
      </div>
      <div className="vcf-viewer__body">
        {err ? (
          <div className="vcf-viewer__error">{err}</div>
        ) : !cards ? (
          <div className="vcf-viewer__loading">Parsing contacts…</div>
        ) : cards.length === 0 ? (
          <div className="vcf-viewer__empty">No contacts found.</div>
        ) : (
          <ul className="vcf-viewer__list">
            {cards.map((c, i) => {
              const fullName = c.fn || [c.n?.given, c.n?.family].filter(Boolean).join(" ") || "(no name)";
              return (
                <li key={i} className="vcf-card">
                  <div className="vcf-card__avatar">
                    {c.photo ? (
                      <img src={c.photo} alt={fullName}/>
                    ) : (
                      <span>{initials(fullName)}</span>
                    )}
                  </div>
                  <div className="vcf-card__main">
                    <div className="vcf-card__name">{fullName}</div>
                    {(c.title || c.org) && (
                      <div className="vcf-card__org">
                        {[c.title, c.org].filter(Boolean).join(" · ")}
                      </div>
                    )}
                    <ul className="vcf-card__rows">
                      {c.phones.map((p, j) => (
                        <li key={`p${j}`} className="vcf-card__row">
                          <Icon name="cpu" size={12}/>
                          <a href={`tel:${p.value}`} className="mono">{p.value}</a>
                          {p.label && <span className="vcf-card__label mono">{p.label}</span>}
                        </li>
                      ))}
                      {c.emails.map((e, j) => (
                        <li key={`e${j}`} className="vcf-card__row">
                          <Icon name="share" size={12}/>
                          <a href={`mailto:${e.value}`}>{e.value}</a>
                          {e.label && <span className="vcf-card__label mono">{e.label}</span>}
                        </li>
                      ))}
                      {c.addrs.map((a, j) => (
                        <li key={`a${j}`} className="vcf-card__row">
                          <Icon name="map_pin" size={12}/>
                          <span>{a.value}</span>
                          {a.label && <span className="vcf-card__label mono">{a.label}</span>}
                        </li>
                      ))}
                      {c.urls.map((u, j) => {
                        // Scheme allow-list: a vcard with `URL:javascript:…`
                        // would otherwise execute on click and exfiltrate
                        // the JWT from localStorage. `safeHref` returns null
                        // for anything that isn't http/https/mailto/tel/sms —
                        // we render the raw text in that case so the user
                        // can still SEE what was in the file without it
                        // being clickable.
                        const href = safeHref(u.value);
                        return (
                          <li key={`u${j}`} className="vcf-card__row">
                            <Icon name="share" size={12}/>
                            {href ? (
                              <a href={href} target="_blank" rel="noreferrer noopener">{u.value}</a>
                            ) : (
                              <span title="Link uses an unsafe scheme and was disabled">{u.value}</span>
                            )}
                            {u.label && <span className="vcf-card__label mono">{u.label}</span>}
                          </li>
                        );
                      })}
                      {c.bday && (
                        <li className="vcf-card__row">
                          <Icon name="calendar" size={12}/>
                          <span className="mono">{c.bday}</span>
                        </li>
                      )}
                    </ul>
                    {c.note && <div className="vcf-card__note">{c.note}</div>}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
