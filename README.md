# IStore

IStore is a FastAPI-based image storage service that combines authenticated uploads,
object storage, content-aware compression, and optional CLIP-powered image metadata
and semantic search.

> Current status: the backend, migrations, storage integration, compression policy,
> and tests are implemented. The frontend files exist as placeholders and are not
> currently a usable UI.

<table>
  <tr>
    <td><strong>Core Stack</strong><br><br>
      <kbd>FastAPI</kbd> <kbd>PostgreSQL</kbd> <kbd>pgvector</kbd>
      <kbd>MinIO</kbd> <kbd>Redis</kbd> <kbd>Alembic</kbd>
      <kbd>OpenCLIP</kbd> <kbd>Pillow</kbd> <kbd>Docker Compose</kbd>
      <kbd>Pytest</kbd>
    </td>
  </tr>
  <tr>
    <td><strong>Implemented Capabilities</strong><br><br>
      <kbd>JWT auth</kbd> <kbd>image uploads</kbd> <kbd>object storage</kbd>
      <kbd>content-aware compression</kbd> <kbd>CLIP metadata</kbd>
      <kbd>semantic search</kbd> <kbd>database migrations</kbd>
      <kbd>optional ML extras</kbd>
    </td>
  </tr>
  <tr>
    <td><strong>Future Direction</strong><br><br>
      <kbd>consented faces</kbd> <kbd>adaptive compression</kbd>
      <kbd>feedback learning</kbd> <kbd>gallery/search UI</kbd>
      <kbd>GDPR operations</kbd> <kbd>production hardening</kbd>
    </td>
  </tr>
</table>

## Contents

