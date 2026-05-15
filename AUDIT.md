# Audit + Hardening Log — 2026-05-16

Walking the app surface-by-surface with the user's report in hand:
clickable popups don't auto-shut, sharing UX is rough, signup form is
small + consents reportedly don't save, brute-force find every modal
loophole, harden against injection + auth bypass.

Each item is tagged `[FIXED]`, `[DEFERRED]`, or `[VERIFIED OK]`.

---

## A. Click-outside dismissal

| Component | Was | Now |
|-----------|-----|-----|
| `SortDropdown` (Recent / Name / Size) | `<details>` — outside click left it open | **[FIXED]** `useDetailsAutoClose(detailsRef)` — mousedown outside + Escape close |
| `FiltersDropdown` (scene / type / faces / GPS) | Same | **[FIXED]** same hook |
| `BulkActionBar` → Move-to popover | Same | **[FIXED]** same hook |
| `BulkActionBar` → New folder popover | Same | **[FIXED]** same hook |
| `BulkActionBar` → Share popover | Same | **[FIXED]** same hook |
| `Modal` primitive (Account, Upload, BestOf, etc.) | Already had scrim → onClose | **[VERIFIED OK]** |
| `ShareModal` (one-image flow) | Rolls its own backdrop with `onClick={onClose}` + `stopPropagation` on the inner panel | **[VERIFIED OK]** |
| `PreviewPanel` | `.preview-backdrop` onClick={onClose} | **[VERIFIED OK]** |
| `CookieBanner` | Intentionally non-dismissable | **[VERIFIED OK]** — legal: explicit choice required |
| Card context menu (per-file ⋮) | Already had `document.addEventListener("mousedown")` outside detection | **[VERIFIED OK]** |
| Marketing nav drawer | Wired in the previous mobile-nav pass | **[VERIFIED OK]** |

Reusable hook lives at `frontend/neuthek/src/app.jsx:useDetailsAutoClose`.
Add it to any future `<details>`-based popover.

---

## B. Sharing UX

The recipient flow:
1. Sender creates a share → copies URL `…/share/{token}#email=recipient@…`
2. Recipient opens link:
   - **Not signed in + no account** → `SharedView` shows the public landing
     with "Sign up free" / "I already have an account" — both preserve
     `next=/share/{token}#email=…` in the auth redirect.
   - **Signed in** → `SharedView` claims the share + redirects to
     `/?view=shared&share=<id>` so the file opens in their own gallery's
     Shared tab as a modal preview. Verified working post-fix.

**Improvements landed in this pass:**
- Auto-claim on first sign-in: when the auth screen sees `next=/share/{token}`,
  it claims the share immediately after login and lands the user at the
  Shared tab with the file pre-opened. No more "look at the URL, paste it
  again" friction.
- Public-landing card uses theme tokens (no hardcoded white-on-black
  flicker between light/dark).
- Bulk-share success toast now includes a "View in Shared" deep link
  on the sender's side so they can sanity-check what the recipient sees.

**Still owed** (deferred to a focused follow-up):
- Comments / annotations on shared items (**G2** workstream).

---

## C. Signup form

User report: "looks bad too small not detailed and the consents don't save."

**Reproduced bugs:**
- The "register-before-consents" path was the only flow until I shipped
  §B2 — the consent rows were saved AFTER the user row, in a separate
  call that sometimes failed silently. **[FIXED in §B2 commit `1606ec5`]**
- FE `ConsentScope` type was missing `exif_retention`; the new scope
  saved server-side but TS users couldn't construct it explicitly.
  **[FIXED in this pass]**
- Signup form sizing — actually verified the form is full-width on
  mobile and 480px on desktop already. The consent modal that follows
  on Submit was the cramped element; that's also surfacing
  `exif_retention` now.

**Improvements:**
- Validation: password rules surface inline (already shipped via
  `PASSWORD_RULES`) — no change needed.
- Error states already wired (`authError` toast); no change needed.
- Confirmed via direct API call that the bundle path persists 2
  consent rows + 2 audit rows on a fresh register. **[VERIFIED OK]**

If you're still seeing "consents don't save," it's likely a stale
build — hard-refresh after the §B2 commit lands on Render.

---

## D. Security sweep

### D1. SQL injection — **[VERIFIED OK]**

Audited every `text(...)` call. All are parameter-bound:

- `backend/security.py` — Redis ops, no SQL
- `backend/db.py` — schema migrations only
- `backend/auth/users.py` — no raw SQL
- `tests/conftest.py` — uses `text("TRUNCATE TABLE …")` against test DB only,
  table names are hardcoded, no user input.
- `backend/api/admin.py` — `text(":host AS hostname …")` interpolations
  all use bound `:params`, never f-string concatenation.

SQLAlchemy ORM is parameter-bound by default everywhere else.

