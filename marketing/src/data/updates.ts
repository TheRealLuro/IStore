// Weekly update entries shown on /updates and /updates/:slug.
//
// Each entry has the SAME five-section shape so the layout reads
// predictably for non-technical visitors:
//
//   - found:        problems we hit this week — what was broken or
//                   limiting users.
//   - fixed:        what we changed to fix them.
//   - newFeatures:  brand-new capabilities that didn't exist before.
//   - why:          the reason this work mattered, in plain English.
//   - whatTheyDo:   how the new stuff actually shows up for the user.
//
// Each section is `string[]` so the article page can render bullet
// lists. Keep bullets short (one sentence each) and avoid engineering
// jargon. The marketing audience is "someone evaluating where to put
// their photos", not "engineer reading the changelog."
//
// IMPORTANT: when you add or edit an entry, mirror the change into
// `marketing/src/data/updates-index.json`. The Express server reads
// the JSON at request time to inject real article HTML into the
// initial page response — AI crawlers / answer engines (ChatGPT
// browse, Perplexity, Google AI Overview) don't run JS and would
// otherwise see an empty `<div id="root"></div>`.
//
// Title guidance for SEO + AI discovery:
//   - Lead with the concrete thing that shipped (a feature name or
//     a product surface), not a generic phrase. "Google Drive sync"
//     scores in search; "this week's release" does not.
//   - Front-load the most-searched keywords (Google Drive, sign-in,
//     cloud storage, AI photo search, privacy, encryption).
//   - Cap around 70 chars so the title fits a SERP card without
//     truncation.

export interface UpdateBuckets {
  found: string[];
  fixed: string[];
  newFeatures: string[];
  why: string;
  whatTheyDo: string[];
}

export interface UpdateEntry {
  slug: string;
  title: string;
  published: string; // ISO date
  week: string;
  summary: string;
  author?: string;
  tags: string[];
  body: UpdateBuckets;
}

