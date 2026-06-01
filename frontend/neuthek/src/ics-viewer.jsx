// ICS / iCalendar viewer — parses VEVENT blocks out of the file and
// renders them as clean event cards, each led by a month/day "date
// chip" so the calendar scans at a glance. The parser is a minimal
// subset of RFC 5545: it understands BEGIN/END blocks, folded long
// lines (a CR/LF followed by a single space/tab continues the previous
// line), basic property parameters (`DTSTART;TZID=…:…`), and the most
// common date/datetime forms (`YYYYMMDD`, `YYYYMMDDTHHMMSS`, with or
// without trailing `Z`).
//
// Recurring events expand only their DTSTART occurrence — RRULE
// expansion across a calendar window is out of scope for a preview
// surface.
import React, { useState, useEffect, useMemo, useCallback } from "react";
import toast from "react-hot-toast";
import { safeHref } from "./safe-href.js";
import { Icon } from "./icons.jsx";
import { fetchMediaBlob, originalMediaUrl } from "@/api/files";
import { ViewerSkeleton, ViewerError, ViewerEmpty } from "./viewer-states.jsx";

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

// `ORGANIZER;CN=Jane Doe:mailto:jane@x.com` → { name: "Jane Doe",
// email: "jane@x.com" }. Falls back to the raw address when no CN.
function parsePerson(left, value) {
  const cn = left.match(/CN=([^;:]+)/i);
  const email = value.replace(/^MAILTO:/i, "").trim();
  return { name: cn ? unescape(cn[1].replace(/^"|"$/g, "")) : "", email };
}

