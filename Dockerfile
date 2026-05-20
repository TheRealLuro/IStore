FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System deps:
#   libpq5         — Postgres client used by psycopg2/asyncpg
#   curl           — health probes
#   libgl1 libglib2.0-0 libsm6 libxext6 libxrender1
#                  — required by OpenCV / Pillow image codecs (insightface,
#                    pymupdf, etc.) at runtime in [ml] mode
#   ffmpeg         — video / audio transcoding pipeline (backend/transcode.py).
#                    Bookworm's ffmpeg ships with libx264, libfdk-aac
#                    alternative (libfdk not by default — we use built-in
#                    aac), AND h264_nvenc when run inside the ml-worker
#                    container with NVIDIA passthrough enabled (see
#                    docker-compose.yml ml-worker `deploy.resources`).
#                    The backend container also gets ffmpeg so it can
#                    inspect uploads / generate poster frames inline if
#                    we ever need a synchronous path.
#   build-essential, git — needed when pip builds wheels from sdist
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         libpq5 curl \
         libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
         ffmpeg \
         build-essential git \
    && rm -rf /var/lib/apt/lists/*

# Copy only the project metadata first so dependency-install layer is
# cached across source edits.
COPY pyproject.toml README.md /app/

# Install order: base deps first (smaller layer that rarely changes),
# then [cloud] (Google OAuth + Drive client — small, ~5 MB) on top
# of the base, then [ml] last so a hotfix to base deps doesn't
# invalidate the multi-GB torch download. INSTALL_ML can be flipped
# to 0 at build time for a much smaller image when vision features
# aren't needed; [cloud] stays in unconditionally because Sign-in-
# with-Google + Drive sync are core auth surfaces, not optional.
#
# Before this, `[cloud]` was never installed in the container — so
# `from google_auth_oauthlib.flow import Flow` raised ImportError at
# request time, and BOTH "Sign in with Google" and "Connect Drive"
# returned 503 "google-auth-oauthlib is not installed" after every
# container rebuild. Adding it to the image makes the install
# survive `docker compose down && up`.
ARG INSTALL_ML=1
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[cloud]" \
    && if [ "$INSTALL_ML" = "1" ]; then \
         pip install --no-cache-dir ".[ml]" ; \
       fi

# Source last so editing app code only rebuilds the small final layer.
COPY backend /app/backend
COPY migrations /app/migrations
COPY alembic.ini /app/alembic.ini
# Policy texts hashed at consent grant time (face-recognition v1, etc).
# Without this, /consent/*/grant 500s with FileNotFoundError on the
# /app/policies path. The dev compose layer also bind-mounts this dir
# so edits to the policy take effect without a rebuild.
COPY policies /app/policies

EXPOSE 8000

# `alembic upgrade head` runs on every container start so a fresh
# environment ends up at the latest schema. `--reload` is intentionally
# omitted here; the dev compose layer adds it back via command override.
CMD ["sh", "-c", "alembic upgrade head && uvicorn backend.app:app --host 0.0.0.0 --port 8000 --log-level info"]
