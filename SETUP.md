# IStore — Operator Setup

This file walks through everything you need to configure beyond `python
scripts/setup.py`. Each section is self-contained — skip the ones that
don't apply to you.

> The fast path: run `python scripts/setup.py`. That generates `.env`
> with strong secrets and prints a checklist. The sections below cover
> the **optional** integrations the script can't do for you because they
> need accounts in third-party services (SMTP relay, Google Cloud, etc.)
> or a deliberate dev decision (promoting an admin).

---

## 1. SMTP — verification + password-reset email

Without SMTP, IStore falls back to **dev mode**: the email body is
printed to the uvicorn terminal. Verification + reset still *work*; you
just have to copy the link out of the terminal yourself. That's fine
for local development. **Production must configure real SMTP.**

### 1a. Pick a provider

Any SMTP relay works. Common picks:

| Provider | Free tier | DNS / DKIM |
|---|---|---|
| **Gmail (App Password)** | Yes (≤ 500/day, personal) | None — sender is your Gmail address |
| **Postmark** | 100/mo free trial | Required for `From:` other than your verified domain |
| **SendGrid** | 100/day free | Required |
| **Resend** | 3 000/mo free | Required |
| **Amazon SES** | First 62 000/mo free from EC2 | Required |

For a personal install, Gmail App Password is the easiest. For
production, use a transactional provider with DKIM + SPF.

### 1b. Gmail App Password (15 minutes)

1. Visit <https://myaccount.google.com/security> and enable
   **2-Step Verification** if you haven't.
2. Go to <https://myaccount.google.com/apppasswords>.
3. Create a new app password (name: "IStore"). Copy the 16-char string
   Google gives you.
4. Add to `.env`:

   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=your-address@gmail.com
   SMTP_PASS=the-16-char-app-password
   SMTP_FROM=IStore <your-address@gmail.com>
   FRONTEND_BASE_URL=http://localhost:5173
   ```

5. Restart uvicorn. Test with:

   ```bash
   curl -X POST http://127.0.0.1:8000/auth/forgot-password \
        -H "Content-Type: application/json" \
        -d '{"email":"your-address@gmail.com"}'
   ```

   You should get **HTTP 202** and an email in your inbox within a few
   seconds. The link points back to `FRONTEND_BASE_URL/reset?token=…`.

### 1c. Other providers

The shape is identical — fill in `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
`SMTP_PASS` from your provider's docs. The `From:` address must match
a verified sender on your account or DKIM will reject.

### 1d. Verify it's working

Two ways to confirm:

```bash
# Option A — run the email module directly:
.venv/Scripts/python.exe -c "
import logging; logging.basicConfig(level=logging.INFO)
from backend.email_send import send_email
print(send_email('you@your-domain.com', 'IStore SMTP test', 'It works.'))
"
```

`True` = success. `False` = the SMTP server rejected; check the
exception traceback above the result.

```bash
# Option B — trigger the actual flow:
curl -X POST http://127.0.0.1:8000/auth/forgot-password \
     -H "Content-Type: application/json" \
     -d '{"email":"<your account email>"}'
```

### 1e. Common gotchas

- **Port 25 blocked.** Most home ISPs block outbound 25. Use 587
  (STARTTLS) — that's the default.
- **"Username and Password not accepted" from Gmail.** The standard
  password doesn't work for SMTP; you need an *App Password* from
  step 1b. If 2FA isn't on, App Passwords aren't available.
- **"550 5.7.1 ... DMARC".** Your `From:` domain doesn't match your
  authenticated user and DMARC is enforced. Either use the
  authenticated user as `From:` or set up DKIM/SPF for your domain.

---

## 2. Google Drive sync (C2)

Read-only sync of your Drive images into IStore. Two prerequisites:

1. **Encryption key.** Refresh tokens are encrypted at rest with a
   symmetric Fernet key. Generate one:

   ```bash
   .venv/Scripts/python.exe -c \
     "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Copy the output into `.env`:

   ```
   CLOUD_ENCRYPTION_KEY=<the-44-char-key>
   ```

   **This key is irreversible.** Rotating it invalidates every stored
   refresh token; users will have to re-authorize Drive. Don't lose it.
   Store it in a secret manager in production (Vault, Doppler, AWS
   Secrets Manager — whatever you already use for `JWT_SECRET`).

2. **Google OAuth client.** Steps:

   1. Go to <https://console.cloud.google.com/>.
   2. Create a project (or pick an existing one).
   3. **APIs & Services → Library** → enable **Google Drive API**.
   4. **APIs & Services → OAuth consent screen** →
      - User type: **External** (unless you're on a Workspace tenant —
        then **Internal** is fine and skips the verification dance).
      - App name: IStore, your email, developer email — fill the
        required fields, leave the optional ones blank.
      - Scopes: add `.../auth/drive.readonly`.
      - Test users: add your own Google account (and any others who
        will test before you publish).
   5. **APIs & Services → Credentials → Create Credentials → OAuth
      client ID**:
      - Application type: **Web application**.
      - Authorized redirect URIs: add
        `http://localhost:8000/cloud/callback/google_drive` (and the
        production URL when you deploy).
   6. Copy the client ID + client secret into `.env`:

      ```
      GOOGLE_OAUTH_CLIENT_ID=<id>.apps.googleusercontent.com
      GOOGLE_OAUTH_CLIENT_SECRET=<secret>
      GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8000/cloud/callback/google_drive
      ```

3. Install the cloud extras (if you used the bare `[ml]` install):

   ```bash
   pip install -e ".[cloud]"
   ```

4. Restart uvicorn. In the app, open **Account → Connect Google Drive**.
   You'll be redirected to Google's consent screen, then bounced back
   to the app with a success toast. Click **Sync now** to pull your
   images.

