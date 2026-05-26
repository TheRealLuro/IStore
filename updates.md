# Updates queue — pending publish

Working draft for upcoming weekly /updates entries on the marketing site.
Drop the next entries you ship into the same buckets; when an entry
is ready to publish, copy it into `marketing/src/data/updates.ts`
(+ mirror to `updates-index.json` + bump `sitemap.xml`) and remove
the block from this file.

Last published: **W22 (2026-05-26) — Per-user fair queue + real Best
Of + dev capacity calculator**. Next entry target:
- **W23, publish 2026-06-06** — multi-axis filtering, search rewrite,
  C5.1 setup script, themed video/audio/CSV/ICS/VCF viewers.

---

## 2026-w23 — Multi-axis filtering, smarter search, fewer "looks broken" bugs

**Slug:** `2026-w23-multi-axis-filtering-and-bug-bundle`
**Week label:** `Week of June 6, 2026`
**Tags:** `["filtering","search","map","tags","setup","preview","cleanup"]`

### Summary

A week of "make the engine match the marketing." Multi-axis gallery
filtering (filter by person, tag, location radius, capture date —
all combined), a search rewrite that actually does what semantic
search promises (type "teacher teaching math", get the whiteboard
photo), the C5.1 setup script that takes a fresh checkout to a
running stack in one command, the C4.2 "Me" → real-name binding,
the C3 map clustering swap to supercluster + a designed grid
backdrop, and a long bug list — the kind of stuff people kept
running into and assuming the product was broken.

### Found

- **Search needed exact words.** Type "teacher teaching math" and
  the whiteboard photo whose summary said "matrix algebra and
  calculus" never showed up. The ranker required at least one of
  the user's literal tokens (after WordNet synonym expansion) to
  appear in the image's text — even when CLIP's cosine score
  thought the image was clearly relevant. Worse, the search history
  dropdown stayed open after you typed, covering the gallery results
  underneath. You had to click out then back in just to see them.
- **Tags added in the preview disappeared on reopen.** Two bugs in
  one: the app's FE-to-backend mapper was reading from the legacy
  `status` column instead of the new `tags` array (so every
  persisted tag got stripped on the way to the gallery), and the
  preview's inline "Add tag" input only mutated local React state —
  it never called the backend. The tag pop-in felt instant but was
  pure illusion; close + reopen and it was gone.
- **New tags didn't appear in the filter dropdown.** Persisted
  fine; the FE just didn't invalidate the right query cache.
- **People counts were always the library total.** Every person card
  in the People tab read the same number — "40 photos" for Me, "40
  photos" for Aidyn. SQLAlchemy's auto-correlation doesn't reach
  into UNION components reliably, so the correlated count was
  collapsing to "every image of every person."
- **Date filter ignored when the photo was actually taken.** Filtered
  by upload time only. A photo taken in 2018, uploaded yesterday,
  showed up in "today" filters and nowhere else. The EXIF capture
  date was extracted at upload but stored on `image_geo` (gated on
  GPS consent) — users without GPS retention got no capture date
  at all.
- **Upload modal kept stale done rows on reopen.** Closing mid-
  upload via "Close (keep running)" was intentional; reopening
  later showed the now-done rows from the previous session next to
  any new ones. Confusing because they're not "happening now."
- **Filter dropdown was about to get unmanageable.** All chip
  groups expanded by default. Fine with five scenes; painful with
  fifty tags + ten people.
- **Preview crashed and dumped you back to the signed-out splash.**
  A subtle one: a recent fix passed the raw backend row to
  PreviewPanel where `tags` is `[{id, label, color}]` (objects).
  The chip UI renders each tag directly as a JSX child — works for
  strings, throws "Objects are not valid as a React child" for
  objects → React unmounted the whole app.
- **"Looking up location…" stuck forever** on the preview even when
  the same coordinates showed a valid pin on the map. Three layers:
  the TanStack Query v5 `refetchInterval` callback signature
  changed (it gets the Query object now, not the data) so the
  polling never actually fired; the post-upload reverse-geocode
  worker silently cached `None` when Nominatim had a blip, which
  poisoned that coordinate forever; and the FE never re-triggered
  the backfill on stale rows.
