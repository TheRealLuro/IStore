// Inline SVG icons. Stroke-based, 1.6 weight, 16px default — outline style
// keeps with the minimalist monochrome direction.
import React from "react";

export const Icon = ({ name, size = 16, strokeWidth = 1.6, ...props }) => {
  const paths = {
    library: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
    image: <><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></>,
    video: <><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/></>,
    document: <><path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M5 21V5a2 2 0 0 1 2-2h7l5 5v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z"/></>,
    folder: <><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></>,
    folderPlus: <><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 11v6"/><path d="M9 14h6"/></>,
    search: <><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></>,
    upload: <><path d="M12 16V4"/><path d="m6 10 6-6 6 6"/><path d="M4 20h16"/></>,
    download: <><path d="M12 4v12"/><path d="m18 10-6 6-6-6"/><path d="M4 20h16"/></>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></>,
    user: <><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></>,
    users: <><circle cx="9" cy="8" r="3.5"/><path d="M2 20a7 7 0 0 1 14 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7"/><path d="M22 20a7 7 0 0 0-5-6.7"/></>,
    map: <><polygon points="3 6 9 3 15 6 21 3 21 18 15 21 9 18 3 21 3 6"/><line x1="9" y1="3" x2="9" y2="18"/><line x1="15" y1="6" x2="15" y2="21"/></>,
    shield: <><path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="m9 12 2 2 4-4"/></>,
    lock: <><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></>,
    key: <><circle cx="8" cy="15" r="4"/><path d="m11 12 8-8"/><path d="m18 5 2 2"/><path d="m15 8 2 2"/></>,
    cloud: <><path d="M17.5 19a4.5 4.5 0 0 0 0-9 6 6 0 0 0-11.5 1A4 4 0 0 0 6 19z"/></>,
    trash: <><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="m6 6 1 14a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-14"/></>,
    x: <><path d="M6 6 18 18"/><path d="M18 6 6 18"/></>,
    check: <><path d="m4 12 5 5L20 6"/></>,
    chevronDown: <><path d="m6 9 6 6 6-6"/></>,
    chevronUp: <><path d="m6 15 6-6 6 6"/></>,
    chevronRight: <><path d="m9 6 6 6-6 6"/></>,
    chevronLeft: <><path d="m15 6-6 6 6 6"/></>,
    arrowRight: <><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></>,
    plus: <><path d="M12 5v14"/><path d="M5 12h14"/></>,
    minus: <><path d="M5 12h14"/></>,
    eye: <><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></>,
    eyeOff: <><path d="M3 3l18 18"/><path d="M10.6 5.1a10 10 0 0 1 11.4 6.9 10 10 0 0 1-1.7 2.7"/><path d="M6.6 6.6A10 10 0 0 0 2 12s3.5 7 10 7a9 9 0 0 0 4.7-1.3"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></>,
    sparkles: <><path d="M12 3v3"/><path d="M12 18v3"/><path d="M3 12h3"/><path d="M18 12h3"/><path d="m5.6 5.6 2.1 2.1"/><path d="m16.3 16.3 2.1 2.1"/><path d="m5.6 18.4 2.1-2.1"/><path d="m16.3 7.7 2.1-2.1"/></>,
    sort: <><path d="M3 6h18"/><path d="M6 12h12"/><path d="M9 18h6"/></>,
    grid: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
    list: <><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></>,
    info: <><circle cx="12" cy="12" r="9"/><path d="M12 8h.01"/><path d="M11 12h1v4h1"/></>,
    alert: <><path d="M12 3 2 21h20z"/><path d="M12 10v5"/><path d="M12 18h.01"/></>,
    file: <><path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M5 21V5a2 2 0 0 1 2-2h7l5 5v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z"/></>,
    moreH: <><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></>,
    share: <><circle cx="6" cy="12" r="3"/><circle cx="18" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><path d="m9 13 6 4"/><path d="m15 7-6 4"/></>,
    star: <><polygon points="12 3 14.5 9 21 9.5 16 14 17.5 21 12 17.5 6.5 21 8 14 3 9.5 9.5 9"/></>,
    pin: <><path d="M12 21v-7"/><path d="M8 14h8l-1-7H9z"/><path d="M9 7V4h6v3"/></>,
    bell: <><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10 21a2 2 0 0 0 4 0"/></>,
    camera: <><path d="M3 7a2 2 0 0 1 2-2h2l2-2h6l2 2h2a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><circle cx="12" cy="13" r="4"/></>,
    map_pin: <><path d="M12 21s-7-7.5-7-12a7 7 0 0 1 14 0c0 4.5-7 12-7 12z"/><circle cx="12" cy="9" r="2.5"/></>,
    refresh: <><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/></>,
    log_out: <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/></>,
    copy: <><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></>,
    sun: <><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.9 4.9 1.4 1.4"/><path d="m17.7 17.7 1.4 1.4"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m4.9 19.1 1.4-1.4"/><path d="m17.7 6.3 1.4-1.4"/></>,
    moon: <><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></>,
    logo: <><path d="M4 7.5 12 3.5l8 4-8 4-8-4z"/><path d="m4 12.5 8 4 8-4"/><path d="m4 17 8 4 8-4"/></>,
    cookie: <><path d="M12 3a9 9 0 1 0 9 9 5 5 0 0 1-5-5 4 4 0 0 1-4-4z"/><circle cx="9" cy="10" r="0.5"/><circle cx="14" cy="14" r="0.5"/><circle cx="8" cy="15" r="0.5"/></>,
    contact: <><circle cx="12" cy="9" r="3"/><path d="M5 21a7 7 0 0 1 14 0"/><rect x="3" y="3" width="18" height="18" rx="3"/></>,
    password: <><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/><path d="M12 15v2"/></>,
    gamesave: <><path d="M6 9h12a3 3 0 0 1 3 3v4a3 3 0 0 1-5.4 1.8L13 16h-2l-2.6 1.8A3 3 0 0 1 3 16v-4a3 3 0 0 1 3-3z"/><path d="M8 13h2"/><path d="M9 12v2"/><circle cx="15" cy="13" r="0.5"/><circle cx="17" cy="14" r="0.5"/></>,
    iot: <><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10h.01"/><path d="M11 10h.01"/><path d="M15 10h.01"/><path d="M7 14h6"/></>,
    edit: <><path d="M16 3l5 5L8 21H3v-5z"/></>,
    menu: <><path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/></>,
    pencil: <><path d="m15 5 4 4"/><path d="M4 20h4l11-11-4-4L4 16z"/></>,
    arrowUp: <><path d="M12 19V5"/><path d="m6 11 6-6 6 6"/></>,
    map_marker: <><path d="M12 21s-7-7.5-7-12a7 7 0 0 1 14 0c0 4.5-7 12-7 12z"/><circle cx="12" cy="9" r="2.5"/></>,
    cpu: <><rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3"/><path d="M15 1v3"/><path d="M9 20v3"/><path d="M15 20v3"/><path d="M1 9h3"/><path d="M1 15h3"/><path d="M20 9h3"/><path d="M20 15h3"/></>,
    activity: <><path d="M3 12h4l3-9 4 18 3-9h4"/></>,
    database: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></>,
    play: <><polygon points="5 3 19 12 5 21 5 3"/></>,
    pause: <><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></>,
    // Stop glyph — a filled-ish rounded square; used by the document TTS reader.
    square: <><rect x="5" y="5" width="14" height="14" rx="2"/></>,
    // Headphones glyph — the "Listen" affordance on the document Translate panel.
    headphones: <><path d="M4 14v-2a8 8 0 0 1 16 0v2"/><rect x="2" y="14" width="5" height="7" rx="1.5"/><rect x="17" y="14" width="5" height="7" rx="1.5"/></>,
    stack: <><polyline points="2 7 12 12 22 7"/><polyline points="2 12 12 17 22 12"/><polyline points="2 17 12 22 22 17"/><polyline points="2 7 12 2 22 7"/></>,
    history: <><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/></>,
    target: <><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/></>,
    layers: <><polygon points="12 2 22 8 12 14 2 8 12 2"/><polyline points="2 14 12 20 22 14"/></>,
    wand: <><path d="M3 21 21 3"/><path d="m14 6 4 4"/><path d="M5 3v4"/><path d="M3 5h4"/><path d="M19 17v4"/><path d="M17 19h4"/></>,
    arrowLeft: <><path d="M19 12H5"/><path d="m11 18-6-6 6-6"/></>,
    home: <><path d="m3 11 9-8 9 8"/><path d="M5 9v11h14V9"/></>,
    maximize: <><path d="M3 9V3h6"/><path d="M21 9V3h-6"/><path d="M3 15v6h6"/><path d="M21 15v6h-6"/></>,
    game: <><path d="M6 9h12a3 3 0 0 1 3 3v4a3 3 0 0 1-5.4 1.8L13 16h-2l-2.6 1.8A3 3 0 0 1 3 16v-4a3 3 0 0 1 3-3z"/><path d="M8 13h2"/><path d="M9 12v2"/><circle cx="15" cy="13" r="0.5"/><circle cx="17" cy="14" r="0.5"/></>,
    wifi: <><path d="M2 9a16 16 0 0 1 20 0"/><path d="M5 12a12 12 0 0 1 14 0"/><path d="M8 15a8 8 0 0 1 8 0"/><circle cx="12" cy="19" r="0.5"/></>,
    device: <><rect x="3" y="4" width="18" height="11" rx="2"/><path d="M2 19h20"/><path d="M9 19v-4"/><path d="M15 19v-4"/></>,
    laptop: <><rect x="3" y="4" width="18" height="11" rx="2"/><path d="M2 19h20"/></>,
    logout: <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/></>,
    // `code` is the angle-brackets glyph used on the gallery card +
    // preview hero whenever the file is a source-code text type.
    code: <><polyline points="8 6 2 12 8 18"/><polyline points="16 6 22 12 16 18"/></>,
    // Audio / video player glyphs.
    volume: <><polygon points="3 10 7 10 12 5 12 19 7 14 3 14 3 10"/><path d="M16 8a5 5 0 0 1 0 8"/><path d="M19 5a9 9 0 0 1 0 14"/></>,
    volume_low: <><polygon points="3 10 7 10 12 5 12 19 7 14 3 14 3 10"/><path d="M16 8a5 5 0 0 1 0 8"/></>,
    volume_off: <><polygon points="3 10 7 10 12 5 12 19 7 14 3 14 3 10"/><line x1="22" y1="9" x2="16" y2="15"/><line x1="16" y1="9" x2="22" y2="15"/></>,
    rewind10: <><path d="M4 12a8 8 0 1 0 3-6.2L4 8"/><path d="M4 4v4h4"/><text x="12" y="15" fontSize="6" textAnchor="middle" stroke="none" fill="currentColor" fontFamily="ui-monospace,Menlo,monospace">10</text></>,
    forward10: <><path d="M20 12a8 8 0 1 1-3-6.2L20 8"/><path d="M20 4v4h-4"/><text x="12" y="15" fontSize="6" textAnchor="middle" stroke="none" fill="currentColor" fontFamily="ui-monospace,Menlo,monospace">10</text></>,
    music: <><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></>,
    // Other file-type glyphs used by the file-type catalog + viewers.
    calendar: <><rect x="3" y="5" width="18" height="16" rx="2"/><line x1="3" y1="10" x2="21" y2="10"/><line x1="8" y1="3" x2="8" y2="7"/><line x1="16" y1="3" x2="16" y2="7"/></>,
    spreadsheet: <><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/></>,
    archive: <><rect x="3" y="3" width="18" height="5" rx="1"/><path d="M5 8v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8"/><line x1="10" y1="13" x2="14" y2="13"/></>,
    slides: <><rect x="2" y="4" width="20" height="13" rx="2"/><path d="M12 17v4"/><path d="M8 21h8"/></>,
    audio: <><path d="M3 12a9 9 0 0 1 18 0v6a2 2 0 0 1-2 2h-2v-6h4"/><path d="M3 18v-6h4v6H5a2 2 0 0 1-2-2z"/></>,
    // 3D model glyph — an isometric cube: hexagon silhouette with three
    // inner edges meeting at the center vertex. Used by the model3d kind.
    cube: <><path d="M12 2 21 7v10l-9 5-9-5V7z"/><path d="M12 12 21 7"/><path d="M12 12 3 7"/><path d="M12 12v10"/></>,
    // Open-book glyph — two facing pages over a spine. Used by the ebook kind.
    book: <><path d="M12 5.5C10.5 4.5 8.5 4 6 4a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1c2.5 0 4.5.5 6 1.5"/><path d="M12 5.5C13.5 4.5 15.5 4 18 4a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1c-2.5 0-4.5.5-6 1.5z"/><path d="M12 5.5v14"/></>,
    // Type / font glyph — a serif "A" with a baseline serif bar, evoking a
    // typeface specimen. Used by the font kind.
    type: <><path d="M5 19 12 5l7 14"/><path d="M8 13h8"/><path d="M3 19h4"/><path d="M17 19h4"/></>,
    // Speech-bubble used by the comments panel + collapsed bubble.
    message: <><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></>,
  };
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" {...props}>
      {paths[name]}
    </svg>
  );
};

// Initials helper colocated with the icons module since it's a tiny pure
// utility used by Sidebar/PreviewPanel for avatar fallbacks.
export const initials = (n) =>
  (n || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase() || "")
    .join("") || "?";