// Newest first.
export const UPDATES: UpdateEntry[] = [
  {
    slug: "2026-w22-fair-queue-best-of-and-dev-capacity",
    title: "Per-user fair queue + real Best Of + dev capacity calculator",
    published: "2026-05-26",
    week: "Week of May 26, 2026",
    summary:
      "This week was everything-but-the-glamour: the per-user fair queue so one user's 'reclassify everything' can't starve the GPU for everyone else, real Best Of that actually scores your library (the old one was a marketing mock), email verification on the waitlist so launch day doesn't spam real humans, a public FAQ page so AI answer engines can cite us directly, and a Developer tab inside the app that tells operators bytes-exactly how much hardware they need for N users.",
    author: "neuthek team",
    tags: ["fair-queue", "best-of", "dev-dashboard", "aeo", "newsletter", "email-verification", "privacy"],
    body: {
      found: [
        "The AI summary banner sometimes stuck at '58 of 59' forever. The previous fix only handled rows where the worker recorded a failed attempt — rows where the worker crashed *before* recording anything stayed pending indefinitely.",
        "'Delete forever' in the Trash view did nothing. Items were silently reported as 'skipped' instead of getting hard-deleted.",
        "Gallery filter chips (Indoor, Outdoor, Has location, scene labels) only returned results at the library root. Clicking 'Indoor' inside a folder returned an empty gallery even though the count beside the chip said otherwise.",
        "The People tab showed '29 photos' of Me but the drill-in only surfaced 16. Multi-select tagging silently dropped any photo where face detection didn't find a face.",
        "Searching 'vibrant' missed every image whose summary said 'colorful' or 'vivid' — the FTS pass only matched exact tokens.",
        "Search required pressing Enter — no results updated while typing.",
        "One user firing 'Reclassify entire library' on 500 photos blocked every other user behind them on the shared ML worker. Nothing limited how often a user could hit the heavy backfill endpoints.",
        "The Best Of feature was a UI shell with sample Unsplash photos and seeded mock scores. It never actually operated on your library; even from a multi-select, the modal dropped you on the 'Drop a burst here' upload screen.",
        "After we did wire Best Of to the real backend, every photo rendered as an empty tile because background-image: url(...) can't carry a Bearer token.",
        "Best Of only had 6 hard-coded use cases — no way to type 'vintage car' or 'garden plants' for your own subject.",
        "Waitlist signups had no email verification. Anyone could type a typo + get pinged at launch as if they were a real account.",
        "The admin waitlist viewer broke on long student email addresses — they overflowed into the next column making the table unreadable.",
        "The marketing /updates index and per-article pages ran flush to the viewport edge on mobile — content was clipped by the screen bezel.",
        "The newsletter dropdown in the admin viewer rendered white text on a white background — CSS tokens that didn't exist in the marketing theme fell back to invisible.",
        "The app's sidebar and auth screen used a placeholder octahedron icon; the favicon was still Vite's default vite.svg.",
        "The Developer tab had a 6-bar storage breakdown plus a separate deployment-shapes table — too many widgets to read at a glance. Numbers were guesses, not measurements.",
      ],
      fixed: [
        "Summary banner: any row sitting 'pending but never attempted' for >10 minutes auto-dead-letters now, so the counter self-heals even for rows where the worker crashed before recording an attempt. The _mark_done path also stamps pending_summary=false on failures so future stuck rows clean themselves up immediately.",
        "Delete-forever: the bulk-delete endpoint was filtering rows by deleted_at IS NULL, which by definition excluded every trashed item. `?purge=true` now allows already-deleted rows so the Trash view's 'Delete forever' actually purges.",
        "Gallery filters: any active filter chip flips the file query to cross-folder scope (the same way Photos/Videos/Documents pills already worked). The chips already counted cross-folder, so clicking one now matches that count.",
        "People multi-select tag: new image_persons association table (migration 0032) writes a manual link for every selected image, regardless of whether face detection found a face. The People count and the drill-in read from the same source.",
        "Search debounced to 280 ms so we fire as you stop typing instead of on every keystroke. Enter still works to commit instantly.",
        "Search synonyms: every query token gets expanded with WordNet lemmas plus a small visual-domain overlay for terms WordNet doesn't link well (vibrant → colorful, vivid, bright, saturated, bold; sunset → dusk, evening, golden-hour, twilight, sundown). Strict-rank stays on the literal query so exact tokens still outrank synonym-only hits.",
        "ML job queue rewritten as a per-user fair scheduler. Each user has their own Redis FIFO list, the worker round-robins between users with pending work, and the per-user in-flight cap is 1 so the user who just got served goes to the back of the line. Per-user queue cap of 1000 stops a script from filling Redis.",
        "Rate limits on every heavy ML endpoint: backfill-summaries + backfill-vision 3/hr/user, resummarize + redetect-faces 30/hr/user, detect-and-label 10/hr/user, best-of 30/hr/user. Sized for legitimate everyday use but painful to script.",
        "Best Of: real backend scorer (OpenCV Laplacian sharpness + exposure + face-detection confidence + optional CLIP cosine to a use-case prompt), measured on your actual library. Images render with auth'd blob URLs so they actually display.",
        "Best Of: 25 preset use-case chips grouped People / Scenes / Content / Style, plus a free-text input — type any subject ('vintage car', 'garden plants') and the backend wraps it as a photo prompt for CLIP. A 'Scored by:' callout shows the exact prompt that ran.",
        "Waitlist email verification: HMAC-signed token, 7-day TTL, Resend HTTP-API integration with console fallback when no key, resend endpoint with per-IP + per-email rate limits. Signup confirmation message changes to 'check your inbox.'",
        "Admin viewer redesigned: purpose-built .admin-table with explicit colgroup widths, long emails wrap inside their column, new Verified / Newsletter / Status columns with filter pills, below 900 px the table reshapes into stacked cards.",
        "Updates pages: wrapped both the index and per-article in `.container` (the missing wrapper was the bug). 720 px breakpoint tightens bucket padding, scales the article title, and stacks the older/newer footer nav vertically.",
        "Newsletter admin dropdown: replaced undefined CSS tokens with hardcoded #0a0a0a text on #fff background so the option text is readable.",
        "App branding: NeuthekMark constellation glyph in the sidebar + auth screen (matched to the marketing-site wordmark + favicon). /favicon.svg replaces Vite's default. Browser tab title is now 'neuthek — your AI-aware personal cloud'.",
        "Developer tab simplified to a single Capacity estimate panel that outputs storage / RAM / VRAM / CPU / workers / predicted speeds for the user count you plug in. Constants retuned with measurements from `docker stats` + `nvidia-smi` on the actual stack, not back-of-envelope guesses.",
      ],
      newFeatures: [
        "Per-user fair queue — the ML job pipeline now isolates users. One user's backfill can't starve another user's upload-time summary. Admin gets a Queue tab showing per-user pending depth, in-flight counters, rate-limit headroom for every gated endpoint, and a Drain button per row.",
        "Best Of — multi-select 2-30 photos in the gallery, click 'Pick best of burst', get a ranked list with per-criterion breakdown (sharpness, exposure, face quality, optional use-case match). Three modes: Overall best, Best of burst (clusters similar shots by CLIP cosine and picks one keeper per cluster), or For a use case (composite × CLIP cosine to a 25-preset prompt or your own text). 'Keep this one' moves the rest to Trash.",
        "Email verification on the waitlist — Resend HTTP-API integration. Signup sends a one-click confirmation; the row stays 'unverified' until the user clicks. Admin gets a 'Resend verify' button per row that copies the link to clipboard if no mailer is wired.",
        "Newsletter broadcast — admin can publish a weekly update to opted-in addresses with one click. Per-recipient unsubscribe tokens, RFC 8058 List-Unsubscribe + One-Click-Post headers for Gmail compliance, dedup so re-sending the same slug is a no-op. New /unsubscribe SPA page.",
        "FAQ page at /faq with 22 Q&As across 9 topics. FAQPage JSON-LD with stable @id anchors so AI answer engines (ChatGPT, Perplexity, Google AI Overview, Bing Copilot) can deep-link individual answers. Strengthened Organization / WebSite / SoftwareApplication JSON-LD. Homepage SearchAction so Google's sitelinks search box can render under the brand SERP card.",
        "Admin Developer tab — interactive capacity estimate calculator (users / photos per user / avg MB / uploads per user per day → storage TB, RAM GB, VRAM GB, vCPU + cores, ML workers, predicted speeds). Plus a 9-row performance benchmarks reference table and the full API surface in a compact code-block panel.",
        "Reclassify images action in Library maintenance — runs the CLIP scene/content classifier on every image missing scene_label / content_type / indoor_outdoor. Mostly populates filter-chip metadata for Drive-synced images that skip vision at upload under Drive's Limited Use policy.",
        "App brand mark — NeuthekMark constellation glyph in the sidebar + auth + favicon. Same artwork as the marketing wordmark.",
      ],
      why:
        "A bunch of this week was plumbing nobody sees but everything depends on. The per-user fair queue is the difference between a system that works for one tester and a system that works for a hundred. The rate-limits-with-real-numbers protect the shared GPU from a single script. Email verification on the waitlist is the difference between 'we'll ping you at launch' being a real promise vs. spam-bot fuel. And the Best Of feature finally connects to your real library — the old one was a marketing screenshot, not a working tool. The Developer tab inside the admin is what we wished we'd had when people first started asking 'how much hardware do I need to host this for my family / team / school?' The numbers are now measured from the actual running stack instead of guessed.",
      whatTheyDo: [
        "Sign up for the waitlist and you'll get a one-click confirmation email. Click it to lock in your spot. Without that click your row stays 'unverified' and won't get the launch ping — same as every legitimate service.",
        "If your gallery filter chips only show 'Has people', open Settings → AI features → Library maintenance → Reclassify images. The CLIP classifier will re-run on Drive-synced photos that skipped vision at upload, and the rest of the filter chips (Indoor, Outdoor, Document, Screenshot, etc.) will populate.",
        "Try Best Of: multi-select 2 or more photos in the gallery, click 'Pick best of burst' in the action bar, type a use case (or pick a preset like Portrait / Sunset / Document), and we'll score them on sharpness, exposure, face quality, and how well each matches your prompt. Keep the winner; the rest move to Trash with one click (restorable for 30 days).",
        "Search now fires as you type. Try 'vibrant' — you'll match photos whose summary actually says 'colorful' or 'vivid', not just 'vibrant' literally.",
        "Self-hosters and operators: Admin → Queue shows per-user pending depth + rate-limit headroom + a Drain button per user, so you can see exactly who's bumping into what. Admin → Developer has a live capacity calculator so you can plan for the next scale-up bracket before you hit it.",
      ],
    },
  },
  {
    slug: "2026-w21-end-to-end-security-audit",
    title: "End-to-end security audit: 25 PRs of hardening across the stack",
    published: "2026-05-23",
    week: "Week of May 23, 2026",
    summary:
      "This week was the most thorough security review neuthek has had. Every backend route, the cloud-sync OAuth flow, file uploads, sharing, and infrastructure config got read end-to-end with two static scanners plus per-domain deep review. The result: 152 findings catalogued and triaged, 25 PRs landed, and a stack of static-analysis alerts (CodeQL, Dependabot) closed across backend, marketing, and the SPA. None of this changes how neuthek looks day-to-day — but you'd notice the day someone tried to abuse the things we hardened.",
    author: "neuthek team",
    tags: ["security-audit", "privacy", "cloud-sync", "authentication", "self-host"],
    body: {
      found: [
        "Cloud sync had no per-user storage quota check on the download path: a 100 GB user could trigger a sync against a 10 TB Drive and we'd happily fetch every byte before the disk filled up.",
        "Clicking 'Disconnect' on a Google Drive link only deleted the local record — the refresh token kept living on Google's side until you manually disconnected at myaccount.google.com/permissions.",
        "Two browser tabs (or the hourly cron tick + a manual click) hitting 'Sync now' simultaneously would both walk Drive in parallel and double-import the same files.",
        "Drive API rate-limit responses (429) or server hiccups (5xx) silently skipped files on a sync run — they'd reappear next sweep, but a sustained Google rate window could starve a fresh sync entirely.",
        "The brute-force-protection lockout was keyed only by email, so someone with a list of email addresses could deliberately lockout other users by guessing 5 bad passwords for each.",
        "Resetting your password or disabling 2FA stopped working sessions from being created, but old JWTs issued before the change kept working until their expiry — a long-lived stolen token survived a password rotation.",
        "If a stranger had registered your email + an attacker-chosen password before you ever visited neuthek (without verifying the email), then 'Sign in with Google' for the same address would attach Google to that pre-existing row — a quiet account takeover.",
        "Pagination endpoints accepted any limit value: a request with limit=10_000_000 would dutifully try to materialize ten million rows before falling over.",
        "Manually labelling a face on someone else's photo had no ownership check — a different user could write into your image_persons table from a guessable image ID.",
        "Archive uploads (zip, 7z, rar) had a cumulative compression-ratio cap but no per-entry size cap — a zip with one 50 GB entry would pass the gate.",
        "EXIF strip failures on uploaded photos were swallowed silently, so a malformed image could end up served to recipients with original GPS coordinates intact. Video metadata wasn't being stripped at all.",
        "Share URLs were signed but not bound to the recipient — anyone with the link plus the email could replay it as a different account, and we only audited the share creation event, not each fetch.",
        "Backend containers ran as root, exposed every port on 0.0.0.0 by default, and the production-only config validator didn't catch weak defaults (blank JWT_SECRET, dev-only Postgres password, FRONTEND_BASE_URL missing from ALLOWED_ORIGINS, half-configured Google OAuth).",
        "The marketing site's per-IP rate limiter trusted X-Forwarded-For without bound, so an attacker rotating that header could bypass throttles entirely.",
        "Static-analysis scanners (CodeQL) flagged 30+ alerts across the stack: backend ReDoS in search, OAuth open-redirect on /auth/google/callback and /cloud/callback, stack-trace exposure in admin observability endpoints, marketing missing-rate-limit on 8 routes + URL substring sanitisation, and SPA client-side URL-redirect + http iframe.",
      ],
      fixed: [
        "Cloud-sync quota enforcement runs three layered checks inside the sync loop: pre-flight (refuse to start if you're already at quota), per-entry size gate against Drive's listing-reported size (refuses to download an entry that won't fit), and a post-download safety net (catches lies on missing-size cases). The running budget decrements at the larger of (actual stored bytes, listing-claimed bytes) so a listing under-report can't game the loop.",
        "Disconnecting a cloud link now POSTs to Google's OAuth revoke endpoint before deleting the local row. Best-effort: a hung Google endpoint doesn't stall your click, but a successful revoke means your Google account stops listing neuthek as a connected service. The outcome (revoked / revoke_failed / decrypt_failed / skipped) lands in your audit log.",
        "Per-link Redis mutex (cloud:sync:lock:{link_id}) gates the HTTP route and the hourly cron sweep through the same lock. 30-minute TTL is a crash-safety net; the release path drops the key on success and failure. Gracefully falls back to in-process locking when Redis isn't available (dev-only).",
        "Drive API calls now retry on 429 / 5xx with jittered exponential backoff (1 s → 30 s, 5 attempts, honors Retry-After). Wraps both the listing pass and the per-file download.",
        "Login lockout keys are now {path}:{identity}:{ip} — an attacker spamming bad passwords for someone else's email can only lock out their own IP from that email, not the legitimate user's other devices.",
        "Added token_version to users. The JWT-decode path rejects tokens whose tv claim is older than the current row. Password reset, 2FA disable, and admin-initiated revoke all bump it, so a stolen token dies immediately on credential change.",
        "The SSO email-bind safety predicate now refuses to attach a Google identity to an existing unverified row that has a password (the takeover case). Verified rows + SSO-only rows continue to bind normally.",
        "Every paginated endpoint declares Annotated[int, Query(ge=1, le=500)] (or per-endpoint cap) so FastAPI rejects bad values at the validation boundary before any DB work. limit=10M now 422s instead of OOM-ing.",
        "image_persons writes ownership-check the image IDs up front: an INSERT for an image you don't own is dropped before it touches the table.",
        "Archive extracts enforce per-entry uncompressed cap (200 MB), per-entry ratio check, total uncompressed cap (400 MB), and the existing cumulative ratio. All four gates run before bytes leave the buffer.",
        "EXIF strip failures now raise an exception that the upload endpoint catches as 422 — broken metadata refuses the upload rather than serving it intact. Video uploads pass through ffmpeg with `-map_metadata -1 -c copy` so video metadata strips too.",
        "Signed share URLs now bind the recipient's user_id; an attacker can't replay a leaked link from a different account. Every share-fetch writes an audit row, not just creation.",
        "Containers are now non-root (uid=1000 neuthek), built multi-stage (smaller, faster image), and all dev ports bind to 127.0.0.1 by default. The production-config validator at boot refuses to start if any of a dozen safety conditions fail.",
        "Marketing's rate limiter swapped to express-rate-limit (which CodeQL recognises) + 'trust proxy' tightened from `true` to `1` — closing a real X-Forwarded-For spoofing gap, not just an analyzer warning.",
        "Every CodeQL alert in scope is closed: ReDoS patterns rewritten to linear-time, OAuth `?error=` query params allow-listed (RFC 6749 + OIDC standard codes), system-probe stack-traces redacted from admin responses, http iframes refused unless protocol is https or host is localhost, client-side redirects rebound through URL → pathname so they can only land same-origin.",
        "Audit log details have a 4 KiB cap with a PII-key scrubber (email, password, token, key) so a retention sweep can anonymize old rows without losing structural signal.",
      ],
      newFeatures: [
        "Per-user storage quota actually enforced — cloud_links.status flips to 'over_quota' when you hit your cap, and the hourly worker keeps trying once you free space.",
        "Cloud-sync 'over quota' surface: API returns `{over_quota: true, skipped_over_quota: N}` so the UI can prompt to upgrade or reclaim instead of failing silently.",
        "Production startup validator refuses to boot with weak defaults: blank or short JWT_SECRET, dev Postgres password, dev CLOUD_ENCRYPTION_KEY, FRONTEND_BASE_URL not in ALLOWED_ORIGINS, half-configured Google OAuth (client_id without secret), insecure MinIO config, JWT lifetime > 7 days, missing SMTP host, missing STRIPE_WEBHOOK_SECRET when billing is on.",
        "Audit-log retention sweeper now anonymizes PII keys in `details` JSONB columns as well as the indexed columns — a leak of old audit rows can't reconstruct emails or tokens.",
        "GitHub removed as a cloud-sync provider (public repos turned the gallery into READMEs + build configs); Drive stays the only wired provider.",
        "ffmpeg's `-protocol_whitelist` is now `file` only for user-uploaded media, so a maliciously-crafted video can't trigger an SSRF via `concat:` or `http://`.",
      ],
      why:
        "Security isn't a feature, it's a floor. Every piece of work in this release is something you'd never see in normal use — but you'd notice immediately the day someone tried to abuse it. We don't want to be the personal cloud that did everything right except the one thing. Two findings stood out as 'this would have been bad day one': the cloud-sync quota bypass (a malicious shared file in your Drive could have made us download terabytes you can't store) and the SSO email-takeover (an attacker who pre-registered your email could have ended up with access once you signed in with Google). Both closed. The rest is the steady defense-in-depth work that turns a working product into a trustworthy one — proper container privilege separation, real rate limiting that can't be spoofed, JWT revocation that respects password resets, and the boring-but-critical input-validation caps that stop a single bad request from taking the service down.",
      whatTheyDo: [
        "Nothing to do — every fix applies automatically on the next deploy. New uploads, syncs, shares, and sign-ins all run under the tighter rules from this release.",
        "If you're self-hosting: `git pull && docker compose down && docker compose up -d --build` picks up the new container layer (non-root uid=1000, multi-stage build). On first boot the production-config validator runs; if anything's still on a dev default it'll refuse to start and tell you which env var to set.",
        "If you forked the marketing server.mjs: the per-IP rate limit now requires `trust proxy: 1` (not `true`) to be enforceable. The default in this release is safe; if your override sets `trust proxy: true` an attacker rotating X-Forwarded-For can defeat the throttle.",
        "If you'd noticed any cloud-sync getting stuck mid-walk: the new per-link Redis mutex has a 30-minute TTL that auto-clears stale state, so a `POST /cloud/links/{id}/sync` after a worker crash will just work instead of being permanently blocked.",
        "If you change your password or disable 2FA: every existing JWT for your account is invalidated immediately. The next API call returns 401 and the FE walks you back through sign-in.",
        "Old JWTs from before this deploy continue to work normally — token_version was initialized to 0 on every existing user and only bumps when YOU change a credential. Nothing forces a global re-login.",
      ],
    },
  },
  {
    slug: "2026-w20-google-drive-sync-and-google-sign-in",
    title: "Google Drive sync + Sign in with Google: bring your photos and your account in one click",
    published: "2026-05-16",
    week: "Week of May 16, 2026",
    summary:
      "This week was all about Google. You can now connect Google Drive to pull your existing photos and documents into neuthek without uploading by hand, sign in with Google (or link a Google account to an existing email + password account from Settings), and trust that everything stays opt-in — Drive content is fenced out of AI training by default, and your Google identity is used only to sign you in, never sold or shared.",
    author: "neuthek team",
    tags: ["google-drive", "google-sign-in", "cloud-sync", "ai-photo-search", "privacy"],
    body: {
      found: [
        "Getting an existing photo library into neuthek meant uploading by hand — fine for ten photos, miserable for ten thousand.",
        "If you signed up with email + password, there was no way to also use Sign in with Google for the same account: clicking 'Continue with Google' would create a separate, empty profile.",
        "Signing in with Google failed when you'd previously authorized the app for Drive — Google adds the Drive scope back on subsequent flows and our token exchange refused the response.",
        "Once Drive was connected, photos and documents synced but nothing ever auto-grouped them into folders that matched your Drive layout.",
        "After connecting Drive, the AI summary counter would sometimes camp at, say, 58 of 59 forever because one image returned no caption and got requeued in a loop.",
      ],
      fixed: [
        "Built one-click Google Drive sync — Settings → Cloud sync → Connect Google Drive. We use the read-only `drive.readonly` scope (we can never write to your Drive), encrypt the refresh token at rest with Fernet, and run an hourly background sweep so new files arrive automatically.",
        "Settings → Account → Sign-in & security now has a Link Google button that attaches a Google identity to an existing neuthek account. Sign in with Google from the auth screen now lands in the same account.",
        "Token-exchange failures fixed by setting `OAUTHLIB_RELAX_TOKEN_SCOPE=1` so Google's scope expansion stops being treated as a mismatch. The id_token is still verified against Google's keys.",
        "Drive sync now mirrors your Drive folder tree into neuthek — a top-level 'Google Drive' folder with every sub-folder preserved underneath, so your library looks like your Drive looks.",
        "AI summaries stop retrying photos the model can't caption. Counter drains to N/N instead of getting stuck.",
      ],
      newFeatures: [
        "Google Drive sync (read-only, PKCE OAuth, encrypted refresh tokens, hourly sweep, conflict detection).",
        "Sign in with Google on the auth screen — one click, no email-verification round-trip.",
        "Link Google to an existing neuthek account from Settings.",
        "Per-source AI opt-in toggle in the cloud-sync panel, so Drive content stays out of the AI training pipeline by default (Google Limited Use policy compliance).",
        "Conflict banner in the cloud-sync panel when neuthek detects you edited a file locally after it last synced.",
      ],
      why:
        "If the easiest way to get your photos into neuthek is to download them from Drive and re-upload, you're not going to do it. Drive sync removes that friction. And Sign in with Google removes the friction of remembering another password — but only if it actually lands in the right account, which it now does whether you started with email or with Google. Drive's Limited Use policy also means cloud content can't be used to train AI models, so we kept AI off-by-default for synced files; you choose per-source whether to enable summaries and face recognition.",
      whatTheyDo: [
        "Open Settings → Cloud sync, click Connect Google Drive, grant consent on Google's screen — you come back to a synced library inside a 'Google Drive' folder. Click Sync now to pull immediately, or let the hourly sweep handle it.",
        "On the sign-in screen, click 'Continue with Google'. If you already have a neuthek account with that Google email or with Google linked in Settings, you land in it. If not, you get a fresh account.",
        "Open Settings → Account → Sign-in & security → 'Link Google' to attach Google sign-in to your current account. The row flips to 'Unlink' once linked.",
        "Inside Settings → Cloud sync, toggle 'Enable AI features for Google Drive files' per source if you want AI summaries and face detection to run on synced photos. Default is off.",
      ],
    },
  },
  {
    slug: "2026-w19-marketing-site-launch-and-bug-hunt",
    title: "Marketing site launch + 30+ bug fixes across the engine",
    published: "2026-05-09",
    week: "Week of May 9, 2026",
    summary:
      "We shipped the public neuthek.com marketing site this week — the home page, features, hosting, developers, roadmap, compare-to-the-big-providers, and waitlist pages all went live. While building it we also chased down a long backlog of engine bugs across the gallery, upload pipeline, face recognition, and admin dashboard. Most fixes are invisible — they're the stuff that should have worked the first time.",
    tags: ["marketing-site", "bug-fixes", "stability", "ui-polish", "privacy"],
    body: {
      found: [
        "Uploads crashed with a 'null value in column user_id' database error on every photo upload because the AI-tag insert was missing the owner column the recent multi-user migration required.",
        "Animated GIFs flattened to a single frame on upload because the upload pipeline re-encoded them through Pillow's single-frame save.",
        "Camera RAW files (Nikon NEF, Canon CR2, Sony ARW, Fuji RAF) showed up as soft thumbnails because we were re-encoding the embedded JPEG preview instead of decoding the actual sensor data.",
        "Deleting a photo permanently removed it instead of moving it to Trash, so users had no way to undo.",
        "The Photos / Videos / Documents sidebar pills showed counts for the current folder only, even though those views are conceptually 'all my videos across the library'.",
        "Re-labelling a single face would rename the entire person row, dragging every other photo of that person along with the new name.",
        "The admin dashboard took 25+ seconds to open because every poll walked every object in every storage bucket on the API event loop.",
        "Code files from Google Drive (`.py`, `.js`, `.md`, etc.) had no viewer — they showed up as generic 'document' icons with no preview.",
        "The Settings page showed mock 'face data' and 'location' stats with hard-coded numbers instead of your actual library counts.",
        "The empty-gallery state told you 'No results' even when you had folders visible above (it only checked file count, not folders).",
      ],
      fixed: [
        "AI-tag insert now stamps `user_id` so every uploaded photo gets its CLIP tags without crashing.",
        "Animated GIFs are passthrough end-to-end — no re-encode, no frame loss.",
        "Added rawpy (LibRaw) to decode camera RAW into the full sensor image, then re-encoded at JPEG quality 95. NEF / CR2 / ARW / DNG / RAF / ORF / RW2 / PEF all work.",
        "Delete is now soft by default — items go to Trash with `deleted_at` stamped. The Trash view shows them with a Restore button and a 'Delete forever' button for permanent removal.",
        "Photos / Videos / Documents sidebar tabs are now cross-folder by definition. Counts come from a new `/images/facets` field that aggregates the entire library, not just the current folder.",
        "Face re-labelling clones the underlying face row instead of mutating the shared Person — correcting one mis-grouped photo no longer renames everyone.",
        "Admin dashboard's bucket-walk now lives behind a 60-second LRU cache + a worker thread + a 20,000-object iteration cap. Subsequent opens are ~330x faster (28 s → 86 ms).",
        "Code-file preview opens in a syntax-highlighted PDF-style modal with line numbers (Prism, ~40 grammars eagerly loaded).",
        "Settings → Privacy → Face data and Location panels now show real numbers pulled from `/people/` and `/images/geo`, with copy that explains your embeddings stay on the server you control.",
        "Empty-gallery state checks for folders too — if you have synced Drive folders at root, the gallery shows them instead of falling through to the 'drop files here' CTA.",
      ],
      newFeatures: [
        "Public marketing site at neuthek.com — home, features, hosting, developers, roadmap, compare page, waitlist, privacy + terms.",
        "Waitlist endpoint with Postgres or SQLite storage, admin viewer with Basic Auth, and rate limiting on the public form.",
        "Roadmap page listing what's shipped, what's in progress, and what's planned through to public release.",
        "Compare table showing neuthek next to Google Photos / iCloud / OneDrive / Dropbox on the dimensions that matter (privacy, search by meaning, self-host, compression, AI lock-in).",
        "Drag-rectangle multi-select in the gallery: click empty space and drag to select cards.",
        "Cross-folder Detect-Person workflow: select a batch of photos, type a name, neuthek runs face detection on each one and assigns every newly-found face to that person.",
      ],
      why:
        "Two big motivations this week. First, neuthek needed a place on the public web where you could read what it is, decide if it's for you, and join the launch waitlist — without us having to slack you the elevator pitch. Second, every week we test the engine end-to-end and a stack of small bugs surfaces; we'd rather burn a week paying them down than let them compound into a system that feels unreliable. None of these individual fixes are dramatic, but together they're the difference between 'half-finished prototype' and 'something I'd actually trust my library with'.",
      whatTheyDo: [
        "Visit neuthek.com to read about the product, see the roadmap, compare to the big providers, and join the waitlist.",
        "Upload any photo — including animated GIFs, camera RAW (NEF / CR2 / ARW / etc), and HEIC — without the upload silently failing or losing data.",
        "Delete a photo and see it show up in the Trash view. Restore it or delete it forever from there.",
        "Click Photos / Videos / Documents in the sidebar to see your full library across every folder.",
        "On a desktop, click and drag across the gallery to multi-select. Use the Detect person dropdown in the multi-select bar to tag a batch of photos at once.",
        "Open the admin dashboard without waiting half a minute.",
        "Open a code file synced from Drive and read it with syntax highlighting and line numbers.",
      ],
    },
  },
];

export function findUpdateBySlug(slug: string): UpdateEntry | undefined {
  return UPDATES.find((u) => u.slug === slug);
}

export function allTags(): string[] {
  const set = new Set<string>();
  UPDATES.forEach((u) => u.tags.forEach((t) => set.add(t)));
  return Array.from(set).sort();
}