- **Map clustering wouldn't scale.** The old pixel-space clusterer
  was O(N × visible-clusters) and re-walked every point on each
  render. Fine up to a few hundred pins; painful past two thousand.
- **Map flashed white when zooming out fast.** Plain backdrop
  exposed during tile-pyramid transitions reads as "the map
  broke."
- **C5.1 setup script was 18 env keys behind reality.** It missed
  every secret added since W12 — Fernet key for the secret-box,
  Google OAuth, GitHub OAuth, Stripe, Resend, every SSE knob, the
  rate-limit caps, half the bucket names.
- **MP4 / MOV / WebM uploads were rejected as "Unsupported or
  unrecognized file type."** The upload validator recognized
  images / PDF / OOXML / SVG / text / code formats but had no
  branch for ISO BMFF (mp4 / m4v / mov / m4a), Matroska
  (mkv / webm), AVI, or any audio container. Every video / audio
  file fell through to the generic "we don't know what this is"
  rejection at the magic-byte stage, never reaching the dispatch
  table that already had a `"video"` validator wired and waiting.
- **DOCX / XLSX / PPTX uploads crashed mid-INSERT.** The
  `images.mime_type_{original,served}` columns were
  `VARCHAR(64)`, but the OOXML MIMEs are 66-74 chars long —
  `application/vnd.openxmlformats-officedocument.presentationml.presentation`
  is 74. Postgres raised `StringDataRightTruncationError` on every
  Office-format upload before the row landed. The validator said
  yes, the database said no.
- **No collapse-to-bubble for the comments panel.** When viewing
  a video full-screen, the 360 px comment panel covered a
  meaningful slice of the lightbox content. There was no way to
  shrink it back without closing the entire surface — and the
  comment thread state went with it.
- **Video playback fought the rest of the UI.** The lightbox
  backdrop stayed at its normal 92 %-opaque black, the right-
  side details pane stayed visible, the comments panel stayed
  expanded. Watching a clip felt like watching a video in a
  cluttered tool, not watching a video.

### Fixed

- **Search now leads with CLIP cosine, FTS is a boost.** Dropped
  the hard keyword-overlap gate. Lowered the cosine floor from
  0.26 → 0.22 so semantic queries without vocabulary anchors
  actually pass through. Weights flipped to 0.65 CLIP / 0.35 FTS
  so a literal filename hit still ranks well but "teacher teaching
  math" can find the whiteboard photo by meaning alone. Relative-
  margin floor loosened from 0.60 → 0.45 so the second- and
  third-best plausible matches aren't suppressed by the top hit.
- **Search history dropdown closes the moment you type.** Empty
  query + focus = recent history + tip; one keystroke = dropdown
  collapses, gallery results are visible.
- **Tags from the preview persist now.** The mapper reads from the
  new `tags` array (and falls back to the legacy `status` column
  for un-migrated rows). `addTag` and `removeTag` call the real
  `attachImageTag` / `detachImageTag` endpoints. The preview also
  refreshes from the live cache row instead of the click-time
  snapshot so the chip you just added stays put through the next
  refetch.
- **Filter dropdown invalidates `["facets"]`** after tag changes
  so new tags appear in the Tags chip group immediately.
- **Person counts** are correct: replaced the correlated UNION
  scalar subquery with a single derived `(image_id, person_id)`
  table + `COUNT(DISTINCT)`. Two persons with disjoint photos now
  show disjoint counts. Regression test makes sure the next time
  this breaks it breaks loudly.
- **EXIF capture date** is now its own column on `images`
  (migration 0033), populated whenever `exif_retention` consent is
  on, independent of GPS retention. Date-range filter (and the
  facets endpoint) COALESCE `captured_at` > `image_geo.taken_at` >
  `uploaded_at` so the filter prefers EXIF when available and
  falls back gracefully. Migration backfills from
  `image_geo.taken_at` for rows that already had one.
