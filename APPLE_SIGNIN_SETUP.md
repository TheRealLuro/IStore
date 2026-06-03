# Sign in with Apple — setup guide

The code is **done and wired** (mirrors Google SSO). It stays a graceful
"coming soon" button until the 5 `APPLE_*` settings below are present, then
the **"Continue with Apple"** button goes live automatically (the auth screen
checks `GET /auth/apple/enabled` on load).

> **Hard requirement:** Apple rejects `http://localhost` return URLs. Sign in
> with Apple only works on a **public HTTPS domain** (your deployment, or an
> HTTPS tunnel like ngrok/cloudflared pointed at the backend). You can't test
> the full flow on plain localhost — that's an Apple platform rule, not a bug.

---

## 1. In the Apple Developer console (developer.apple.com → Certificates, IDs & Profiles)

You need an active **Apple Developer Program** membership ($99/yr).

1. **App ID** — under *Identifiers*, create (or reuse) an App ID and enable
   the **Sign in with Apple** capability.
2. **Services ID** — create a new *Services ID* (this becomes
   `APPLE_CLIENT_ID`, e.g. `net.glaa.neuthek.signin`).
   - Enable **Sign in with Apple**, click *Configure*.
   - **Primary App ID:** the App ID from step 1.
   - **Domains and Subdomains:** your domain, e.g. `neuthek.net`.
   - **Return URLs:** the EXACT callback, e.g.
     `https://neuthek.net/auth/apple/callback`
     (must match `APPLE_SIGNIN_REDIRECT_URI` below character-for-character).
3. **Key** — under *Keys*, create a key, enable **Sign in with Apple**,
   download the **`.p8`** file (this is `APPLE_PRIVATE_KEY`). Note the
   **Key ID** (`APPLE_KEY_ID`). You can only download the `.p8` once.
4. **Team ID** — shown top-right of the developer console / in *Membership*
   (this is `APPLE_TEAM_ID`).
5. (For private-relay emails to receive your mail) optionally configure the
   **email relay domain** under *More → Configure Sign in with Apple for Email*.

## 2. Set these env vars (`.env` or Docker/K8s secrets), then redeploy

| Variable | Value |
|---|---|
| `APPLE_CLIENT_ID` | the **Services ID** (e.g. `net.glaa.neuthek.signin`) |
| `APPLE_TEAM_ID` | your 10-char **Team ID** |
| `APPLE_KEY_ID` | the 10-char **Key ID** of the `.p8` |
| `APPLE_PRIVATE_KEY` | the **contents** of the `.p8` (PEM). Or mount the file and set `APPLE_PRIVATE_KEY_FILE` to its path (Docker secret style). |
| `APPLE_SIGNIN_REDIRECT_URI` | `https://<your-domain>/auth/apple/callback` — must match the Services ID Return URL exactly. **https, no localhost.** |

`APPLE_PRIVATE_KEY` accepts either a real multi-line PEM or a single-line
value with `\n` escapes (both are normalized). Prefer `APPLE_PRIVATE_KEY_FILE`
in production — it's registered as a `*_FILE` secret like the Stripe keys.

## 3. That's it

On the next deploy with those set, `GET /auth/apple/enabled` returns
`{"enabled": true}` and the button activates. No code changes needed.

---

## How it behaves (parity with Google)

- **Sign up with Apple** → a new neuthek account is created with
  `age_confirmed=false`, so the user **still goes through the consent + age
  gate** on first sign-in. Apple sign-up does **not** skip your consents — it
  only removes the password step.
- **Sign in with either** → lookup is by Apple's stable `sub` (stored in the
  new `users.apple_sub` column), then a CR-5-safe email match. A user who
  signed up with email or Google can sign in with Apple if the verified
  emails match. *(Caveat: if they chose Apple's "Hide My Email" relay, the
  email differs, so cross-provider auto-linking won't fire — they can link
  Apple from Settings, or it just creates a distinct Apple-identified login.)*
- **2FA** → if the account has neuthek TOTP enabled, Apple sign-in still
  requires the 6-digit code (F1), same as Google.
- **Account linking** → `POST /auth/apple/link` (logged-in) attaches Apple to
  an existing account; `DELETE /auth/apple/link` unlinks.

## What was built
- `migrations/versions/0055_user_apple_sub.py` — `users.apple_sub` (applied)
- `backend/models.py` — the `apple_sub` column
- `backend/config.py` — the `APPLE_*` settings + `APPLE_PRIVATE_KEY` `*_FILE` secret
- `backend/auth/apple_sso.py` — the full flow (`/login`, `/callback` POST,
  `/link`, `/complete-totp`, `/enabled`), reusing Google's audited security
  helpers (HMAC state, CR-5 bind, TOTP pending token, id_token verification
  against Apple's JWKS with the audience pinned to your Services ID)
- `backend/app.py` — router wired
- `frontend/neuthek/src/auth.jsx` — "Continue with Apple" button wired to the
  real flow, gated on `/auth/apple/enabled`
