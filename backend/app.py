from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend.api.account import admin_router as admin_router
from backend.api.account import router as account_router
from backend.api.admin import router as admin_dashboard_router
from backend.api.archives import router as archives_router
from backend.api.billing import router as billing_router
from backend.api.cloud import router as cloud_router
from backend.api.comments import router as comments_router
from backend.api.faces import router as faces_router
from backend.api.feedback import router as feedback_router
from backend.api.folders import router as folders_router
from backend.api.images import router as images_router
from backend.api.ocr import router as ocr_router
from backend.api.doctext import router as doctext_router
from backend.api.translate_stream import router as translate_stream_router
from backend.api.tts import router as tts_router
from backend.api.translate_doc import router as translate_doc_router
from backend.api.translate_doc import render_router as render_translated_pdf_router
from backend.api.translate_image import router as translate_image_router
from backend.api.translate_langs import router as translate_langs_router
from backend.api.translate_pdf import router as translate_pdf_router
from backend.similar import router as similar_router
from backend.api.people import router as people_router
from backend.api.search import router as search_router
from backend.api.shares import router as shares_router
from backend.api.sftp import router as sftp_router
from backend.api.storage import router as storage_router
from backend.api.tags import folder_attach_router as tag_folder_attach_router
from backend.api.tags import image_attach_router as tag_image_attach_router
from backend.api.tags import router as tags_router
from backend.api.vault import router as vault_router
from backend.api.email_link import router as email_link_router
from backend.api.two_factor import auth_router as two_factor_auth_router
from backend.api.two_factor import router as two_factor_router
from backend.auth.google_sso import router as google_sso_router
from backend.auth.apple_sso import router as apple_sso_router
from backend.auth.users import auth_backend, cookie_auth_backend, fastapi_users
from backend.config import settings
from backend.consent import router as consent_router
from backend.context import reset_current_user_id, set_current_user_id
from backend.db import engine
from backend.schemas import UserCreate, UserRead, UserUpdate
from backend.security import (
    CsrfOriginMiddleware,
    SecurityControlsMiddleware,
    SecurityHeadersMiddleware,
    validate_production_settings,
)
from backend.storage import storage


# Single source of truth for "origins we trust to make credentialed
# requests against this API." Module-level so `validate_production_
# settings` can cross-check against FRONTEND_BASE_URL at boot, AND
# so `create_app()` and the CSRF middleware read the same list.
#
# F15 — the PRODUCTION origin is derived from `FRONTEND_BASE_URL` (and any
# `CORS_EXTRA_ORIGINS`) so the deployed hostname is trusted BY CONSTRUCTION.
# Previously this list was localhost-only, which made
# `validate_production_settings` fail boot (the prod origin was never present)
# unless an operator hand-edited this source tuple.
_DEV_ORIGINS: tuple[str, ...] = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "tauri://localhost",
)


def _build_allowed_origins() -> tuple[str, ...]:
    origins: list[str] = list(_DEV_ORIGINS)
    fe = (settings.frontend_base_url or "").rstrip("/")
    if fe and fe not in origins:
        origins.append(fe)
    for raw in (settings.cors_extra_origins or "").split(","):
        o = raw.strip().rstrip("/")
        if o and o not in origins:
            origins.append(o)
    return tuple(origins)