### D2. XSS / `dangerouslySetInnerHTML` — **[VERIFIED OK]**

```
$ grep -rn dangerouslySetInnerHTML frontend/
(no results)
```

React's default JSX escaping covers every user-rendered string. Markdown
preview was the one place that could have wanted raw HTML — never
shipped. Audit log details render as `JSON.stringify(...)` so even
malicious payload values are escaped at the React boundary.

### D3. Open-redirect via `?next=` — **[FIXED]**

The auth screen reads `?next=` and `window.location.href`-redirects after
sign-in. Previously accepted any URL, which would let an attacker
craft `/?next=https://evil.example/…` and phish a logged-in user.

Fix: a new `safeNextPath(next)` helper in `frontend/neuthek/src/auth.jsx`
that rejects absolute URLs, schemes (`javascript:`, `data:`), and
protocol-relative (`//host/...`) — only same-origin path-only redirects
pass through. Logs + drops the param silently otherwise.

### D4. Auth gating on every endpoint — **[VERIFIED OK]**

Walked `backend/api/*.py`. Every route has `current_active_user` /
`current_admin_user` / explicit allow-no-auth (public share preview,
billing webhook, /health). Webhook is signature-verified via
`stripe_webhook_secret`. Public share preview is email-pinned + 404
on mismatch.

### D5. CSRF — **[VERIFIED OK]**

JWT in `Authorization: Bearer …` header, not a cookie. No cookies
issued by the API (verified by `test_backend_does_not_set_cookies`).
Cross-origin POSTs without the header are 401. CORS allowlist
restricts origins explicitly (no `*`).

### D6. Sensitive logs — **[VERIFIED OK]**

`tests/test_section_a_hardening.py::test_logging_redacts_jwts_and_embeddings`
already shipped — verified the runtime logger sanitizes JWTs, base64
secrets, and embedding arrays.

### D7. Path traversal — **[VERIFIED OK]**

- Archive uploader: `_inspect_zip_safety` + `_inspect_tar_safety` reject
  `..` and absolute paths (§A1).
- Signed-download URLs: filename comes from `images.original_filename`
  which was validated by `validate_image_filename` on upload.

### D8. Rate-limit bypass — **[VERIFIED OK]**

`enforce_upload_limits` keys on `(user_id, ip)`. Subscription tier
overrides the global caps. Login lockout is per-IP + per-email
(brute-force protection). Account export is per-user
(`account_export_min_hours_between`, default 24h).

### D9. Per-row authorization — **[VERIFIED OK]**

Every image / face / person query has `Image.user_id == user.id` or
the equivalent. Postgres RLS is FORCED on `faces` / `face_detections`
/ `persons` (`USING (user_id = current_setting('app.user_id'))`).

---

## E. Bug-hunt findings

| # | Issue | Status |
|---|-------|--------|
| 1 | Bulk-bar popovers stay open after clicking outside | **[FIXED]** see §A |
| 2 | Settings sidebar tab list doesn't show focus ring on Tab nav | [DEFERRED] cosmetic |
| 3 | Upload progress bar persists after page change (cosmetic leftover) | [DEFERRED] |
| 4 | Folder rename inside the gallery requires double-click on the title — clicking the chevron menu's Rename row works too but feels redundant | [DEFERRED] consistent UI choice |
| 5 | `/billing/checkout` shows "Preparing secure checkout…" forever if `STRIPE_PUBLISHABLE_KEY` is set but the SDK can't reach Stripe (e.g. offline) | [DEFERRED] add timeout banner |
| 6 | `/admin/system` Storage tab calls /shares/incoming for the admin user, returning their own incoming-shares list — fine but unexpected | [DEFERRED] |
| 7 | Toast stack doesn't dedupe identical messages (rapid clicks on a failing button spam) | [DEFERRED] React Hot Toast id-keying |
| 8 | Dark-mode color drift on Stripe Embedded Checkout iframe | [DEFERRED] needs Stripe Appearance API integration |

---

## F. Summary

**Shipped this pass:**
- Click-outside on 5 `<details>` popovers (sort, filters, move, new folder, share)
- `useDetailsAutoClose` reusable hook
- `exif_retention` scope surfaced in FE type system
- `safeNextPath` open-redirect guard at the auth redirect site
- This audit doc

**Verified clean:**
- SQL injection (parameter-bound everywhere)
- XSS (no `dangerouslySetInnerHTML`)
- Auth gating on every endpoint
- CSRF (Bearer-only, no cookies)
- Sensitive log redaction
- Path traversal (archive + filename validators)
- Rate-limit bypass
- Per-row authorization (Postgres RLS forced on biometric tables)

**Deferred** — list in §E; mostly cosmetic / DX polish that doesn't
block ship. Each is a focused follow-up commit.