### Privacy guarantees

- **Read-only scope.** We never write back to Drive — no rename, no
  upload, no delete. Your Drive is the source of truth.
- **No AI training.** Per Google's Limited Use policy, files synced
  from Drive aren't used for AI training. We disable AI summary +
  face scan on synced files unless you explicitly opt in per source.
- **Encrypted refresh tokens.** Stored ciphertext-only in Postgres;
  the master key is in `.env`, not the DB. A DB dump alone can't
  decrypt them.

---

## 3. Promoting an admin user (C8)

The `/admin/*` endpoints + the in-app **Admin** button are gated on
`is_superuser=True` on the user row. Promote a user via the DB:

```bash
.venv/Scripts/python.exe -c "
import asyncio
from sqlalchemy import update
from backend.db import SessionLocal
from backend.models import User
async def main():
    async with SessionLocal() as s:
        await s.execute(
            update(User).where(User.email == 'YOUR_EMAIL@example.com')
            .values(is_superuser=True, is_verified=True)
        )
        await s.commit()
asyncio.run(main())
"
```

Refresh the page. The settings cog now shows an **Admin** button next
to it; opens the dashboard with Storage / Users / Audit tabs.

---

## 4. Per-user storage quotas (C8)

Default quota lives in `backend/api/storage.py` (`DEFAULT_QUOTA_BYTES`,
currently 5 GB). Override per user via the admin dashboard:

- Open Admin → **Users** tab.
- Click a row → enter the new quota in bytes (or null to clear).
- Hit Save. The user's storage bar reflects the new cap immediately.

Behind the scenes this is `PATCH /admin/users/{id}/quota` writing
`User.quota_bytes`; `/storage/usage` honors the override.

---

## 5. EXIF GPS / map view (C3)

The map view is gated by an explicit consent scope:

1. The user opens **Account → Privacy** and toggles
   **GPS retention** on.
2. From that point, every newly uploaded image has its EXIF GPS
   extracted and saved into the `image_geo` table.
3. The map view (toolbar toggle next to the search bar) renders the
   points via maplibre-gl + supercluster.

**Already-uploaded files** don't get re-extracted automatically — the
EXIF data still lives in the original blob in MinIO, but pulling it
into `image_geo` requires a backfill job (not built yet). For a manual
backfill, run:

```bash
.venv/Scripts/python.exe -c "
import asyncio
from backend.db import SessionLocal
from backend.image import _exif_gps, fetch_original
from backend.models import Image, ImageGeo
from sqlalchemy import select
async def main():
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Image).where(Image.deleted_at.is_(None), Image.category == 'image')
        )).scalars().all()
        for img in rows:
            try:
                raw, _ = await fetch_original(img)
            except Exception:
                continue
            gps = _exif_gps(raw)
            if not gps:
                continue
            existing = await s.get(ImageGeo, img.id)
            if existing is None:
                s.add(ImageGeo(image_id=img.id, user_id=img.user_id, **gps))
        await s.commit()
asyncio.run(main())
"
```

That's a one-shot — wire it as a proper endpoint when you have time.

---

## 6. Per-scope consent (C4)

Already wired in the UI. The user's first visit shows the BIPA-grade
**ConsentModal** for face recognition. Other scopes
(`gps_retention`, `semantic_search`, `ai_summary`,
`bandit_compression_telemetry`) live in **Account → Privacy** and use
the simpler `POST /consent/{scope}/grant|withdraw` endpoints.

Withdraw is destructive: revoking `gps_retention` deletes every
`image_geo` row for that user immediately (defense in depth — we don't
just stop processing, we drop the data we already have).

---

## 7. Light theme (C7)

Already shipped — no operator action needed. Toggle via the sun/moon
icon in the topbar. Tokens are aligned to Apple HIG system grays
(see `frontend/src/index.css:13-20`).

---

## 8. Drag-and-drop file → folder (C1)

Already wired:

- File cards are `draggable` and emit a custom MIME
  `application/x-istore-image` on drag start.
- Folder cards listen for that MIME on drag-over and drop, calling
  `moveImageToFolder(id, folderId)` which hits
  `PATCH /images/{id}/move`.

No setup. Drag a card from the grid onto a folder card — it disappears
from the current view and appears inside the folder when you click in.

---

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| Backend fails to start with "Status code 204 must not have a response body" | Some `@router.delete(..., status_code=204)` has `-> None` AND the file uses `from __future__ import annotations`. Drop the annotation. |
| Email links don't appear in the inbox AND don't appear in uvicorn logs | `SMTP_HOST=` is unset and uvicorn is silencing non-uvicorn loggers. As of this commit we write to stderr; if you don't see them, check the uvicorn terminal directly (not the file log). |
| `/cloud/links/google_drive` returns 503 with "CLOUD_ENCRYPTION_KEY is not set" | Generate a Fernet key (section 2 step 1) and add it to `.env`. Restart uvicorn. |
| `/cloud/links/google_drive` returns 503 with "Google OAuth client not configured" | Register an OAuth client (section 2 step 2) and set `GOOGLE_OAUTH_CLIENT_ID` + `GOOGLE_OAUTH_CLIENT_SECRET` in `.env`. |
| OAuth callback says "missing_code_or_state" | Google returned an error without `?code`. Most often the user clicked Cancel; less often the redirect URI doesn't match exactly (check trailing slash). |
| Drive sync pulls 0 files even though Drive has photos | Check the user's Drive: only `image/*` MIME types are pulled. Files in shared drives the user doesn't own won't be visible with `drive.readonly`. |
| `/images/geo` returns 422 | You're hitting an old build that registered `/{image_id}` before `/geo`. Rebuild + restart. |
