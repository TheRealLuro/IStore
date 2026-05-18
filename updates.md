# Updates queue — pending publish

Working draft for the next weekly /updates entry on the marketing site.
Everything below shipped between W20's publish (2026-05-16) and the
end of this week. Drop the next entries you ship into the same
buckets; copy the whole file into `marketing/src/data/updates.ts`
(+ mirror to `updates-index.json` + bump `sitemap.xml`) at end of
week and clear this file.

Last published: **W20 (2026-05-16) — Google Drive sync + Sign in with
Google**. Next entry target: **W22, publish 2026-05-30**. W21 below
covers the engine-plumbing batch; W22 (newest, top of file) covers
filtering, setup tooling, search rewrite, and a long list of UX bugs.

---

## 2026-w22 — Multi-axis filtering, smarter search, fewer "looks broken" bugs

**Slug:** `2026-w22-multi-axis-filtering-and-bug-bundle`
**Week label:** `Week of May 30, 2026`
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

---

## 2026-w21 — Engine plumbing week: fair queue, real Best-Of, dev capacity calculator

**Slug:** `2026-w21-fair-queue-best-of-and-dev-capacity`
**Week label:** `Week of May 23, 2026`
**Tags:** `["fair-queue","best-of","dev-dashboard","aeo","newsletter","email-verification","privacy"]`

### Summary

This week was about everything-but-the-glamour: the per-user fair
queue so one user's "reclassify everything" can't starve the GPU for
everyone else, real Best Of that actually scores your library (the
old one was a marketing mock), email verification on the waitlist so
launch day doesn't spam real humans, a public FAQ page so AI answer
engines can cite us directly, and a Developer tab inside the app
that tells operators bytes-exactly how much hardware they need for N
users.

### Found

- The AI summary banner sometimes stuck at "58 of 59" forever. The
  previous fix only handled the case where the worker recorded a
  failed attempt — rows where the worker crashed *before* recording
  anything stayed pending indefinitely.
- "Delete forever" in the Trash view did nothing. Items were silently
  reported as "skipped" instead of getting hard-deleted.
- Gallery filter chips (Indoor, Outdoor, Has location, document type,
  scene labels) only returned results when the user was at the
  library root. Clicking "Indoor" while inside a folder returned an
  empty gallery even though the count beside the chip said otherwise.
- The People tab showed "29 photos" of Me but the drill-in only
  surfaced 16. Multi-select tagging silently dropped any photo where
  face detection didn't find a face.
- Searching "vibrant" missed every image whose summary said
  "colorful" or "vivid" — the FTS pass only matched exact tokens.
- Search required pressing Enter — no results updated while typing.
- One user firing "Reclassify entire library" on 500 photos blocked
  every other user behind them on the shared ML worker thread.
  Nothing limited how often a user could hit the heavy backfill
  endpoints.
- The Best Of feature was a UI shell with sample Unsplash photos and
  seeded mock scores. It never actually operated on the user's
  library; even if you opened it from a multi-select, the modal
  dropped you on the "Drop a burst here" upload screen.
- After we did wire Best Of to the real backend, every photo
  rendered as an empty tile because `background-image: url(...)`
  can't carry a Bearer token.
- Best Of only had 6 hard-coded use cases — no way to type "vintage
  car" or "garden plants" for your own subject.
- Waitlist signups had no email verification. Anyone could type a
  typo + get pinged at launch as if they were a real account.
- The admin waitlist viewer broke on long student email addresses —
  they overflowed into the next column making the table unreadable.
- The marketing /updates index and per-article pages ran flush to
  the viewport edge on mobile — content was clipped by the screen
  bezel.
- The newsletter dropdown in the admin viewer rendered white text on
  a white background — CSS tokens that didn't exist in the
  marketing theme fell back to invisible.
- The app's sidebar and auth screen used a placeholder octahedron
  icon; the favicon was still Vite's default `vite.svg`.
- The Developer tab originally had a 6-bar storage breakdown plus a
  separate deployment-shapes table — too many widgets to read at a
  glance for the "how much hardware do I need" question. Numbers
  also weren't actually measured; they were guesses.

### Fixed