- [What IStore Does Today](#what-istore-does-today)
- [Technology Stack](#technology-stack)
- [Architecture](#architecture)
- [Data And Storage Model](#data-and-storage-model)
- [API Reference](#api-reference)
- [Privacy And Security](#privacy-and-security)
- [Setup](#setup)
- [Development Workflow](#development-workflow)
- [Future Guidelines](#future-guidelines)
- [Contributing](#contributing)
- [Security Disclosure](#security-disclosure)
- [License](#license)

## What IStore Does Today

| Feature | Current behavior |
| --- | --- |
| User accounts | Registration, login, JWT auth, and user routes are provided through FastAPI Users. |
| Authenticated image upload | Users can upload image files through `POST /images/`; uploads are tied to the authenticated user. |
| Image listing and lookup | Users can list their own non-deleted images, fetch metadata by ID, and filter by scene, content type, tag, or indoor/outdoor classification. |
| Original and served downloads | Users can download the original image when retained, or the compressed served variant. |
| Soft delete | `DELETE /images/{image_id}` marks the image as deleted with `deleted_at`; regular image queries exclude deleted images. |
| Object storage | MinIO stores original uploads, served/compressed variants, and creates a separate face bucket placeholder. |
| Metadata storage | PostgreSQL stores users, image metadata, compression decisions, CLIP embeddings, tags, and retention fields. |
| Vector search | pgvector stores 768-dimensional CLIP embeddings and supports semantic image search. |
| Optional vision pipeline | With `[ml]` extras installed, OpenCLIP classifies content type, scene, face likelihood, indoor/outdoor context, tags, and embeddings. |
| Compression policy | Default is WebP quality 82 with the longest side capped at 4096px; high-confidence screenshots, documents, illustrations, and icons use lossless WebP. |
| Migrations | Alembic manages PostgreSQL schema and extensions. |
| Local infrastructure | Docker Compose starts PostgreSQL with pgvector, Redis, and MinIO. |

### Current Limitations

| Area | Current limitation |
| --- | --- |
| Frontend | `frontend/` contains placeholder HTML/CSS files, but no working gallery, upload flow, or search UI. |
| Face identity | The current backend stores face likelihood metadata only. It does not identify people or manage face galleries. |
| Retention cleanup | The schema supports `original_expires_at` and nullable original blob keys, but no automated retention sweeper is currently implemented. |
| Production controls | TLS termination, audit logging, encryption-at-rest policy, malware scanning, backups, CI/CD, and observability are not implemented in this repo. |
| Document/video storage | The current HTTP API is image-focused. General document and video storage APIs are not implemented. |

## Technology Stack

| Technology | Role in IStore |
| --- | --- |
| FastAPI | ASGI API framework for health checks, auth routes, image routes, and search routes. |
| FastAPI Users | User registration, JWT login/logout, and user management route generation. |
| SQLAlchemy asyncio | Async database access layer for application queries. |
| PostgreSQL 16 | Primary relational database for users, image records, tags, and metadata. |
| pgvector | Vector column and cosine search support for CLIP embeddings. |
| Alembic | Database migrations and PostgreSQL extension setup. |
| MinIO | S3-compatible object storage for original images, served images, and face-data bucket scaffolding. |
| Redis | Local service included in Compose for background/job workflows; not heavily used by current app code yet. |
| Pillow | Image decoding, metadata inspection, resize, and WebP/JPEG encoding. |
| pillow-heif | Optional AVIF/HEIF support used by codec helpers when available. |
| imagecodecs + NumPy | Optional JPEG XL support used by codec helpers when available. |
| OpenCLIP / torch | Optional ML pipeline for image embeddings, zero-shot labels, tags, and semantic text query encoding. |
| Docker Compose | Local PostgreSQL, Redis, and MinIO orchestration. |
| Pytest / pytest-asyncio | Unit and async API tests. |

## Architecture

| Component | Path or service | Responsibility |
| --- | --- | --- |
| API app | `backend/app.py` | Creates the FastAPI app, health checks, auth routes, image routes, search routes, and storage bucket startup check. |
| Auth | `backend/auth/users.py` | Configures JWT strategy, FastAPI Users integration, and active-user dependency. |
| Image API | `backend/api/images.py` | Upload, list, metadata lookup, download, and soft-delete endpoints. |
| Search API | `backend/api/search.py` | Semantic search endpoint backed by OpenCLIP text encoding and pgvector cosine distance. |
| Upload pipeline | `backend/image.py` | Decodes uploads, optionally runs vision, chooses compression, writes blobs, and persists metadata. |
| Compression | `backend/codecs.py`, `backend/policy.py` | Codec helpers and deterministic content-aware compression policy. |
| Vision | `backend/vision/` | Lazy-loaded OpenCLIP runtime and zero-shot image processing pipeline. |
| Storage adapter | `backend/storage.py` | MinIO bucket creation, upload, fetch, and delete helpers. |
| Database | `backend/db.py`, `backend/models.py` | Async engine, SQLAlchemy models, user/image/tag tables, and vector columns. |
| Migrations | `migrations/` | PostgreSQL extensions, users/images schema, vision columns, tag tables, vector index, and retention fields. |
| Frontend | `frontend/` | Placeholder static assets; no usable UI is currently implemented. |

### Runtime Services

| Service | Default port | Compose service | Purpose |
| --- | ---: | --- | --- |
| FastAPI app | `8000` | Started manually with `uvicorn` | HTTP API. |
| PostgreSQL + pgvector | `5432` | `postgres` | Relational metadata and vector search. |
| Redis | `6379` | `redis` | Available for future/background workflows. |
| MinIO API | `9000` | `minio` | S3-compatible object storage API. |
| MinIO console | `9001` | `minio` | Browser admin console. |
| Data root | n/a | bind-mounted volumes | Persistent local state under `ISTORE_DATA_ROOT`. |

## Data And Storage Model

| Data | Where it lives | Notes |
| --- | --- | --- |
| Users | PostgreSQL `users` table | FastAPI Users UUID model plus optional `display_name`. |
| Image metadata | PostgreSQL `images` table | Ownership, blob keys, dimensions, MIME types, byte sizes, SHA-256, codec settings, upload/delete timestamps. |
| Original images | MinIO originals bucket | Raw uploaded bytes are stored under a per-user object key while retained. |
| Served images | MinIO served bucket | Compressed output generated by the active compression policy. |
| Face bucket placeholder | MinIO faces bucket | Bucket is created, but identity-level face workflows are not implemented yet. |
| CLIP embeddings | PostgreSQL `images.clip_embedding` | 768-dimensional vector, present only when the vision pipeline runs successfully. |
| Vision metadata | PostgreSQL `images` table | Content type, scene, confidence scores, face likelihood, indoor/outdoor label, and processing timestamp. |
| Tags | PostgreSQL `tags` and `image_tags` | CLIP-derived tags with confidence scores. |
| Retention fields | PostgreSQL `images.original_expires_at`, `images.original_blob_key` | Originals default to a 30-day expiry timestamp; schema allows originals to be removed later. |

### Upload Pipeline

1. Read authenticated upload bytes.
2. Decode the image with Pillow and record dimensions.
3. Optionally run the OpenCLIP vision pipeline when `VISION_ENABLED` is true and `[ml]` dependencies are installed.
4. Pick a compression plan from the deterministic policy.
5. Encode a served variant.
6. Store original and served blobs in MinIO.
7. Persist image metadata, vision metadata, embeddings, and tags in PostgreSQL.

If the optional ML stack is unavailable, uploads still work. The app falls back to
the default compression policy and skips vision-derived fields.

## API Reference

Authentication is required for image and search routes. The generated OpenAPI
docs are available from FastAPI at `/docs` when the app is running.

### Health

| Method | Path | Auth | Behavior |
| --- | --- | --- | --- |
| `GET` | `/health` | No | Returns `{"status": "ok"}`. |
| `GET` | `/health/db` | No | Runs `SELECT 1` against PostgreSQL and returns database reachability. |

### Auth And Users

| Prefix | Source | Behavior |
| --- | --- | --- |
| `/auth/jwt` | FastAPI Users auth router | JWT login/logout routes. |
| `/auth` | FastAPI Users register router | User registration route. |
| `/users` | FastAPI Users users router | Authenticated user-management routes such as current-user reads/updates. |

### Images

| Method | Path | Auth | Behavior |
| --- | --- | --- | --- |
| `POST` | `/images/` | Yes | Upload an image file, store original and served blobs, and return image metadata. |
| `GET` | `/images/` | Yes | List the current user's non-deleted images. Supports pagination and metadata filters. |
| `GET` | `/images/{image_id}` | Yes | Return metadata for one image owned by the current user. |
| `GET` | `/images/{image_id}/original` | Yes | Download the original blob. If the original has expired or was removed, returns the served variant with `X-Original-Expired: true`. |
| `GET` | `/images/{image_id}/served` | Yes | Download the compressed served variant. |
| `DELETE` | `/images/{image_id}` | Yes | Soft-delete the image by setting `deleted_at`. |

List filters supported by `GET /images/`:

| Query parameter | Meaning |
| --- | --- |
| `limit` | Maximum number of images to return. Defaults to `100`. |
| `offset` | Number of images to skip. Defaults to `0`. |
| `scene` | Filter by `scene_label`. |
| `content_type` | Filter by CLIP-derived content type, such as `photo`, `screenshot`, or `document`. |
| `tag` | Filter by a tag label from the `tags` table. |
| `indoor_outdoor` | Filter by `indoor`, `outdoor`, or `unknown` where available. |

### Search

| Method | Path | Auth | Behavior |
| --- | --- | --- | --- |
| `GET` | `/search/?q=...` | Yes | Encodes the text query with OpenCLIP and returns nearest owned images by cosine similarity. Requires `[ml]` dependencies. |

| Query parameter | Meaning |
| --- | --- |
| `q` | Required search text, 1 to 200 characters. |
| `limit` | Maximum number of hits to return. Defaults to `30`. |

## Privacy And Security

IStore handles images and derived ML metadata. Treat both as sensitive user data.

| Concern | Current behavior | Guidance |
| --- | --- | --- |
| User isolation | Image and search queries filter by the authenticated user's `user_id`. | Keep every new data-access path scoped to the current user unless an explicit admin workflow exists. |
| JWT secrets | `JWT_SECRET` is loaded from environment settings. `.env.example` uses a placeholder. | Use a long random secret outside development. Never commit real secrets. |
| Object storage | Originals, served files, and face bucket scaffolding are separated into distinct MinIO buckets. | Bucket separation helps organization, but it is not a substitute for encryption, access policy, or lifecycle controls. |
| EXIF/GPS metadata | Originals keep the uploaded bytes. Some served encoders preserve EXIF where supported. | Assume uploads may contain location, device, and capture-time metadata. Consider stripping or exposing controls before public deployment. |
| CLIP embeddings | Embeddings are stored in PostgreSQL when vision processing succeeds. | Treat embeddings as sensitive derived data because they can encode image content and enable similarity search. |
| Tags and scenes | Tags, content type, scene labels, and indoor/outdoor labels are inferred and persisted. | These labels can reveal private context even when the image itself is not viewed. |
| Face likelihood | The current pipeline stores a likelihood score and `pending_face_scan`; it does not identify people. | Face-related metadata should be handled conservatively and deleted/exported with the source image. |
| Future biometric data | Identity-level face recognition is planned but not implemented. | Face galleries, face templates, and "who is this?" prompts must require explicit consent and strong lifecycle controls. |
| Retention | Schema supports original expiry and nullable original blob keys. | Automatic deletion is not implemented yet; do not rely on retention cleanup until a sweeper exists. |
| Production hardening | TLS, audit logging, encryption-at-rest policy, malware scanning, backups, CI/CD, and observability are not implemented here. | Add these before hosting real user data outside trusted development environments. |

## Setup

### Prerequisites

- Python 3.11 or newer.
- Docker and Docker Compose.
- A running Docker daemon, such as Docker Desktop on Windows/macOS.

### Environment

Copy `.env.example` to `.env` or let the setup scripts create it.

| Variable | Default example | Purpose |
| --- | --- | --- |
| `APP_ENV` | `dev` | Application environment label. |
| `ISTORE_DATA_ROOT` | `./data` | Root directory for persistent local Postgres, Redis, MinIO, model, and backup data. |
| `POSTGRES_USER` | `istore` | PostgreSQL username used by Compose. |
| `POSTGRES_PASSWORD` | `istore` | PostgreSQL password used by Compose. Change outside local dev. |
| `POSTGRES_DB` | `istore` | PostgreSQL database name. |
| `POSTGRES_HOST` | `localhost` | PostgreSQL host for local app connections. |
| `POSTGRES_PORT` | `5432` | PostgreSQL port. |
| `DATABASE_URL` | `postgresql+asyncpg://...` | Async SQLAlchemy database URL used by the app. |
| `DATABASE_URL_SYNC` | `postgresql+psycopg2://...` | Sync database URL used by Alembic. |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL. |
| `MINIO_ENDPOINT` | `localhost:9000` | MinIO S3-compatible API endpoint. |
| `MINIO_ACCESS_KEY` | `istore` | MinIO access key. Change outside local dev. |
| `MINIO_SECRET_KEY` | `istorepass` | MinIO secret key. Change outside local dev. |
| `MINIO_SECURE` | `false` | Whether MinIO should be accessed over HTTPS. |
| `MINIO_BUCKET_ORIGINALS` | `istore-originals` | Bucket for original uploads. |
| `MINIO_BUCKET_SERVED` | `istore-served` | Bucket for compressed served variants. |
| `MINIO_BUCKET_FACES` | `istore-faces` | Bucket reserved for future face workflows. |
| `JWT_SECRET` | placeholder value | JWT signing secret. Must be replaced outside development. |
| `JWT_LIFETIME_SECONDS` | `86400` | JWT lifetime in seconds. |

Additional settings supported by `backend/config.py`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLIP_MODEL_NAME` | `ViT-L-14` | OpenCLIP model name used by the optional vision pipeline. |
| `CLIP_PRETRAINED` | `openai` | OpenCLIP pretrained weights identifier. |
| `VISION_ENABLED` | `true` | Enables the optional vision pipeline when ML dependencies are available. |

### Windows PowerShell

```powershell
./scripts/setup.ps1
```

Useful setup flags:

| Flag | Meaning |
| --- | --- |
| `-Ml` | Install CPU torch and OpenCLIP extras for the vision/search pipeline. |
| `-Gpu cu128` | Pre-install GPU torch from the CUDA 12.8 wheel index. |
| `-Gpu cu126` | Pre-install GPU torch from the CUDA 12.6 wheel index. |
| `-Gpu cu124` | Pre-install GPU torch from the CUDA 12.4 wheel index. |
| `-Start` | Start Uvicorn in the foreground after setup completes. |

Start the API manually:

```powershell
.venv/Scripts/python.exe -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

### Linux Or macOS

```bash
./scripts/setup.sh
```

Useful setup flags:

| Flag | Meaning |
| --- | --- |
| `--ml` | Install CPU torch and OpenCLIP extras for the vision/search pipeline. |
| `--gpu cu128` | Pre-install GPU torch from the CUDA 12.8 wheel index. |
| `--gpu cu126` | Pre-install GPU torch from the CUDA 12.6 wheel index. |
| `--gpu cu124` | Pre-install GPU torch from the CUDA 12.4 wheel index. |
| `--start` | Start Uvicorn in the foreground after setup completes. |

Start the API manually:

```bash
.venv/bin/python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

### Common Operations

| Task | Command |
| --- | --- |
| Start infrastructure | `docker compose up -d` |
| Stop infrastructure | `docker compose down` |
| Run migrations | `.venv/Scripts/python.exe -m alembic upgrade head` on Windows, `.venv/bin/python -m alembic upgrade head` on Unix |
| Open API docs | `http://localhost:8000/docs` |
| Open MinIO console | `http://localhost:9001` |

## Development Workflow

| Task | Command |
| --- | --- |
| Install base dev dependencies | `pip install -e ".[dev]"` |
| Install dev + ML dependencies | `pip install -e ".[dev,ml]"` |
| Run tests | `pytest` |
| Run codec benchmark | `python scripts/bench_codecs.py` |
| Benchmark real images | `python scripts/bench_codecs.py path/to/*.jpg` |
| Run mocked end-to-end demo | `python scripts/e2e_demo.py` |

### Test Coverage

Current tests cover:

- Health endpoint behavior.
- Compression defaults and codec dispatch.
- Resize behavior.
- AVIF/JPEG XL smoke checks when optional codec support is installed.
- Content-aware compression policy behavior for photos, screenshots, documents, illustrations, icons, and low-confidence vision results.

## Future Guidelines

This section captures the product and engineering direction without treating
planned work as already implemented.

| Area | Guideline |
| --- | --- |
| Consented face identity | Future identity-level face features should be opt-in and consent-first before any per-user face gallery, identity prompt, or "who is this?" workflow. |
| Biometric data handling | Face templates, inferred identities, and face clusters should be treated as highly sensitive data with clear deletion, export, and consent-revocation behavior. |
| Adaptive compression | Compression should evolve from the current deterministic policy toward user-tuned adaptive decisions based on explicit quality ratings and safe implicit feedback. |
| Feedback learning | Feedback collection should be transparent, limited to useful signals, and designed so users are not surprised by what affects model or policy behavior. |
| Search and gallery UI | The UI should become a real lightweight gallery, search, and photo-view experience, with privacy controls close to sensitive actions. |
| Compliance operations | Account deletion, ZIP export, retention cleanup, and derived metadata cleanup should cover originals, served files, embeddings, tags, and future face data. |
| Production hardening | Background workers, GPU process isolation, model runtime reliability, observability, load testing, and operational safety should come after the core workflows are stable. |

## Contributing

Contributions should keep privacy and data ownership central. A good default
workflow is:

1. Run the setup script for your platform.
2. Create a focused branch.
3. Add or update tests for behavior changes.
4. Run `pytest`.
5. Open a small pull request with a clear summary, test notes, and privacy impact notes when user data or derived metadata is involved.

Please avoid broad refactors in the same pull request as feature work. Changes
that touch auth, object storage, retention, embeddings, or future face workflows
should explain how user isolation and deletion/export behavior are preserved.

## Security Disclosure

Do not post real secrets, private images, EXIF data, embeddings, face data, or
other sensitive user information in public issues or pull requests. If a
security policy file is added later, use that process for private reporting.

