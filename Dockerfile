# syntax=docker/dockerfile:1.7
#
# Two-stage build (CR-7 + F17 from audit_findings/REPORT.md):
#
#   1. `builder` — installs pip dependencies into a venv at /opt/venv.
#      Carries the compilers (build-essential, git) needed to build any
#      sdist-only wheels among [cloud]/[ml] deps.
#
#   2. `runtime` — copies the venv across, installs ONLY the runtime
#      shared libraries (libpq5, OpenCV deps, ffmpeg), creates an
#      unprivileged `neuthek` user, and launches uvicorn as that user.
#
# Why the split:
#   - F17 — before this, build-essential + git stayed in the final
#     image. An attacker who landed an in-container RCE got gcc, make,
#     and git to develop further exploits and exfiltrate.
#   - CR-7 — before this, the runtime had no USER directive and uvicorn
#     ran as root. The compose layer bind-mounts `./backend`,
#     `./migrations`, `./alembic.ini`, `./policies`, AND the host
#     HuggingFace cache RW into the container, so any RCE-shaped bug
#     (one of the pickle-loading model paths, an ffmpeg CVE, a Pillow
#     parser flaw) could rewrite the host's source tree and poison the
#     HF cache for the next boot.
#
# Image-size cost: ~600 MB shaved off (no build tools in runtime).
# Re-uses build cache aggressively: pyproject.toml is COPYed before
# source, and `pip install --no-cache-dir` makes the venv layer
# deterministic per pyproject content.


# ---------- Stage 1: build venv ----------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Compilers + git for any sdist that needs to build C extensions
# (some [ml] deps fall back to source build when no manylinux wheel
# matches the runner platform). libpq-dev is here too — even though
# psycopg2-binary ships a wheel, asyncpg's compile-from-sdist path
# wants headers if a wheel miss ever happens.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         build-essential git libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Metadata first so the (slow, multi-GB) dep-install layer is cached
# across source-only edits.
COPY pyproject.toml README.md /build/

# venv at /opt/venv so we can copy it whole into the runtime stage.
# Activation is via PATH below — no `source` needed at runtime.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip

# Same install order as before this refactor: base + [cloud] always,
# [ml] gated by INSTALL_ML so test runners / CI can opt out of the
# ~3 GB torch download. [cloud] stays unconditional because Sign-in-
# with-Google + Drive sync are core auth surfaces, not optional.
ARG INSTALL_ML=1
RUN /opt/venv/bin/pip install --no-cache-dir ".[cloud]" \
    && if [ "$INSTALL_ML" = "1" ]; then \
         /opt/venv/bin/pip install --no-cache-dir ".[ml]" ; \
       fi

# Pre-download the NLTK corpora the ml pipeline needs so the FIRST
# request doesn't pay the network round-trip (and so a network-isolated
# runtime still works):
#   - punkt_tab                          → sentence/word tokenizer
#   - averaged_perceptron_tagger_eng     → POS tagger, used by
#     `backend/summarize._extract_adjective_tags` to pull the
#     descriptive adjectives that become a file's tags.
#   - wordnet / omw-1.4                  → query-synonym expansion
#     (backend/synonyms.py), previously relied on a lazy runtime
#     download too.
# Installed into the venv's nltk_data dir (/opt/venv/nltk_data is on
# NLTK's default search path) and copied whole into the runtime stage.
RUN if [ "$INSTALL_ML" = "1" ]; then \
      /opt/venv/bin/python -m nltk.downloader -d /opt/venv/nltk_data \
        punkt_tab averaged_perceptron_tagger_eng wordnet omw-1.4 ; \
    fi

# RTL shaping for the translated-document PDF render (Arabic/Hebrew): ReportLab
# does no reshaping or bidi, so backend/api/translate_doc._shape_rtl uses these.
# Separate cheap layer (tiny pure-Python wheels) so the heavy [ml] layer above
# stays cached on a rebuild.
RUN if [ "$INSTALL_ML" = "1" ]; then \
      /opt/venv/bin/pip install --no-cache-dir arabic-reshaper python-bidi ; \
    fi