- Summary banner: any row sitting in "pending but never attempted"
  for >10 minutes now auto-dead-letters, so the counter self-heals
  even for rows where the worker crashed before recording an
  attempt. The `_mark_done` path also stamps `pending_summary=false`
  on failures now so future stuck rows clean themselves up
  immediately.
- Delete-forever: the bulk-delete endpoint was filtering rows by
  `deleted_at IS NULL`, which by definition excluded every trashed
  item. Now `?purge=true` allows already-deleted rows so the Trash
  view's "Delete forever" actually purges.
- Gallery filters: any active filter chip now flips the file query
  to cross-folder scope (the same way Photos/Videos/Documents pills
  already worked). The chips already counted cross-folder, so
  clicking one now matches that count.
- People multi-select tag: new `image_persons` association table
  (migration 0032) writes a manual link for every selected image,
  regardless of whether face detection found a face. The People
  count and the drill-in now read from the same source — both
  numbers will always match.
- Search: 280ms debounce so we fire as you stop typing instead of
  on every keystroke. Enter still works to commit instantly.
- Search synonyms: every query token gets expanded with WordNet
  lemmas plus a small visual-domain overlay for terms WordNet
  doesn't link well in the photographer sense (`vibrant → colorful,
  vivid, bright, saturated, bold`; `sunset → dusk, evening,
  golden-hour, twilight, sundown`). Strict-rank stays on the
  literal query so exact tokens still outrank synonym-only hits.
- ML job queue rewritten as a per-user fair scheduler. Each user has
  their own Redis FIFO list, the worker round-robins between users
  with pending work, and the per-user in-flight cap is 1 so the
  user who just got served goes to the back of the line. Per-user
  queue cap of 1000 stops a script from filling Redis.
- Rate limits on every heavy ML endpoint: backfill-summaries +
  backfill-vision 3/hr/user, resummarize + redetect-faces 30/hr/user,
  detect-and-label 10/hr/user, best-of 30/hr/user. Sized for
  legitimate everyday use but painful to script.
- Best Of: real backend scorer (OpenCV Laplacian sharpness +
  exposure + face-detection confidence + optional CLIP cosine to a
  use-case prompt), measured on the user's actual library, results
  rendered with auth'd blob URLs so images actually display.
- Best Of: 25 preset use-case chips grouped People / Scenes /
  Content / Style, plus a free-text input — type any subject
  ("vintage car", "garden plants") and the backend wraps it as a
  photo prompt for CLIP. A "Scored by:" callout shows the exact
  prompt that ran.
- Waitlist email verification: HMAC-signed token, 7-day TTL,
  Resend HTTP-API integration with console fallback when no key,
  resend endpoint with per-IP + per-email rate limits. Frontend
  message changes to "check your inbox" after signup.
- Admin viewer redesigned: purpose-built `.admin-table` with
  explicit colgroup widths, long emails wrap inside their column,
  new Verified / Newsletter / Status columns with filter pills,
  below 900px the table reshapes into stacked cards.
- Updates pages: wrapped both the index and per-article in
  `.container` (the missing wrapper was the bug). 720px breakpoint
  tightens bucket padding, scales the article title, and stacks
  the older/newer footer nav vertically so long titles don't push
  off-screen.
- Newsletter admin dropdown: replaced undefined CSS tokens with
  hardcoded `#0a0a0a` text on `#fff` background so the option text
  is actually readable.
- App branding: `NeuthekMark` component renders the constellation
  glyph (matched to the marketing-site wordmark + favicon) in the
  sidebar + auth screen. `/favicon.svg` replaces Vite's default.
  Browser tab title bumped to "neuthek — your AI-aware personal
  cloud".
- Developer tab simplified: single Capacity estimate panel that
  outputs storage / RAM / VRAM / CPU / workers / predicted speeds
  for the user count you plug in. Constants retuned with
  measurements from `docker stats` + `nvidia-smi` on the actual
  stack, not back-of-envelope guesses.

### New features

- **Per-user fair queue** — the ML job pipeline now isolates users.
  One user's backfill can't starve another user's upload-time
  summary. Admin gets a Queue tab showing per-user pending depth,
  in-flight counters, rate-limit headroom for every gated endpoint,
  and a Drain button per row.