- **Upload modal** drops done/error rows on (re)open. In-flight
  rows survive so "Close (keep running)" still keeps visibility.
- **Filter dropdown groups** are now native `<details>`
  collapsibles. Default closed unless the group has an active
  filter; force-open while the Cmd-K search input is non-empty so
  keyboard-first filtering still works. Each summary row shows
  "n options · k active" so the user knows what's selected
  without expanding.
- **Preview crash fix:** PreviewPanel is now fed the live MAPPED
  row from `baseFiles` (looked up by id), not the raw backend
  response. The mapper flattens the `tags` array to label strings
  and keeps the rich `{id, label, color}` shape on a parallel
  `tagRows` mirror used by detach lookups.
- **"Looking up location…"** unsticks itself now. Three matching
  fixes: the `refetchInterval` callback reads `query.state.data`
  per the v5 signature; the Nominatim helper no longer caches
  `None` (transient failure means "retry next time," not "never
  again"); the backfill endpoint defensively re-queues any
  leftover poisoned `None` entries. And on app load, if any geo
  row has coords but no place, the FE auto-fires `/images/geo/
  backfill-places` once so already-uploaded files catch up.
- **Map clustering** swapped to `supercluster` — O(N) build,
  O(visible) per render. Click a cluster → `getClusterExpansionZoom`
  returns the exact level at which it splits; `flyTo` animates
  there in 500 ms. The import is dynamic with a graceful fallback
  to the old pixel-space clusterer so a missing install doesn't
  block the dev server behind a Vite overlay.
- **Map backdrop** is now a subtle 28-px grid at ~6 % contrast
  (light) / 5 % (dark). Reads as designed empty space, not "the
  map broke." Tiles still cover it completely once they resolve.
- **C5.1 setup script** (`scripts/setup.py`) rewritten from
  scratch. Stdlib only (runs before the venv exists). Detects
  Windows / Linux / macOS, enumerates drives, probes for CUDA /
  ROCm / Apple Metal / Intel XPU with the right torch-wheel
  `--index-url` hint. Generates four fresh secrets (JWT, Postgres
  password, MinIO secret key, Fernet `CLOUD_ENCRYPTION_KEY`).
  Writes a 56-key `.env` covering the current surface. `--mode`
  flag picks `docker compose up -d` or a per-platform native
  install checklist with binary-on-PATH detection.
- **Video + audio uploads accept the full common-format list now.**
  Added magic-byte detection for ISO BMFF (mp4 / m4v / mov / m4a)
  with an explicit brand → MIME table that disambiguates the
  `ftyp` box (`isom` / `mp41` / `mp42` / `mp4v` / `avc1` → mp4,
  `M4A` / `M4B` → audio/mp4, `qt` → mov, etc.), plus Matroska
  (mkv / webm), AVI (`RIFF…AVI `), WAV (`RIFF…WAVE`), OGG, Opus
  (sniffed via the OpenHead packet in the first 4 KiB), FLAC, and
  MP3 (both ID3-tagged and raw MPEG frame-sync). Audio gets its
  own validator entry in the dispatch table (passthrough — bytes
  go to originals unmodified).
- **MIME column widened to 128 chars** (migration `0035`). Forward
  path is metadata-only on Postgres; the downgrade refuses to
  truncate any rows whose MIME already exceeds 64 chars, so a
  rollback can't silently break an existing library.
- **Comments collapse to a bubble** instead of being all-or-nothing.
  The 360 px side panel now shrinks to a small floating button on
  the left edge with a count badge; click to expand, click the
  ←-arrow in the header to collapse again. Thread state and draft
  text stay in the TanStack Query cache so reopening is instant —
  no refetch, no lost typing.
- **Video focus mode** kicks in the moment a video starts playing.
  The lightbox backdrop drops to pure black, the comment panel
  auto-collapses to its bubble, and everything that isn't the
  player visually steps back. Pausing or stopping the clip
  restores normal lightbox brightness; the comments stay
  collapsed (the user can re-expand whenever — we don't fight
  their choice). The bubble dims to ~35 % opacity in focus mode
  and brightens on hover so it's reachable without being a
  distraction.

### New features

- **Multi-axis gallery filtering (§C9).** The filter dropdown above
  the gallery now lets you combine: scene (indoor / outdoor /
  CLIP-classified scene labels), content type (photo / screenshot
  / document / etc.), tag, person, date range (two date inputs
  bounded by your library's actual earliest/latest), and the
  existing "has people" / "has location" toggles. All compose;
  you can ask "Indoor photos of Sasha from January 2026" and
  actually get only those. Filters live in the URL so a reload or
  link share preserves the active combination. The `near=lat,lng,
  radius_km` parameter is wired on the backend with a `gps_retention`
  consent gate for power users / future map integration.
- **"Me" binds to your display name (§C4.2).** When you label a
  face cluster (or rename a person) with the literal word "Me",
  the backend substitutes your account display name so AI
  summaries say "Jakub on the beach" instead of "Me on the beach."
  If you haven't set a display name yet, the chip swaps into an
  inline prompt: "What's your name? We'll use it instead of 'Me'."
  One PATCH + retry, no nav-away.
- **One-command self-host setup (§C5.1).** `python scripts/setup.py`
  on a fresh checkout: detects your hardware, generates fresh
  secrets, writes `.env`, and either brings up the docker-compose
  stack or prints the per-platform install commands for Postgres
  16 + pgvector / Redis 7 / MinIO. `--yes` for CI / non-
  interactive, `--reset` to regenerate every secret.
- **EXIF capture date** as a first-class column. Filters and
  facets respect it. Photos taken five years ago and uploaded
  today land in the year they were taken.
- **Themed video player** (Batch 1). Native `<video>` with the
  full control surface a normal player has — scrub bar with a
  buffered underlay so you can see how much loaded, 10-second
  skip back/forward, volume slider with mute, playback speed
  (0.5×–2×), fullscreen, and a keyboard map that matches the
  big video sites: Space/K for play-pause, ←/→ for ±5 s, J/L for
  ±10 s, ↑/↓ for volume, M to mute, F for fullscreen, 0–9 to seek
  to a percent of the file. Controls fade after 2.5 s of mouse-
  quiet during playback and snap back on movement.
- **Audio-autoplay consent** that actually respects browser
  policy. The first time you open a video or audio file, a tiny
  consent strip asks "Play future files with sound automatically?"
  Yes flips a localStorage flag so every subsequent file plays
  with sound the moment it opens; no keeps the muted-autoplay
  default. Video and audio have separate flags — your "play music
  with sound" choice doesn't accidentally unmute every video.
- **Themed audio player** for the same hit list — `.mp3`, `.wav`,
  `.flac`, `.ogg`, `.m4a`, `.aac`, `.opus`. Same control surface
  minus the things that don't apply (no fullscreen, no speed menu
  by default). Renders as a themed card with the file's extension
  as the art chip so the format is identifiable at a glance.
- **CSV / TSV viewer.** Used to be: download the file, open it in
  Excel, scroll through commas. Now: click the file, see it as a
  themed table with a sticky header, zebra rows, row numbers,
  and a hover highlight. The parser is RFC-4180-subset, so quoted
  fields with embedded commas / newlines / escaped quotes work,
  and tab-delimited files auto-detect.
- **ICS calendar viewer.** Open a `.ics` and see the events as
  clean date-grouped cards — title, time range, location, organizer,
  URL, description. Folded long lines unfold, UTC vs local datetimes
  render in your locale, and all-day events get their own visual
  style.
- **VCF contact-card viewer.** Open a `.vcf` and see the contacts
  as named cards with inline base64 photos (or initials if none),
  phones, emails, addresses, URLs, notes — all `tel:` / `mailto:`
  links so one tap dials or composes.
- **Right glyph for every file type.** The gallery card icon and
  the preview hero both pull from a catalog of ~50 extensions —
  video gets a film icon, audio gets the music icon, calendar
  files get a calendar, contact cards get a person, spreadsheets
  get a grid. Unknown types still fall back to the generic
  document, but the common formats look right out of the box.

### Why

A lot of this week is the "the demo's already great, why aren't
people getting the experience we built" category. Search that
required exact words isn't really search; a tag UI that doesn't
save isn't really a UI; a filter dropdown with 50 unsorted chips
isn't really a filter. Each fix here removes one place a user
went "wait, is it broken?" and replaced it with the thing the
product was supposed to do.

The multi-axis filter is the headline feature: every gallery you've
ever used filters by ONE thing at a time (Type, Album, Date). neuthek
filters by all the signals at once because we already have them in
the DB — scene, content type, person, location radius, date range,
tags. Once the UX is in your hands you'll wonder how a photo app
ever worked without it.

C4.2 is small but high-polish: the difference between "AI summary
says 'Me'" and "AI summary uses your real name" is one consent-
checked rename behind the scenes. Felt right to land.

Batch 1 of the "support every file type" effort is the other
substantial piece. neuthek already accepted everything; what was
missing was the experience of *opening* the long tail. Now a
video plays in our own themed player instead of the browser's
generic controls, a CSV renders as a readable table instead of a
wall of commas, a calendar export shows up as events instead of a
text file. Same surface that already handled images / PDFs / code
just got extended to the rest of the formats the typical library
actually contains. More batches coming — office documents (`.docx`,
`.xlsx`, `.pptx`), archive previews, passwords vault, IoT
time-series — but the bones are now in place.

### What this means for you

- **Try the new filter dropdown.** Open the gallery, click Filters
  in the toolbar. Combine People + Scene + a date range. Share
  the resulting URL — the filter combination travels with it.
- **Search by what you mean, not by what you wrote.** "Teacher
  teaching math" will find the whiteboard photo even though no
  caption says "teacher" or "math." Type a phrase, not keywords.
- **Add tags from the preview again.** The "Add tag" input
  actually saves now. Tags appear in the filter dropdown
  immediately.
- **Mark a face as "Me"** and we'll bind it to your display name
  (or prompt you for one). Future AI summaries will use your real
  name.
- **Self-hosters** — try `python scripts/setup.py` on a fresh
  checkout. One command, fresh secrets, docker compose up or the
  install checklist. Migration `0033` adds `images.captured_at`;
  `alembic upgrade head` picks it up.
- **Open a video.** No more generic browser controls — themed
  player with everything you'd expect plus a few keyboard shortcuts
  (J/L for ±10 s, 0–9 to seek to %, F for fullscreen). First time
  you open one we'll ask about audio-autoplay; say yes and every
  subsequent file plays with sound the moment it opens.
- **Open a `.csv` / `.ics` / `.vcf`.** They render as a readable
  table / event list / contact card respectively, with the
  comments panel available alongside (same way images + PDFs work).
  No download-and-open round trip.
- **Upload an MP4 / MOV / WebM / MKV / MP3 / WAV / FLAC / OGG.**
  They go through now. Before this week the validator quietly
  refused them with "unsupported file type" — wasn't a UI bug,
  the magic-byte sniffer didn't have a branch for video / audio
  containers at all.
- **Upload a `.docx` / `.xlsx` / `.pptx`.** They go through too —
  the Office MIME strings are 66-74 characters long and were
  blowing past a 64-char database column on every insert. Migration
  `0035` (`alembic upgrade head` picks it up) widens those columns
  so the row actually lands.
- **Comments slide out of the way during playback.** When you
  start a video, the comment panel folds into a small floating
  bubble on the left edge and the background goes pure black so
  the only thing you're looking at is the clip. Pause and the
  lightbox returns to normal; the bubble stays there so you can
  expand it back when you want to write something while watching.
- **Collapse the comment panel any time.** Click the ←-arrow in
  the comments header to shrink the panel into a bubble; click
  the bubble to bring it back. Useful when the file is
  landscape-heavy and you want maximum content width.

---

<!-- When the entry above lands in marketing/src/data/updates.ts +
     updates-index.json + sitemap.xml, delete the block above this
     comment and start the next one at the top of this file. -->
