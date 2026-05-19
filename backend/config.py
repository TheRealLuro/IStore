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

    database_url: str = Field(
        default="postgresql+asyncpg://istore:istore@localhost:5432/istore"
    )
    database_url_sync: str = Field(
        default="postgresql+psycopg2://istore:istore@localhost:5432/istore"
    )

    redis_url: str = Field(default="redis://localhost:6379/0")

    minio_endpoint: str = Field(default="localhost:9000")
    minio_access_key: str = Field(default="istore")
    minio_secret_key: str = Field(default="istorepass")
    minio_secure: bool = Field(default=False)
    minio_bucket_originals: str = Field(default="istore-originals")
    minio_bucket_served: str = Field(default="istore-served")
    minio_bucket_faces: str = Field(default="istore-faces")
    minio_bucket_quarantine: str = Field(default="istore-quarantine")
    # C8.2 — fine-tune checkpoint storage. Contains .pkl / .safetensors
    # written by the trainer when D6 fine-tuning eventually lands.
    minio_bucket_models: str = Field(default="istore-models")
    # off | sse-s3 | sse-kms. KMS mode can use distinct key IDs for content
    # and biometric buckets.
    minio_sse_mode: str = Field(default="off")
    minio_sse_kms_key_id_content: str = Field(default="")
    minio_sse_kms_key_id_biometric: str = Field(default="")

    jwt_secret: str = Field(default="dev-only-jwt-secret-CHANGE-IN-PROD")
    jwt_lifetime_seconds: int = Field(default=60 * 60 * 24)

    upload_max_bytes: int = Field(default=200 * 1024 * 1024)
    upload_max_count_per_hour: int = Field(default=300)
    upload_max_bytes_per_day: int = Field(default=10 * 1024 * 1024 * 1024)
    upload_max_image_pixels: int = Field(default=120_000_000)
    upload_max_archive_entries: int = Field(default=5_000)
    upload_max_archive_depth: int = Field(default=10)
    upload_max_archive_ratio: int = Field(default=5)
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

    # ---- Phase 13 (C2) cloud sync — Drive / GitHub / Dropbox / OneDrive ----
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

    # §C2 — GitHub OAuth (second provider). Same "empty → endpoints
    # 503" pattern. The redirect_uri must match the value registered
    # at GitHub → Settings → Developer settings → OAuth Apps.
    github_oauth_client_id: str = Field(default="")
    github_oauth_client_secret: str = Field(default="")
    github_oauth_redirect_uri: str = Field(
        default="http://localhost:8000/cloud/callback/github"
    )

    # §C2 — hourly cloud-sync sweep. The lifespan-managed background
    # task pulls every active CloudLink at this cadence. Set to 0 (or
    # flip cloud_sync_hourly_enabled=false) to disable in test/dev runs
    # that shouldn't issue outbound HTTP.
    cloud_sync_hourly_enabled: bool = Field(default=True)
    cloud_sync_interval_seconds: int = Field(default=3600)

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