# LaMa deep-inpainting ("magic eraser") for clean in-image text removal +
# background reconstruction (backend/vision/runtime.get_lama). --no-deps so it
# uses the already-installed cu130 torch/torchvision/opencv rather than pulling
# CPU wheels. The ~200 MB model downloads at first use into $TORCH_HOME.
RUN if [ "$INSTALL_ML" = "1" ]; then \
      /opt/venv/bin/pip install --no-cache-dir --no-deps simple-lama-inpainting ; \
    fi

# HUGGINGFACE MODEL WEIGHTS — NOT baked into the image (by design).
# Every HF model (Florence-2, MADLAD-400, NLLB, Qwen, the summarizer, and now
# TrOCR handwriting `microsoft/trocr-base-handwritten`, ~330 MB) is loaded from
# the cache at ${HF_HOME}=/models, which docker-compose ALWAYS mounts — either
# the host's ~/.cache/huggingface bind mount or the `model_cache` named volume.
# A bind/volume mount SHADOWS anything baked into the image's /models, so baking
# weights would be dead image weight that the mount hides at runtime. Instead
# the weights download lazily into that persistent cache on first use and are
# pre-warmed at startup in the background (see backend/app.py
# `_prewarm_translate_ocr`, which now also warms TrOCR on CPU), so the first
# handwriting OCR / in-image translate doesn't pay the download or load. The
# transformers `TrOCRProcessor` + `VisionEncoderDecoderModel` classes and their
# deps (`sentencepiece` — already required by NLLB/MADLAD) ship via the [ml]
# extras installed above; no new pip dependency is needed for TrOCR.


# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:$PATH"

# Runtime-only shared libraries. NOTHING compiles in this stage.
#   libpq5         — Postgres client used by psycopg2/asyncpg
#   curl           — health probes
#   libgl1 libglib2.0-0 libsm6 libxext6 libxrender1
#                  — required at IMPORT TIME by OpenCV / Pillow image
#                    codecs (insightface, pymupdf, etc.) when [ml] is on
#   ffmpeg         — video/audio transcoding (backend/transcode.py).
#                    Bookworm's ffmpeg ships libx264 + built-in aac;
#                    h264_nvenc is available when the ml-worker has
#                    NVIDIA passthrough enabled per docker-compose.yml.
#   espeak-ng      — TIER-2 universal TTS fallback (backend/api/tts.py).
#                    GPL-3.0 formant synthesizer covering ~100+ languages
#                    (the long tail Piper's ~50 neural voices don't reach:
#                    Welsh, Hindi, Vietnamese, Maori, Yoruba, …). CPU-only
#                    subprocess (`espeak-ng -v <code> -w out.wav <text>`); no
#                    GPU, no Python deps. Bundles its own espeak-ng-data voice
#                    tables. Robotic but intelligible — used only when no Piper
#                    voice exists for the requested language.
#   libreoffice-*  — headless Office→PDF conversion for in-app document
#                    viewing (backend/worker/main.py:_process_convert_office
#                    runs `soffice --headless --convert-to pdf`). The
#                    ml-worker renders docx/xlsx/pptx/odt/ods/odp/rtf/doc to
#                    PDF so they flow through the existing PDF page viewer.
#                    MINIMAL set — only the engine core + the three document
#                    apps, NOT the `libreoffice` metapackage (which drags in
#                    Base, Draw, Math, the whole GNOME/KDE integration stack,
#                    ~1 GB more). `--no-install-recommends` keeps the JRE,
#                    dictionaries, and clipart out:
#                      libreoffice-core    shared headless engine + PDF export
#                      libreoffice-writer  .docx/.doc/.odt/.rtf
#                      libreoffice-calc     .xlsx/.xls/.ods
#                      libreoffice-impress  .pptx/.ppt/.odp
#                    Note: no Java (default-jre-headless) — basic Writer/
#                    Calc/Impress → PDF export does not need it. A handful
#                    of legacy spreadsheet features (some array formulas,
#                    .xls macros) silently degrade without Java; add
#                    default-jre-headless here if that ever matters.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         libpq5 curl ca-certificates unzip \
         libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
         ffmpeg \
         espeak-ng \
         libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress \
         fonts-liberation fonts-dejavu-core \
         fonts-noto-cjk fonts-noto-core \
         fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*

