from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_FILE_ENV_NAMES = {
    "DATABASE_URL",
    "DATABASE_URL_SYNC",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "JWT_SECRET",
    "SMTP_PASS",
    "CLOUD_ENCRYPTION_KEY",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "BACKUP_AGE_RECIPIENT",
    "MINIO_SSE_KMS_KEY_ID_CONTENT",
    "MINIO_SSE_KMS_KEY_ID_BIOMETRIC",
    # Stripe billing — secrets only. Publishable key and price IDs
    # are not secret; they ship via plain env. The webhook secret is
    # critical: a leak lets an attacker forge subscription state by
    # POSTing crafted events to `/billing/webhook`.
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    # Shared secret for the marketing-site newsletter forward. A leak
    # would let an attacker subscribe arbitrary emails to the marketing
    # list (spam / list-poisoning), so treat it like other secrets and
    # support the Docker/K8s *_FILE convention.
    "MARKETING_INTERNAL_TOKEN",
    # Sign in with Apple — the .p8 private key that SIGNS the client-secret
    # JWT (ES256). A leak lets an attacker mint client secrets for our
    # Services ID, so treat it like other secrets + support the *_FILE
    # mount convention (APPLE_PRIVATE_KEY_FILE → APPLE_PRIVATE_KEY).
    "APPLE_PRIVATE_KEY",
}


def _apply_file_env_overrides() -> None:
    """Support Docker/Kubernetes-style VAR_FILE secrets before Settings loads.

    Pydantic does not universally expand arbitrary *_FILE environment variables,
    but MinIO and Docker secrets both use that convention. We copy the file
    contents into the normal env key unless the key is already set.
    """
    for name in _FILE_ENV_NAMES:
        if os.getenv(name):
            continue
        file_name = os.getenv(f"{name}_FILE")
        if not file_name:
            continue
        try:
            os.environ[name] = Path(file_name).read_text(encoding="utf-8").strip()
        except OSError:
            # Let production validation fail with a clear missing-secret error.
            continue