ALLOWED_ORIGINS: tuple[str, ...] = _build_allowed_origins()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await validate_production_settings()
    storage.ensure_buckets()
    # §C2 — hourly cloud-sync sweep. Pulls every active CloudLink in
    # the background while the API serves traffic. Disabled when
    # `CLOUD_SYNC_HOURLY_ENABLED=false` so test/dev runs don't fire
    # outbound HTTP from pytest, and when the OAuth credentials
    # aren't configured (the worker wakes up, sees no eligible
    # links, and goes back to sleep).
    import asyncio

    from backend.cloud_sync_worker import run_hourly_sweep

    task: asyncio.Task | None = None
    if settings.cloud_sync_hourly_enabled:
        task = asyncio.create_task(run_hourly_sweep())

    # Cap this process's share of the GPU so it can't starve the ml-worker
    # (and vice-versa) on the shared 12 GB card. See backend/vision/gpu_budget.py.
    try:
        from backend.vision.gpu_budget import apply_process_memory_cap
        apply_process_memory_cap()
    except Exception:
        pass

    # Pre-warm the CLIP text encoder so the first /search?q=... request
    # doesn't pay the 5-10 s cold-load tax. We saw a real "matrix math"
    # query take 9.6 s end-to-end where ~9 s was just torch loading
    # OpenCLIP weights into memory on first call. The warmup runs in a
    # background thread so it doesn't block startup (API serves requests
    # while the model loads — search-only queries that arrive before
    # warmup finishes still pay the cold-load cost, but anything else
    # comes up immediately). Wrapped in a broad except so an [ml]-less
    # build doesn't 503 on startup.
    async def _prewarm_clip():
        try:
            from backend.vision.runtime import encode_text_cached
            await asyncio.to_thread(encode_text_cached, "warm")
        except Exception:
            pass
    asyncio.create_task(_prewarm_clip())

    # Pre-warm NLLB-200 (document/image translate) + Florence-2 (OCR) so the
    # FIRST "Translate document" / "Text in image" request doesn't pay a ~40 s
    # cold model-load in the request process — the streaming translator
    # otherwise shows a dead "Translating…" until the weights land. Same
    # background-thread pattern as CLIP above: the API serves immediately while
    # they warm, and a translate/OCR that arrives mid-warmup shares the
    # in-flight load instead of starting a second one. Broad except so an
    # [ml]-less build doesn't fail startup.
    async def _prewarm_translate_ocr():
        try:
            # Warm the Apache-2.0 translation engine (MADLAD-400 + Opus-MT) —
            # the live path. NOT NLLB (which is CC-BY-NC and now only a
            # fallback), so we don't pin its ~1.2GB of VRAM that MADLAD needs.
            from backend.api.translate_engine import get_translator, translate_text
            await asyncio.to_thread(get_translator)
            # Run ONE throwaway translation so the quantization kernels finish
            # autotuning HERE (startup, in the background) instead of on the
            # user's first "Translate" — that autotune was the long "warming
            # up" stall. After this the model is fully hot + resident.
            await asyncio.to_thread(translate_text, "hello world", "es")
        except Exception:
            logging.getLogger(__name__).warning(
                "translate-engine prewarm failed; will warm on first request",
                exc_info=True,
            )
        try:
            from backend.vision.runtime import get_florence2
            await asyncio.to_thread(get_florence2)
        except Exception:
            pass
        # Release the throwaway-translate's transient activations so the idle
        # footprint is the resident weights only — otherwise ~2-3 GB of reserved
        # pool sits at the process's GPU cap and starves the worker.
        try:
            import torch
            await asyncio.to_thread(torch.cuda.empty_cache)
        except Exception:
            pass
    asyncio.create_task(_prewarm_translate_ocr())

    # §B4 — daily retention sweeper. Before this lived in the
    # lifespan the 30-day original-TTL sweep was only reachable via
    # the superuser endpoint, which meant in practice originals
    # accumulated forever and storage cost ballooned. We now run
    # `sweep_expired_originals` on a 24h tick. Sweeper is idempotent
    # and a no-op when nothing is expired, so the cost on a healthy
    # install is one Postgres query per day.
    from backend.db import SessionLocal
    from backend.retention import (
        sweep_audit_log_anonymize,
        sweep_expired_originals,
        sweep_expired_quarantine,
        sweep_feedback_events,
        sweep_orphan_blobs,
        sweep_scheduled_account_deletes,
    )

    async def _retention_loop():
        # Wait a minute on first boot so we don't compete with the
        # CLIP warmup + cloud-sync sweep for DB connections during
        # the first second of life. Then run all five sweepers
        # serially every 24 h. Each one writes its own audit row;
        # any failure is logged + the loop continues to the next.
        # Before this round only `sweep_expired_originals` was wired
        # to a daily tick — quarantine bytes / consumed feedback
        # rows / aged audit-log rows / scheduled-delete accounts
        # accumulated indefinitely because no cron ever ran them.
        import logging
        log = logging.getLogger(__name__)
        await asyncio.sleep(60)
        while True:
            for name, fn in (
                ("originals", sweep_expired_originals),
                ("quarantine", sweep_expired_quarantine),
                ("feedback", sweep_feedback_events),
                ("audit_anonymize", sweep_audit_log_anonymize),
                ("account_deletes", sweep_scheduled_account_deletes),
                # Orphan-blob sweep walks the MinIO buckets and deletes
                # objects no Image row points at. Catches the leak from
                # failed uploads / re-syncs where the blob was written
                # before the DB row commit, AND any blob a prior delete
                # didn't fully clean up. Runs last because it's the
                # slowest (one full bucket listing per call) and we want
                # the cheaper sweeps to land their audit rows first.
                ("orphans", sweep_orphan_blobs),
            ):
                try:
                    async with SessionLocal() as s:
                        res = await fn(s)
                    log.info("retention.%s: %s", name, res)
                except Exception:
                    log.exception(
                        "retention.%s: sweep crashed — continuing", name,
                    )
            await asyncio.sleep(24 * 60 * 60)

    retention_task = asyncio.create_task(_retention_loop())

    # Production hardening — stuck-pending reaper. Independent of the
    # daily retention loop because it runs on a much tighter cadence
    # (~10 min default). Finds Image rows stuck `pending_summary` /
    # `pending_face_scan` / un-transcoded past a timeout with no active
    # job and re-enqueues a bounded batch, server-side, for ALL users.
    # This is the backstop for the failure mode where the worker (or
    # Redis) was down at upload time, or a worker died mid-process and
    # its job was lost: without it those rows sit pending forever. The
    # Redis dedupe set makes re-enqueueing an already-queued row a no-op,
    # and the queue-side reaper clears leaked dedupe keys first, so this
    # can't double-process. Guarded so a failure never crashes the app.
    async def _stuck_pending_loop():
        import logging
        log = logging.getLogger(__name__)
        from backend.retention import sweep_stuck_pending

        interval = max(
            60, int(getattr(settings, "stuck_pending_reaper_interval_seconds", 600))
        )
        # Initial delay so we don't compete with boot warmup, and so the
        # ml-worker has a moment to drain whatever was already queued.
        await asyncio.sleep(min(interval, 120))
        while True:
            try:
                async with SessionLocal() as s:
                    res = await sweep_stuck_pending(s)
                if (
                    res.requeued_summary
                    or res.requeued_faces
                    or res.requeued_transcode
                ):
                    log.info("retention.stuck_pending: %s", res)
            except Exception:
                log.exception("retention.stuck_pending: sweep crashed — continuing")
            await asyncio.sleep(interval)

    stuck_pending_task: asyncio.Task | None = None
    if getattr(settings, "stuck_pending_reaper_enabled", True):
        stuck_pending_task = asyncio.create_task(_stuck_pending_loop())

    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        retention_task.cancel()
        try:
            await retention_task
        except (asyncio.CancelledError, Exception):
            pass
        if stuck_pending_task is not None:
            stuck_pending_task.cancel()
            try:
                await stuck_pending_task
            except (asyncio.CancelledError, Exception):
                pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="neuthek",
        version="0.1.0",
        description="AI-driven personal storage with privacy-compliant face recognition.",
        lifespan=lifespan,
    )

    # Resolved at app-construction time from the module-level
    # constant declared below. Kept as a local alias so the existing
    # middleware-wiring code below reads unchanged.
    allowed_origins = ALLOWED_ORIGINS
    # Middleware order matters (and is counterintuitive).
    # `add_middleware` does `user_middleware.insert(0, …)`, so the
    # LAST middleware added becomes the OUTERMOST wrapper. The
    # OUTERMOST sees every response on the way out, including
    # 403/401 short-circuits from middlewares INSIDE it.
    #
    # We want CORS to be OUTERMOST so its `Access-Control-Allow-Origin`
    # header lands on every response — including the 403s from
    # CsrfOriginMiddleware. Previously CORS was added first (=
    # innermost), so a Csrf 403 returned WITHOUT CORS headers and
    # the browser surfaced it as "blocked by CORS policy" with no
    # Allow-Origin header, obscuring the real error.
    #
    # Correct order:
    #   1. SecurityControlsMiddleware  (innermost — closest to router)
    #   2. SecurityHeadersMiddleware
    #   3. CsrfOriginMiddleware
    #   4. CORSMiddleware              (outermost — added last)
    app.add_middleware(SecurityControlsMiddleware)
    # Always-on baseline headers (CSP / HSTS / X-Frame / etc.). Sits
    # between Security and Csrf so its headers land on every response,
    # including Csrf 403s and Security 429s.
    app.add_middleware(SecurityHeadersMiddleware)
    # CSRF defence-in-depth: reject mutating requests that present
    # our auth cookie but originate from an Origin we don't trust.
    # See backend/security.py::CsrfOriginMiddleware for the exempt
    # path list (login, health, public share routes).
    app.add_middleware(CsrfOriginMiddleware, allowed_origins=tuple(allowed_origins))
    # CORS added LAST so it's the OUTERMOST wrapper. Every response
    # (including Csrf 403, Security 429, anything from the router)
    # goes through CORS on the way out and picks up the
    # `Access-Control-Allow-Origin` header. Without this ordering,
    # the browser sees responses without the CORS header and
    # surfaces them as generic "blocked by CORS policy" errors
    # that hide the real backend error message.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # X-Source-Lang / X-Target-Lang let the in-image translate UI read the
        # detected→target language pair cross-origin; without exposing them the
        # browser hides them and the FE falsely reports "no text found".
        expose_headers=["X-Original-Expired", "X-Source-Lang", "X-Target-Lang"],
    )

    # CORS-on-500: FastAPI's default 500 handler is invoked from
    # inside Starlette's exception-handling middleware, which sits
    # INSIDE our CORS layer in the middleware stack. When a route
    # raises an unhandled exception, the 500 response does NOT get
    # the Access-Control-Allow-Origin header attached. Browsers then
    # report the error as "blocked by CORS policy: No
    # 'Access-Control-Allow-Origin' header is present on the
    # requested resource" — which is technically true but
    # completely obscures the real bug. The fix is to override the
    # Exception handler so it sets the CORS headers itself before
    # returning the 500 JSON. Now the browser shows {"detail":
    # "internal_server_error"} and the FE can render a useful toast
    # instead of pretending the server is unreachable.
    @app.exception_handler(Exception)
    async def _cors_aware_500(request, exc):  # noqa: ANN001
        import logging
        from fastapi.responses import JSONResponse
        logging.getLogger("backend").exception(
            "unhandled exception on %s %s: %s",
            request.method, request.url.path, exc,
        )
        origin = request.headers.get("origin", "")
        headers: dict[str, str] = {}
        # Only echo the Origin back if it's in the allowlist —
        # don't reflect arbitrary origins on error responses.
        if origin and origin in allowed_origins:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Access-Control-Allow-Credentials"] = "true"
            headers["Vary"] = "Origin"
        # Surface the exception class so we can grep logs, but
        # the user-visible body stays generic — we don't want to
        # leak stack-trace fragments to the browser.
        detail = (
            f"server_error: {type(exc).__name__}"
            if settings.app_env.lower() in ("dev", "test", "local")
            else "internal_server_error"
        )
        return JSONResponse(
            status_code=500,
            content={"detail": detail},
            headers=headers,
        )

    @app.middleware("http")
    async def user_context_boundary(request, call_next):  # noqa: ANN001
        token = set_current_user_id(None)
        try:
            return await call_next(request)
        finally:
            reset_current_user_id(token)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/db")
    async def health_db() -> dict[str, str]:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok", "db": "reachable"}

    app.include_router(
        fastapi_users.get_auth_router(auth_backend),
        prefix="/auth/jwt",
        tags=["auth"],
    )
    # Cookie-based login/logout for browsers (HttpOnly + Secure +
    # SameSite=Lax). Mounted alongside the Bearer JWT routes so
    # programmatic callers keep working; the browser frontend uses
    # /auth/cookie/login. See backend/auth/users.py for cookie attrs.
    app.include_router(
        fastapi_users.get_auth_router(cookie_auth_backend),
        prefix="/auth/cookie",
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate),
        prefix="/auth",
        tags=["auth"],
    )
    # Phase 13 (C6) — forgot-password + email verification flows. These
    # mount POST /auth/forgot-password, POST /auth/reset-password,
    # POST /auth/request-verify-token, POST /auth/verify. UserManager
    # callbacks dispatch the actual transactional mails.
    app.include_router(
        fastapi_users.get_reset_password_router(),
        prefix="/auth",
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_verify_router(UserRead),
        prefix="/auth",
        tags=["auth"],
    )
    # Google Sign-In: /auth/google/login → consent screen,
    # /auth/google/callback → JWT in URL fragment.
    app.include_router(google_sso_router)
    # Sign in with Apple — mirror of the Google SSO surface (/auth/apple/*).
    # Stays "not configured" (503) until the APPLE_* settings are present.
    app.include_router(apple_sso_router)
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate),
        prefix="/users",
        tags=["users"],
    )
    app.include_router(images_router)
    app.include_router(ocr_router)
    # Full document text (Phase 1 of doc translation): GET /images/{id}/text
    # → extracted full text so the preview's Translate tool can render the
    # whole document in any of the 200 NLLB languages.
    app.include_router(doctext_router)
    # Streaming document translation (NDJSON) — the translated text fills in
    # live, chunk-by-chunk, so a long document never feels frozen.
    app.include_router(translate_stream_router)
    # Structured document translation: typed blocks (title/heading/bullet/para)
    # streamed for an in-place, format-preserving render in the center view, +
    # a translated-PDF render endpoint (POST /render-translated-pdf) for download.
    app.include_router(translate_doc_router)
    app.include_router(render_translated_pdf_router)
    # In-image text translation (Google-Lens style): POST /images/{id}/translate-image
    # detects text regions, erases the original, renders the translation in place.
    # Same router also serves POST /images/{id}/translate-image-stream, the NDJSON
    # variant that streams each region's translated text live then the final PNG.
    app.include_router(translate_image_router)
    # Exact-copy PDF translation: POST /images/{id}/translate-pdf redacts the
    # original text in place and reinserts the translation, preserving the real
    # page layout, images, colors, and positions.
    app.include_router(translate_pdf_router)
    # Supported-language catalogue: GET /translate/languages → the full
    # [{"code","name"}] list the engine can produce (source of truth for the
    # FE language picker). Every code is validated through resolve_target so the
    # FE can never send an unmapped code → no silent-English fallback.
    app.include_router(translate_langs_router)
    # Server-side neural TTS (Piper) — POST /tts/speak returns audio/wav for a
    # text chunk in any supported language, so the "Listen" reader has a clear,
    # human voice independent of the user's installed OS voices.
    app.include_router(tts_router)
    app.include_router(similar_router)
    # Browsable archive viewer — list an owned archive's contents and
    # extract a single inner file for inline preview. Read-only sibling
    # of backend.archive_upload (shares its safety inspection).
    app.include_router(archives_router)
    app.include_router(shares_router)
    # SFTP access management (per-user keys + SFTP password + connect info).
    # The SFTP SERVER itself runs in its own `sftp` compose service
    # (backend.sftp_server); this router only manages credentials.
    app.include_router(sftp_router)
    app.include_router(folders_router)
    app.include_router(search_router)
    app.include_router(storage_router)
    app.include_router(consent_router)
    app.include_router(people_router)
    app.include_router(faces_router)
    # Person re-detection — "Find more photos of this person": instant,
    # owner-scoped pgvector KNN over existing face embeddings. Confirming a
    # candidate reuses the IDOR-safe PATCH /people/faces/{id} reassign handler.
    from backend.api.person_redetect import router as person_redetect_router
    app.include_router(person_redetect_router)
    app.include_router(account_router)
    app.include_router(two_factor_router)
    app.include_router(two_factor_auth_router)
    # Passwordless email-link sign-in: /auth/email-link/request +
    # /auth/email-link/consume. Same anti-enumeration posture as
    # /auth/forgot-password; 2FA-aware on consume.
    app.include_router(email_link_router)
    app.include_router(admin_router)
    app.include_router(admin_dashboard_router)
    app.include_router(cloud_router)
    app.include_router(feedback_router)
    app.include_router(billing_router)
    app.include_router(vault_router)
    # §C1.6 — tags CRUD + image/folder attach.
    app.include_router(tags_router)
    app.include_router(tag_image_attach_router)
    app.include_router(tag_folder_attach_router)
    # §G2 — comments on any file (owner or active share recipient).
    app.include_router(comments_router)

    return app


app = create_app()