# §C4.6 — Install rclone for Proton Drive + MEGA sync. Both services
# are end-to-end encrypted with fragile Python clients (mega.py
# breaks every few months, proton-python-client lags upstream); the
# Go-implemented rclone is the most stable bridge. Pinned to a known-
# good version so an rclone upgrade doesn't silently break a working
# sync — bump deliberately when validating the new release.
RUN curl -fsSL https://downloads.rclone.org/v1.69.0/rclone-v1.69.0-linux-amd64.zip -o /tmp/rclone.zip \
    && unzip /tmp/rclone.zip -d /tmp/ \
    && mv /tmp/rclone-v1.69.0-linux-amd64/rclone /usr/local/bin/rclone \
    && chmod +x /usr/local/bin/rclone \
    && rm -rf /tmp/rclone.zip /tmp/rclone-v1.69.0-linux-amd64

# Non-root user.
#   - UID 1000 matches the common host-user UID on Linux dev boxes,
#     so bind mounts of ./backend etc. don't fight host filesystem
#     ownership.
#   - `-r` makes it a system account (no password, no aging).
#   - `/usr/sbin/nologin` shell prevents accidental interactive login.
#   - HOME at /home/neuthek so any tool that writes to ~/.cache (HF,
#     pip wheel cache, etc.) lands in a writable place.
RUN useradd -r -u 1000 -m -d /home/neuthek -s /usr/sbin/nologin neuthek

# Bring the venv across. Owned by root because /opt/venv is read-only
# at runtime — neuthek runs the python binary but never modifies the
# venv directory tree. (Pip writes go to /home/neuthek/.local if
# they're ever issued, which they shouldn't be in production.)
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Source + metadata. `--chown` so a future runtime-side write (e.g. an
# operator running `docker exec` to apply a hotfix) doesn't trip on
# root-owned files.
COPY --chown=neuthek:neuthek pyproject.toml README.md /app/
COPY --chown=neuthek:neuthek backend /app/backend
COPY --chown=neuthek:neuthek migrations /app/migrations
COPY --chown=neuthek:neuthek alembic.ini /app/alembic.ini
COPY --chown=neuthek:neuthek policies /app/policies

# Pre-create the HF cache target so HuggingFace can fall back to a
# named volume when the host bind mount isn't present. Without this,
# transformers would try to mkdir /models as `neuthek` and fail if
# /models is root-owned.
RUN mkdir -p /models && chown neuthek:neuthek /models

# Pre-create the cloud-sync state directories with the right
# ownership. Both are mounted as named volumes (pyicloud_cookies +
# rclone_configs) per docker-compose; Docker copies the image's
# directory permissions into the volume on first attach. Without
# this, the volumes start root-owned and the neuthek user (uid 1000)
# can't write to them — connect attempts surface as 500 →
# PermissionError: '/var/neuthek/pyicloud/_tmp'. Mode 0700 because
# both directories hold session-trust / rclone-credential blobs:
# the encryption-at-rest layer is the encrypted DB column, but
# defence-in-depth here keeps another process on the host out.
RUN mkdir -p /var/neuthek/pyicloud /var/neuthek/rclone /var/neuthek/sftp \
    && chown -R neuthek:neuthek /var/neuthek \
    && chmod 700 /var/neuthek/pyicloud /var/neuthek/rclone /var/neuthek/sftp

# Drop privileges. Every command from here runs as neuthek (UID 1000).
USER neuthek

EXPOSE 8000

# `alembic upgrade head` runs on every container start. The CMD does
# not need a shell wrapper for security; using sh -c here is fine
# because no part of the command is user-controlled.
CMD ["sh", "-c", "alembic upgrade head && uvicorn backend.app:app --host 0.0.0.0 --port 8000 --log-level info"]