_apply_file_env_overrides()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="dev")

    # Defaults: fresh installs land on neuthek-* names. Existing
    # operators whose .env carries the historical `istore` /
    # `istore-*` values keep working unchanged — the env var
    # override wins over these defaults. See `.env.example` for the
    # historical-vs-new mapping.
    database_url: str = Field(
        default="postgresql+asyncpg://neuthek:neuthek@localhost:5432/neuthek"
    )
    database_url_sync: str = Field(
        default="postgresql+psycopg2://neuthek:neuthek@localhost:5432/neuthek"
    )

    redis_url: str = Field(default="redis://localhost:6379/0")

    minio_endpoint: str = Field(default="localhost:9000")
    minio_access_key: str = Field(default="neuthek")
    minio_secret_key: str = Field(default="neuthekpass")
    minio_secure: bool = Field(default=False)
    minio_bucket_originals: str = Field(default="neuthek-originals")
    minio_bucket_served: str = Field(default="neuthek-served")
    minio_bucket_faces: str = Field(default="neuthek-faces")
    minio_bucket_quarantine: str = Field(default="neuthek-quarantine")
    # C8.2 — fine-tune checkpoint storage. Contains .pkl / .safetensors
    # written by the trainer when D6 fine-tuning eventually lands.
    minio_bucket_models: str = Field(default="neuthek-models")
    # VLT-8 — zero-knowledge vault file storage. Objects here are
    # client-side-encrypted ciphertext only (the server never holds the
    # key), kept in their own bucket so they're never mixed with the
    # AI-processed originals/served buckets.
    minio_bucket_vault: str = Field(default="neuthek-vault")
    # off | sse-s3 | sse-kms. KMS mode can use distinct key IDs for content
    # and biometric buckets.
    minio_sse_mode: str = Field(default="off")
    minio_sse_kms_key_id_content: str = Field(default="")
    minio_sse_kms_key_id_biometric: str = Field(default="")

    jwt_secret: str = Field(default="dev-only-jwt-secret-CHANGE-IN-PROD")
    jwt_lifetime_seconds: int = Field(default=60 * 60 * 24)

    upload_max_bytes: int = Field(default=2 * 1024 * 1024 * 1024)  # 2 GB — supports screen-recordings / DVR clips / .mov / .mp4
    # VLT-8 — vault files can be much larger than AI-processed photos
    # (videos, disk images, archives the user wants zero-knowledge). The
    # ciphertext is streamed straight to object storage, so a generous
    # per-file cap is safe; total usage is still bounded by the account
    # quota. Default 5 GB per file.
    vault_upload_max_bytes: int = Field(default=5 * 1024 * 1024 * 1024)
    upload_max_count_per_hour: int = Field(default=300)
    upload_max_bytes_per_day: int = Field(default=100 * 1024 * 1024 * 1024)  # 100 GB/day so a real library sync isn't blocked after the per-file bump
    upload_max_image_pixels: int = Field(default=120_000_000)
    upload_max_archive_entries: int = Field(default=5_000)
    upload_max_archive_depth: int = Field(default=10)
    upload_max_archive_ratio: int = Field(default=100)  # Office files (.docx/.xlsx/.pptx) are zips whose XML compresses 10-50x; real zip bombs are 1000x+ and are still caught by the absolute uncompressed caps below
    # Audit U2 — per-entry + total uncompressed caps. The cumulative
    # ratio above (5) only catches archives where the AVERAGE entry
    # is ≥5× compressed. A single 200 MB bomb hidden among 4999
    # small entries averages to ~4× and slips through. These two
    # caps make it impossible to land more than one upload-budget-
    # worth of uncompressed bytes per entry AND more than two
    # upload-budget-worth across the whole archive.
    upload_max_archive_entry_uncompressed_bytes: int = Field(
        default=200 * 1024 * 1024  # 200 MB — matches upload_max_bytes
    )
    upload_max_archive_total_uncompressed_bytes: int = Field(
        default=400 * 1024 * 1024  # 400 MB — 2× per-entry cap
    )
    # §B4 — how long rejected-upload payloads sit in the quarantine
    # bucket before the retention sweeper deletes them. The audit row
    # written at rejection time persists forever; this controls only
    # the bytes. Default 30 days gives ops a generous forensic
    # window while keeping bucket growth bounded.
    upload_quarantine_retention_days: int = Field(default=30)

    # §B4 — retention sweepers. Each value is "days past which the
    # sweeper acts." All three default to the §B4 spec horizons.
    feedback_retention_days: int = Field(default=90)
    audit_log_retention_days: int = Field(default=365)
    account_delete_grace_days: int = Field(default=30)
    # §B3 — /account/export rate limit. One full export per N hours
    # per user (24h = once a day per §B3).
    account_export_min_hours_between: int = Field(default=24)

    # §A4 — signed-URL lifetime cap. The TTL knob lets operators set a
    # shorter value (e.g. 60s for very sensitive deployments) but the
    # absolute upper bound is **300 seconds (5 minutes)**, enforced in
    # `signed_urls.make_signed_*`. Anything longer would be a regression
    # against the A4 spec, which mandates "expire ≤ 5 min" on every
    # signed download.
    download_url_ttl_seconds: int = Field(default=300)
    require_signed_downloads: bool = Field(default=False)
    # The compile-time cap on `download_url_ttl_seconds`. Exposing it as
    # a constant means tests + `verify_download` can reason about the
    # ceiling without hard-coding 300 in three places.
    download_url_ttl_max_seconds: int = Field(default=300)
    # Streaming URLs (video / audio) live longer than downloads
    # because the URL has to survive a watch session, including
    # pauses + seeks. Default 1 hour; capped at 4 hours so a leaked
    # URL still expires within one workday. The browser can fetch
    # a fresh URL on `error` if a long pause crosses the boundary.
    stream_url_ttl_seconds: int = Field(default=3600)
    stream_url_ttl_max_seconds: int = Field(default=14400)

    security_rate_limits_enabled: bool = Field(default=True)
    auth_rate_limit_per_minute: int = Field(default=5)
    auth_lockout_failures: int = Field(default=5)
    auth_lockout_base_seconds: int = Field(default=60)
    auth_lockout_max_seconds: int = Field(default=15 * 60)

    # Trust `X-Forwarded-For` / `X-Real-IP` for client_ip resolution.
    # Flip ON only when the API sits behind a reverse proxy that strips
    # and re-sets these headers (Caddy, nginx, Cloudflare). Default
    # OFF: when the API is reachable directly, any attacker can spoof
    # the header and bypass every per-IP control (rate-limits, auth
    # lockout, audit `details.ip`).
    trust_proxy_headers: bool = Field(default=False)

    secret_manager: str = Field(default="env_file")
    postgres_at_rest_encryption: str = Field(default="")
    backup_age_recipient: str = Field(default="")

    clip_model_name: str = Field(default="ViT-L-14")
    clip_pretrained: str = Field(default="openai")
    vision_enabled: bool = Field(default=True)

    # Florence-2 replaces BLIP as the captioning model — gives denser
    # detail and bundles OCR via the <OCR> task token, so we don't need a
    # separate easyocr / pytesseract dependency for whiteboard content.
    caption_model_name: str = Field(default="microsoft/Florence-2-large")
    # BLIP kept as a fallback when Florence-2 fails to load on this
    # transformers version.
    caption_fallback_model_name: str = Field(
        default="Salesforce/blip-image-captioning-large"
    )
    # Small instruction LLM that rewrites caption + names + OCR + scene
    # into one natural search-friendly sentence. Replaces regex-based
    # pronoun/grammar fixes.
    rewriter_model_name: str = Field(default="Qwen/Qwen2.5-1.5B-Instruct")
    rewriter_enabled: bool = Field(default=True)
    # ---- Handwriting OCR (TrOCR) ----
    # Florence-2 detects WHERE text sits (bounding boxes) well even on
    # handwriting, but READS cursive poorly. TrOCR
    # (microsoft/trocr-base-handwritten, ~330 MB, Apache-2.0) is a dedicated
    # handwriting line-recognizer: we crop each Florence-detected region and
    # run TrOCR on it to read the line. It runs on **CPU on purpose** — the
    # 8 GB GPU is already near-full with Florence + CLIP + the translator, so a
    # resident GPU model is not affordable; ~1-3 s/line on CPU is the accepted
    # trade for actually reading handwriting. Weights auto-download to
    # ${HF_HOME} on first use (pre-baked in the Dockerfile so the first request
    # doesn't pay the download). Loaded lazily + cached in a module global.
    trocr_model_name: str = Field(default="microsoft/trocr-base-handwritten")
    # Master switch for the TrOCR handwriting recognizer. When True (default)
    # the in-image OCR / translate paths re-read Florence's regions with TrOCR
    # when Florence's own read looks low-confidence (likely cursive); set False
    # to force the pure Florence-only path everywhere (printed-text behaviour).
    ocr_handwriting_enabled: bool = Field(default=True)
    # Dedicated machine-translation model for the OCR "Text" tool's
    # translate action (backend/api/ocr.py). NLLB-200 covers 200 languages;
    # the distilled 600M variant (~2.4 GB) is the cost/quality sweet spot
    # and auto-downloads to ${HF_HOME} on first /translate call. Requires
    # `sentencepiece` (tokenizer) + `langdetect` (source detection); when
    # absent the endpoint falls back to the Qwen rewriter above.
    translate_model_name: str = Field(
        default="facebook/nllb-200-distilled-600M"
    )
    # ---- Apache-2.0 translation engine (commercial-safe; replaces the
    # CC-BY-NC NLLB-200 as the PRIMARY translator). NLLB above stays as the
    # automatic FALLBACK when this engine can't load. See
    # backend/api/translate_engine.py. ----
    # PRIMARY: Google MADLAD-400 3B (Apache-2.0, 400+ languages). T5 seq2seq;
    # translation is triggered by a `<2xx>` target token prefixed to the input.
    # Loaded in 8-bit via bitsandbytes (~3 GB) to fit the 8 GB GPU alongside
    # Florence-2 + CLIP. Weights auto-download to ${HF_HOME} on first translate.
    translate_madlad_model_name: str = Field(default="google/madlad400-3b-mt")
    # Load MADLAD in 4-bit nf4 (~1.65 GB) instead of 8-bit. Default True: 4-bit
    # loads faster, skips the int8 kernel autotuning (the long "warming up"
    # stall), and leaves GPU headroom alongside Florence + the 4-way OCR. Set
    # the TRANSLATE_MADLAD_4BIT env var to 0 for 8-bit (slightly higher quality).
    translate_madlad_4bit: bool = Field(default=True)
    # EXOTIC GAP-FILLER: Helsinki-NLP Opus-MT (Apache-2.0, ~0.3 GB each) for
    # languages MADLAD lacks (notably Tongan). en-mul = EN→many (needs a
    # `>>code<<` target token); mul-en = many→EN.
    translate_opus_en_mul_name: str = Field(
        default="Helsinki-NLP/opus-mt-en-mul"
    )
    translate_opus_mul_en_name: str = Field(
        default="Helsinki-NLP/opus-mt-mul-en"
    )
    summarizer_model_name: str = Field(
        default="sshleifer/distilbart-cnn-12-6"
    )
    summarize_enabled: bool = Field(default=True)
    summarize_doc_max_chars: int = Field(default=20_000)
    # Sentence count is now BART-driven via min/max_length tokens; kept for
    # the sumy fallback path.
    summarize_doc_sentence_count: int = Field(default=3)

    # ---- Phase 14 C2 multi-model image pipeline ----
    # Top-K CLIP concept-vocab matches surfaced to the rewriter prompt.
    concept_vocab_top_k: int = Field(default=15)
    # Cosine similarity floor below which a concept is dropped.
    concept_vocab_threshold: float = Field(default=0.22)
    # Heavy vision-language model is OFF by default — gating because it
    # adds ~10 GB VRAM (InternVL2-4B fp16) on top of Florence-2 + CLIP +
    # Qwen2.5. Enable in `.env` (HEAVY_VLM_ENABLED=true) when running on
    # ≥ 12 GB GPU; CPU users should leave it off.
    heavy_vlm_enabled: bool = Field(default=False)
    heavy_vlm_model: str = Field(default="OpenGVLab/InternVL2-4B")

    # ---- Phase 13 (C6) account recovery + email infra ----
    # Empty smtp_host disables real SMTP delivery — backend.email_send falls
    # back to logging the email body so dev users can copy verification /
    # reset links out of the terminal. Production sets these via the env
    # produced by scripts/setup.py.
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587)
    smtp_user: str = Field(default="")
    smtp_pass: str = Field(default="")
    smtp_from: str = Field(default="neuthek <noreply@neuthek.local>")
    # Used to build verification + password-reset links in transactional
    # emails. The trailing slash is intentionally absent — email_send
    # appends `/verify?token=...` directly.
    frontend_base_url: str = Field(default="http://localhost:5173")
    # Optional comma-separated extra origins trusted for credentialed CORS +
    # CSRF (e.g. a second domain, a staging host, the Tauri shell). The prod
    # frontend origin is derived from `frontend_base_url` automatically (F15) —
    # this is only for additional origins beyond that one.
    cors_extra_origins: str = Field(default="")
    # F7 — when true, boot FAILS in production if the app's Postgres role is a
    # SUPERUSER or has BYPASSRLS (either silently voids row-level security).
    # Default False so existing deployments aren't bricked on upgrade; flip to
    # true AFTER cutting the DSN over to the least-privilege `neuthek_app` role
    # (see deploy/initdb/10-app-role.sql). When false, a superuser connection
    # still logs a loud warning at boot.
    require_least_privilege_db: bool = Field(default=False)
    # F17 — max number of archive (zip/tar/7z/rar) extractions allowed to run
    # at once. Each extraction holds the (≤upload_max_bytes) source in memory
    # plus per-entry decode buffers; a burst of large-archive uploads could
    # otherwise stack their decompression amplification and exhaust memory/CPU.
    archive_extract_concurrency: int = Field(default=2, ge=1, le=16)

    # ---- Phase 13 (C2) cloud sync — Drive (Dropbox hooked, not implemented) ----
    # Symmetric Fernet key used by backend.secret_box to encrypt OAuth
    # refresh tokens at rest. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # If left empty, the cloud-sync endpoints return 503 and never persist
    # plaintext credentials.
    cloud_encryption_key: str = Field(default="")

    # Google Drive OAuth client. Empty values keep the endpoints in the
    # "not configured" state — see todo.md / SETUP.md for how to obtain.
    google_oauth_client_id: str = Field(default="")
    google_oauth_client_secret: str = Field(default="")
    # OAuth callback URL. MUST match the value registered in the Google
    # Cloud Console; defaults to a local-dev backend on port 8000.
    google_oauth_redirect_uri: str = Field(
        default="http://localhost:8000/cloud/callback/google_drive"
    )

    # Google Sign-In (sign in / register with Google on the auth screen).
    # Distinct redirect URI from the cloud-sync flow so the consent
    # screens use different scope sets (`openid email profile` here
    # vs. `drive.readonly` over in cloud sync). Re-uses
    # google_oauth_client_id + google_oauth_client_secret since both
    # flows belong to the same Google Cloud project — only the redirect
    # URI list in the Console needs both entries.
    google_signin_redirect_uri: str = Field(
        default="http://localhost:8000/auth/google/callback"
    )

    # ----- Sign in with Apple (auth-screen SSO, mirrors Google) -----
    # Apple's web OAuth differs from Google in three ways the code handles:
    #   * the "client secret" is a short-lived ES256 JWT we SIGN with the
    #     .p8 private key below (not a static string);
    #   * Apple POSTs the callback (response_mode=form_post) and only ever
    #     returns the user's name on the FIRST authorization;
    #   * the Return URL MUST be https on a registered public domain — Apple
    #     rejects http://localhost, so this only works once deployed.
    # Empty values keep /auth/apple/* in the graceful "not configured"
    # state, exactly like Google.
    apple_client_id: str = Field(default="")   # Services ID, e.g. net.glaa.neuthek.signin
    apple_team_id: str = Field(default="")     # 10-char Apple Team ID
    apple_key_id: str = Field(default="")      # 10-char Key ID for the .p8
    # The .p8 private key CONTENTS (PEM). Prefer APPLE_PRIVATE_KEY_FILE in
    # Docker/K8s (see _FILE_ENV_NAMES); the raw value works for a local .env.
    apple_private_key: str = Field(default="")
    # Must EXACTLY match the Return URL registered on the Services ID. No
    # localhost — Apple requires https on a verified domain.
    apple_signin_redirect_uri: str = Field(default="")

    # §C4.6 — Dropbox OAuth client. Empty values keep the endpoint in
    # "not_configured" state. Register at https://www.dropbox.com/
    # developers/apps → "Scoped access" → "App folder" or "Full
    # Dropbox" depending on how much you want to mirror.
    dropbox_oauth_client_id: str = Field(default="")
    dropbox_oauth_client_secret: str = Field(default="")
    dropbox_oauth_redirect_uri: str = Field(
        default="http://localhost:8000/cloud/callback/dropbox"
    )

    # §C4.6 — iCloud Drive. pyicloud persists per-Apple-ID trust state
    # (cookies, keyring) under this directory; we create one subdirectory
    # per CloudLink (named after the link id) so multiple iCloud
    # accounts on the same neuthek instance stay isolated. Production
    # deployments should mount this as a persistent volume — the trust
    # token Apple issues after 2FA is good for ~30 days and saves the
    # user from re-entering their password on every sync, but only as
    # long as the cookie files survive container restarts.
    pyicloud_cookie_root: str = Field(default="/var/neuthek/pyicloud")

    # §C4.6 — Proton Drive + MEGA. Both providers ride on the rclone
    # binary (the Python clients for these E2E services are fragile
    # — mega.py breaks regularly, proton-python-client lags upstream).
    # We persist one rclone config file per CloudLink under this root
    # so the credentials a user supplies during /cloud/{provider}/start
    # survive container restarts. Mount as a persistent volume in
    # production — the files contain rclone-obscured credentials and
    # are chmod'd to 0600 by the wrapper. Filesystem permissions are
    # the real security boundary (rclone "obscure" is light
    # obfuscation, not encryption).
    rclone_config_root: str = Field(default="/var/neuthek/rclone")

    # §C2 — hourly cloud-sync sweep. The lifespan-managed background
    # task pulls every active CloudLink at this cadence. Set to 0 (or
    # flip cloud_sync_hourly_enabled=false) to disable in test/dev runs
    # that shouldn't issue outbound HTTP.
    cloud_sync_hourly_enabled: bool = Field(default=True)
    cloud_sync_interval_seconds: int = Field(default=3600)
    # Per-socket timeout (seconds) for the Google Drive client. The
    # googleapiclient transport (httplib2) ships with NO socket timeout
    # by default, so a Drive endpoint that accepts the connection then
    # stalls (no bytes, no FIN) wedges the sync worker thread forever —
    # the CS10 retry wrapper only fires on an HTTP *status*, not on a
    # silent hang. Pin a generous-but-finite read timeout so a wedged
    # connection raises (and the retry/skip path takes over) instead of
    # blocking. 120s comfortably covers a large single-file chunk fetch.
    cloud_sync_drive_http_timeout_seconds: float = Field(default=120.0)

    # ---- Stripe billing (migration 0025) ----
    # All empty → billing endpoints return 503 and `users.quota_bytes`
    # falls back to the per-user override or the global default. This
    # is the dev / self-host default; production deploys set the
    # secret + webhook secret + price IDs from the Stripe dashboard.
    #
    # NEVER commit the secret_key or webhook_secret — they live in
    # `.env` (gitignored) or via `*_FILE` mounts (Docker/K8s secrets).
    # publishable_key and price IDs are NOT secret and can ship in
    # plain `.env`.
    stripe_secret_key: str = Field(default="")
    stripe_publishable_key: str = Field(default="")
    stripe_webhook_secret: str = Field(default="")
    # Stripe Price IDs (`price_…`) — one per (tier, interval). Empty
    # values disable upgrades for that tier/interval but don't break
    # the API. Operators paste these from Stripe Dashboard → Products.
    stripe_price_id_pro_monthly: str = Field(default="")
    stripe_price_id_pro_annual: str = Field(default="")
    stripe_price_id_business_monthly: str = Field(default="")
    stripe_price_id_business_annual: str = Field(default="")
    # Where Stripe Embedded Checkout redirects after success. The FE
    # serves a result page that polls /billing/subscription so the
    # plan card flips as soon as the webhook lands.
    stripe_checkout_return_url: str = Field(
        default="http://localhost:5173/billing/return?session_id={CHECKOUT_SESSION_ID}"
    )

    # ---- Marketing-site newsletter integration ----
    # When a signed-in user ticks the newsletter opt-in, the backend
    # records the consent locally AND forwards their (already-verified)
    # email to the marketing site's newsletter list via an internal,
    # token-authenticated HTTP call (see backend/marketing.py).
    #
    # `marketing_base_url` is the marketing Express server's origin
    # (e.g. https://neuthek.com in prod, http://host.docker.internal:5181
    # in local dev when the marketing site runs on the host). Empty
    # disables the forward — the consent is still recorded locally, the
    # marketing call is simply skipped (logged, not an error), so a
    # self-host deploy without the marketing site keeps working.
    marketing_base_url: str = Field(default="")
    # Shared bearer token, matched against INTERNAL_API_TOKEN on the
    # marketing server. Empty disables the forward (same as no base URL).
    # Treated as a secret (see _FILE_ENV_NAMES below for *_FILE support).
    marketing_internal_token: str = Field(default="")
    # Per-call timeout for the marketing subscribe HTTP request. Kept
    # short so a slow/unreachable marketing site never stalls the user's
    # opt-in request (the call is best-effort + fire-and-record anyway).
    marketing_http_timeout_seconds: float = Field(default=8.0)

    # ---- Job-queue reliability (production hardening for scale) ----
    #
    # The ml-worker consumes a Redis list (`neuthek:jobs`). These knobs
    # govern crash-safety, retries, and the watchdog. All are additive
    # and default to safe values; nothing here changes the enqueue
    # contract on the API side.
    #
    # `job_max_attempts` — how many times a single job is allowed to run
    # before it's moved to the dead-letter list (`neuthek:jobs:dead`)
    # instead of being retried forever. A poison job (corrupt blob, model
    # that always OOMs on it) lands in dead-letter after this many tries
    # rather than looping and starving real work.
    job_max_attempts: int = Field(default=5)
    # `job_visibility_timeout_seconds` — a job that's been in the
    # in-flight list (`neuthek:jobs:inflight`) longer than this with no
    # progress is presumed orphaned (worker died mid-process) and is
    # re-queued by the reaper. Must comfortably exceed the slowest real
    # job: a long Florence beam search + Qwen rewrite + HLS transcode can
    # run minutes, so default 30 min.
    job_visibility_timeout_seconds: int = Field(default=1800)
    # `job_watchdog_timeout_seconds` — hard per-job wall-clock cap inside
    # the worker. If a single job exceeds this (a wedged LibreOffice /
    # ffmpeg subprocess that ignores its own timeout, a hung model call),
    # the worker abandons it so one hung job can't permanently stall the
    # single-threaded consumer. Set above the visibility timeout would be
    # pointless; keep it the same order. Default 25 min (< visibility so a
    # watchdog-killed job is still reclaimable by the reaper).
    job_watchdog_timeout_seconds: int = Field(default=1500)
    # `job_retry_backoff_base_seconds` — base for exponential backoff
    # between retries (delay = base * 2**(attempt-1), capped). Keeps a
    # transiently-failing job (e.g. MinIO blip) from hot-looping.
    job_retry_backoff_base_seconds: float = Field(default=5.0)
    job_retry_backoff_max_seconds: float = Field(default=300.0)
    # `stuck_pending_reaper_enabled` — server-side sweep that finds Image
    # rows stuck `pending_summary` / `pending_face_scan` past a timeout
    # with no active job and re-enqueues them (bounded). The companion to
    # the FE `summarize-progress` drainer, but it runs for ALL users and
    # ALL job kinds, server-side, on a timer. Disabled in test so pytest
    # doesn't fire background enqueues.
    stuck_pending_reaper_enabled: bool = Field(default=True)
    # How old a `pending_*` row must be (since upload) before the reaper
    # considers it stuck. Above the normal worst-case processing latency
    # so it never races a job that's legitimately still queued.
    stuck_pending_timeout_seconds: int = Field(default=1800)
    # How many stuck rows the reaper re-enqueues per tick (bounded so a
    # huge backlog drains gradually rather than flooding the queue).
    stuck_pending_batch_size: int = Field(default=200)
    # Reaper tick interval.
    stuck_pending_reaper_interval_seconds: int = Field(default=600)
    # Hard cap on the live queue depth. The API refuses to enqueue NEW
    # background work when the queue is already this deep (backpressure)
    # so a runaway producer can't grow Redis unbounded. The upload itself
    # still succeeds — the row is left `pending_*` and the reaper / FE
    # drainer pick it up once the queue drains. 0 disables the cap.
    job_queue_max_depth: int = Field(default=100_000)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() not in {"dev", "test", "local"}

    @property
    def stripe_enabled(self) -> bool:
        """True when the API has enough Stripe config to actually
        sell something. Used by route handlers to 503 cleanly when
        billing isn't set up (dev / self-host)."""
        return bool(self.stripe_secret_key) and bool(self.stripe_webhook_secret)

    @model_validator(mode="after")
    def _normalize_values(self) -> "Settings":
        self.minio_sse_mode = self.minio_sse_mode.lower().strip()
        self.secret_manager = self.secret_manager.lower().strip()
        return self


settings = Settings()