function parseIcs(text) {
  const lines = unfold(text).split(/\r?\n/);
  const events = [];
  let cur = null;
  let calName = null;
  for (const line of lines) {
    if (!line) continue;
    if (line === "BEGIN:VEVENT") { cur = { attendees: [] }; continue; }
    if (line === "END:VEVENT") { if (cur) events.push(cur); cur = null; continue; }
    const colon = line.indexOf(":");
    if (colon === -1) continue;
    const left = line.slice(0, colon);
    const value = line.slice(colon + 1);
    const [name] = left.split(";");
    if (!cur) {
      if (name === "X-WR-CALNAME") calName = unescape(value);
      continue;
    }
    if (name === "SUMMARY") cur.summary = unescape(value);
    else if (name === "DESCRIPTION") cur.description = unescape(value);
    else if (name === "LOCATION") cur.location = unescape(value);
    else if (name === "DTSTART") cur.start = parseIcsDate(value);
    else if (name === "DTEND") cur.end = parseIcsDate(value);
    else if (name === "ORGANIZER") cur.organizer = parsePerson(left, value);
    else if (name === "ATTENDEE") cur.attendees.push(parsePerson(left, value));
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

const DAY_FMT = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
const TIME_FMT = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const MON_FMT = new Intl.DateTimeFormat(undefined, { month: "short" });

// The headline line under the title: weekday + full date.
function fmtDay(start, end) {
  if (!start) return "";
  if (start.allDay && end && end.date && +end.date - +start.date > 86_400_000) {
    return `${DAY_FMT.format(start.date)} → ${DAY_FMT.format(new Date(+end.date - 86_400_000))}`;
  }
  return DAY_FMT.format(start.date);
}

// The time portion, rendered next to a clock glyph. Empty for all-day.
function fmtTime(start, end) {
  if (!start || start.allDay) return start ? "All day" : "";
  const t = TIME_FMT.format(start.date);
  if (!end || !end.date) return t;
  const sameDay = start.date.toDateString() === end.date.toDateString();
  if (sameDay) return `${t} – ${TIME_FMT.format(end.date)}`;
  return `${t} → ${DAY_FMT.format(end.date)} ${TIME_FMT.format(end.date)}`;
}

// ---- small monochrome line-icons (24 viewBox, stroke currentColor) ---
const G = ({ children, size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {children}
  </svg>
);
const ClockIcon = (p) => <G {...p}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></G>;
const PinIcon = (p) => <G {...p}><path d="M12 21s-7-7.5-7-12a7 7 0 0 1 14 0c0 4.5-7 12-7 12z"/><circle cx="12" cy="9" r="2.5"/></G>;
const UserIcon = (p) => <G {...p}><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></G>;
const UsersIcon = (p) => <G {...p}><circle cx="9" cy="8" r="3.5"/><path d="M2 20a7 7 0 0 1 14 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7"/><path d="M22 20a7 7 0 0 0-5-6.7"/></G>;
const LinkIcon = (p) => <G {...p}><path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"/></G>;

function personLabel(p) {
  if (!p) return "";
  if (p.name && p.email) return `${p.name} · ${p.email}`;
  return p.name || p.email || "";
}

export function IcsViewer({ fileId, fileName }) {
  const [text, setText] = useState(null);
  const [err, setErr] = useState(null);
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
        toast.error("Couldn't load calendar");
      }
    })();
    return () => { cancelled = true; };
  }, [fileId, attempt]);

  const parsed = useMemo(() => (text ? parseIcs(text) : null), [text]);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  return (
    <div className="ics-viewer" onClick={(e) => e.stopPropagation()}>
      <div className="ics-viewer__head">
        <span className="vw-head__icon"><Icon name="calendar" size={15} /></span>
        <span className="ics-viewer__name">{parsed?.calName || fileName}</span>
        <span className="ics-viewer__meta mono">
          {parsed ? `${parsed.events.length} event${parsed.events.length === 1 ? "" : "s"}` : ""}
        </span>
      </div>
      <div className="ics-viewer__body">
        {err ? (
          <ViewerError
            title="Couldn't load calendar"
            message={err}
            onRetry={retry}
            downloadUrl={originalMediaUrl(fileId)}
            downloadName={fileName}
          />
        ) : !parsed ? (
          <ViewerSkeleton lines={["46%", "70%", "54%", "60%"]} className="ics-skel" />
        ) : parsed.events.length === 0 ? (
          <ViewerEmpty icon="calendar" title="No events" message="This calendar file doesn't contain any events to show." />
        ) : (
          <ul className="ics-viewer__list">
            {parsed.events.map((ev, i) => {
              const d = ev.start?.date;
              const time = fmtTime(ev.start, ev.end);
              return (
                <li key={ev.uid || i} className="ics-event">
                  <div className="ics-event__chip" aria-hidden="true">
                    {d ? (
                      <>
                        <span className="ics-event__chip-mon">{MON_FMT.format(d).toUpperCase()}</span>
                        <span className="ics-event__chip-day">{d.getDate()}</span>
                      </>
                    ) : (
                      <span className="ics-event__chip-mon">—</span>
                    )}
                  </div>
                  <div className="ics-event__main">
                    <div className="ics-event__title">{ev.summary || "(untitled event)"}</div>
                    {ev.start && <div className="ics-event__day">{fmtDay(ev.start, ev.end)}</div>}
                    {time && (
                      <div className="ics-event__row">
                        <span className="ics-event__icon"><ClockIcon /></span>
                        <span>{time}</span>
                      </div>
                    )}
                    {ev.location && (
                      <div className="ics-event__row">
                        <span className="ics-event__icon"><PinIcon /></span>
                        <a href={`https://maps.google.com/?q=${encodeURIComponent(ev.location)}`}
                           target="_blank" rel="noreferrer noopener">{ev.location}</a>
                      </div>
                    )}
                    {ev.organizer && personLabel(ev.organizer) && (
                      <div className="ics-event__row">
                        <span className="ics-event__icon"><UserIcon /></span>
                        <span>{personLabel(ev.organizer)}</span>
                      </div>
                    )}
                    {ev.attendees.length > 0 && (
                      <div className="ics-event__row ics-event__row--top">
                        <span className="ics-event__icon"><UsersIcon /></span>
                        <span className="ics-event__attendees">
                          {ev.attendees.map(personLabel).filter(Boolean).join(", ")}
                        </span>
                      </div>
                    )}
                    {ev.url && (() => {
                      // Same XSS hardening as the VCF viewer — an .ics file
                      // with `URL:javascript:…` would otherwise be clickable
                      // and run script in our origin.
                      const href = safeHref(ev.url);
                      return (
                        <div className="ics-event__row">
                          <span className="ics-event__icon"><LinkIcon /></span>
                          {href ? (
                            <a href={href} target="_blank" rel="noreferrer noopener">{ev.url}</a>
                          ) : (
                            <span title="Link uses an unsafe scheme and was disabled">{ev.url}</span>
                          )}
                        </div>
                      );
                    })()}
                    {ev.description && <div className="ics-event__desc">{ev.description}</div>}
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