- **Best Of** — multi-select 2-30 photos in the gallery, click "Pick
  best of burst", get a ranked list with per-criterion breakdown
  (sharpness, exposure, face quality, optional use-case match).
  Three modes: Overall best, Best of burst (clusters similar shots
  by CLIP cosine and picks one keeper per cluster), or For a use
  case (composite × CLIP cosine to a 25-preset prompt or your own
  text). "Keep this one" moves the rest to Trash.
- **Email verification on the waitlist** — Resend HTTP-API
  integration. Signup sends a one-click confirmation; the row stays
  "unverified" until the user clicks. Admin gets a "Resend verify"
  button per row that copies the link to clipboard if no mailer is
  wired.
- **Newsletter broadcast** — admin can publish a weekly update to
  opted-in addresses with one click. Per-recipient unsubscribe
  tokens, RFC 8058 List-Unsubscribe + One-Click-Post headers for
  Gmail compliance, dedup so re-sending the same slug is a no-op
  for already-delivered rows. New `/unsubscribe` SPA page.
- **FAQ page** at `/faq` with 22 Q&As across 9 topics. FAQPage
  JSON-LD with stable `@id` anchors so AI answer engines (ChatGPT,
  Perplexity, Google AI Overview, Bing Copilot) can deep-link
  individual answers. Strengthened Organization / WebSite /
  SoftwareApplication JSON-LD with `knowsAbout`, `slogan`,
  `foundingDate`, `contactPoint`, expanded `featureList`. Homepage
  `SearchAction` so Google's sitelinks search box can render under
  the brand SERP card.
- **Admin Developer tab** — interactive capacity estimate calculator
  (users / photos per user / avg MB / uploads per user per day →
  storage TB, RAM GB, VRAM GB, vCPU + cores, ML workers, predicted
  speeds). Plus a 9-row performance benchmarks reference table
  and the full API surface in a compact code-block panel.
- **Reclassify images** action in Library maintenance — runs the
  CLIP scene/content classifier on every image that's missing
  scene_label / content_type / indoor_outdoor. Mostly populates
  filter-chip metadata for Drive-synced images, which skip vision
  at upload under Drive's Limited Use policy.
- **App brand mark** — `NeuthekMark` constellation glyph in the
  sidebar + auth + favicon. Same artwork as the marketing wordmark.

### Why

A bunch of this week was plumbing nobody sees but everything depends
on. The per-user fair queue is the difference between a system that
works for one tester and a system that works for a hundred. The
rate-limits-with-real-numbers protect the shared GPU from a single
script. Email verification on the waitlist is the difference between
"we'll ping you at launch" being a real promise vs. spam-bot fuel.
And the Best Of feature finally connects to your real library — the
old one was a marketing screenshot, not a working tool.

The Developer tab inside the admin is what we wished we'd had when
people first started asking "how much hardware do I need to host
this for my family / team / school?" The numbers are now measured
from the actual running stack instead of guessed.

### What this means for you

- **Sign up for the waitlist** and you'll get a one-click
  confirmation email. Click it to lock in your spot. Without that
  click your row stays "unverified" and won't get the launch ping —
  same as every legitimate service.
- **If your gallery filter chips only show "Has people"**, open
  Settings → AI features → Library maintenance → Reclassify images.
  The CLIP classifier will re-run on Drive-synced photos that
  skipped vision at upload, and the rest of the filter chips
  (Indoor, Outdoor, Document, Screenshot, etc.) will populate.
- **Try Best Of**: multi-select 2 or more photos in the gallery,
  click "Pick best of burst" in the action bar, type a use case
  (or pick a preset like Portrait / Sunset / Document), and we'll
  score them on sharpness, exposure, face quality, and how well
  each matches your prompt. Keep the winner; the rest move to
  Trash with one click (restorable for 30 days).
- **Search now fires as you type.** Try "vibrant" — you'll match
  photos whose summary actually says "colorful" or "vivid", not
  just "vibrant" literally.
- **Self-hosters and operators**: Admin → Queue shows per-user
  pending depth + rate-limit headroom + a Drain button per user,
  so you can see exactly who's bumping into what. Admin →
  Developer has a live capacity calculator (storage / RAM / VRAM /
  CPU / workers) so you can plan for the next scale-up bracket
  before you hit it.

---

<!-- When the entry above lands in marketing/src/data/updates.ts +
     updates-index.json + sitemap.xml, delete the block above this
     comment and start the next one at the top of this file. -->
