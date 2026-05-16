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
#   build-essential, git — needed when pip builds wheels from sdist
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         libpq5 curl \
         libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
         build-essential git \
    && rm -rf /var/lib/apt/lists/*

# Copy only the project metadata first so dependency-install layer is
# cached across source edits.
COPY pyproject.toml README.md /app/

# Install order: base deps first (smaller layer that rarely changes),
# then [ml] on top so a hotfix to base deps doesn't invalidate the
# multi-GB torch download. INSTALL_ML can be flipped to 0 at build time
# for a much smaller image when vision features aren't needed.
ARG INSTALL_ML=1
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir . \
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
