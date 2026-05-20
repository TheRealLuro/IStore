// ICS / iCalendar viewer — parses VEVENT blocks out of the file and
// renders them as clean date-grouped event cards. The parser is a
// minimal subset of RFC 5545: it understands BEGIN/END blocks,
// folded long lines (a CR/LF followed by a single space/tab
// continues the previous line), basic property parameters
// (`DTSTART;TZID=…:…`), and the most common date/datetime forms
// (`YYYYMMDD`, `YYYYMMDDTHHMMSS`, with or without trailing `Z`).
//
// Recurring events expand only their DTSTART occurrence — RRULE
// expansion across a calendar window is out of scope for a preview
// surface.
import React, { useState, useEffect, useMemo } from "react";
import { Icon } from "./icons.jsx";
import toast from "react-hot-toast";
import { fetchMediaBlob, originalMediaUrl } from "@/api/files";
import { safeHref } from "./safe-href.js";

function unfold(text) {
  // Per RFC 5545 §3.1 a long line MAY be split with CRLF + WSP.
  return text.replace(/\r?\n[ \t]/g, "");
}

function parseIcsDate(raw) {
  if (!raw) return null;
  const s = raw.toString();
  // Date: YYYYMMDD. Datetime: YYYYMMDDTHHMMSS[Z].
  const m = s.match(/^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z)?)?$/);
  if (!m) return null;
  const [, y, mo, d, hh, mm, ss, z] = m;
  if (hh == null) {
    return { date: new Date(Number(y), Number(mo) - 1, Number(d)), allDay: true };
  }
  if (z === "Z") {
    return {
      date: new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d), Number(hh), Number(mm), Number(ss))),
      allDay: false,
    };
  }
  return {
    date: new Date(Number(y), Number(mo) - 1, Number(d), Number(hh), Number(mm), Number(ss)),
    allDay: false,
  };
}

function unescape(s) {
  if (typeof s !== "string") return "";
  return s.replace(/\\n/gi, "\n").replace(/\\,/g, ",").replace(/\\;/g, ";").replace(/\\\\/g, "\\");
}

function parseIcs(text) {
  const lines = unfold(text).split(/\r?\n/);
  const events = [];
  let cur = null;
  let calName = null;
  for (const line of lines) {
    if (!line) continue;
    if (line === "BEGIN:VEVENT") { cur = {}; continue; }
    if (line === "END:VEVENT") { if (cur) events.push(cur); cur = null; continue; }
    const colon = line.indexOf(":");
    if (colon === -1) continue;
    const left = line.slice(0, colon);
    const value = line.slice(colon + 1);
    const [name] = left.split(";"); // drop params for simplicity
    if (!cur) {
      if (name === "X-WR-CALNAME") calName = unescape(value);
      continue;
    }
    if (name === "SUMMARY") cur.summary = unescape(value);
    else if (name === "DESCRIPTION") cur.description = unescape(value);
    else if (name === "LOCATION") cur.location = unescape(value);
    else if (name === "DTSTART") cur.start = parseIcsDate(value);
    else if (name === "DTEND") cur.end = parseIcsDate(value);
    else if (name === "ORGANIZER") cur.organizer = value.replace(/^MAILTO:/i, "");
    else if (name === "URL") cur.url = value;
    else if (name === "STATUS") cur.status = value;
    else if (name === "UID") cur.uid = value;
  }
  // Sort earliest first; events without a date sink to the bottom.
  events.sort((a, b) => {
    const ta = a.start?.date?.getTime() ?? Infinity;
    const tb = b.start?.date?.getTime() ?? Infinity;
    return ta - tb;
  });
  return { calName, events };
}

const DAY_FMT = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" });
const TIME_FMT = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

function fmtRange(start, end) {
  if (!start) return "—";
  if (start.allDay) {
    if (end && end.date && +end.date - +start.date > 86_400_000) {
      return `${DAY_FMT.format(start.date)} → ${DAY_FMT.format(new Date(+end.date - 86_400_000))}`;
    }
    return `${DAY_FMT.format(start.date)} · all day`;
  }
  const sameDay = end && end.date && start.date.toDateString() === end.date.toDateString();
  if (!end || !end.date) return `${DAY_FMT.format(start.date)} · ${TIME_FMT.format(start.date)}`;
  if (sameDay) return `${DAY_FMT.format(start.date)} · ${TIME_FMT.format(start.date)} → ${TIME_FMT.format(end.date)}`;
  return `${DAY_FMT.format(start.date)} ${TIME_FMT.format(start.date)} → ${DAY_FMT.format(end.date)} ${TIME_FMT.format(end.date)}`;
}

export function IcsViewer({ fileId, fileName }) {
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
        toast.error("Couldn't load calendar");
      }
    })();
    return () => { cancelled = true; };
  }, [fileId]);

  const parsed = useMemo(() => (text ? parseIcs(text) : null), [text]);

  return (
    <div className="ics-viewer" onClick={(e) => e.stopPropagation()}>
      <div className="ics-viewer__head">
        <span className="ics-viewer__name">{parsed?.calName || fileName}</span>
        <span className="ics-viewer__meta mono">
          {parsed ? `${parsed.events.length} event${parsed.events.length === 1 ? "" : "s"}` : ""}
        </span>
      </div>
      <div className="ics-viewer__body">
        {err ? (
          <div className="ics-viewer__error">{err}</div>
        ) : !parsed ? (
          <div className="ics-viewer__loading">Parsing calendar…</div>
        ) : parsed.events.length === 0 ? (
          <div className="ics-viewer__empty">No events in this calendar.</div>
        ) : (
          <ul className="ics-viewer__list">
            {parsed.events.map((ev, i) => (
              <li key={ev.uid || i} className="ics-event">
                <div className="ics-event__date mono">
                  {fmtRange(ev.start, ev.end)}
                </div>
                <div className="ics-event__title">{ev.summary || "(untitled event)"}</div>
                {ev.location && (
                  <div className="ics-event__row">
                    <Icon name="map_pin" size={12}/> <span>{ev.location}</span>
                  </div>
                )}
                {ev.organizer && (
                  <div className="ics-event__row">
                    <Icon name="user" size={12}/> <span className="mono">{ev.organizer}</span>
                  </div>
                )}
                {ev.url && (() => {
                  // Same XSS hardening as the VCF viewer — an .ics file
                  // with `URL:javascript:…` would otherwise be clickable
                  // and run script in our origin.
                  const href = safeHref(ev.url);
                  return (
                    <div className="ics-event__row">
                      <Icon name="share" size={12}/>{" "}
                      {href ? (
                        <a href={href} target="_blank" rel="noreferrer noopener">{ev.url}</a>
                      ) : (
                        <span title="Link uses an unsafe scheme and was disabled">{ev.url}</span>
                      )}
                    </div>
                  );
                })()}
                {ev.description && (
                  <div className="ics-event__desc">{ev.description}</div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
