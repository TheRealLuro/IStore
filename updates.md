# Updates queue — pending publish

Working draft for the next weekly /updates entry on the marketing site.
Everything below shipped between W20's publish (2026-05-16) and the
end of this week. Drop the next entries you ship into the same
buckets; copy the whole file into `marketing/src/data/updates.ts`
(+ mirror to `updates-index.json` + bump `sitemap.xml`) at end of
week and clear this file.

Last published: **W20 (2026-05-16) — Google Drive sync + Sign in with
Google**.  Next entry target: **W21, publish 2026-05-23**.

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
