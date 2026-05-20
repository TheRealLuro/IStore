"""Audio transcription helper — speech-to-text for video summaries.

The video summarizer used to capture only what each keyframe LOOKED
like (Florence-2 caption + on-screen text via OCR). For talking-head
videos / interviews / lectures, the most relevant content is what
was SAID, not what was seen. This module wraps faster-whisper to
extract the spoken transcript and surface it as another context
signal Qwen aggregates into the final summary.

Failure modes degrade gracefully:
  - faster-whisper not installed → returns None
  - ffmpeg fails to extract audio → returns None
  - video has no audio track → returns "" (still success, just empty)
  - transcription crashes → returns None, logs the exception

Caller treats None as "no transcript signal, skip the relevant
Qwen context line." Empty string means "audio existed but was
silent" — also skip but for a different reason.
"""
from __future__ import annotations

import logging
import subprocess
from functools import lru_cache
from typing import Optional

from backend.config import settings

logger = logging.getLogger(__name__)


# Cap the transcription input. A 20-minute video at the default model
# size still runs in ~30 s on GPU; anything longer is rare in the
# personal-cloud workflow and the marginal accuracy gain on a
# 2-hour movie doesn't justify the GPU time + memory hit. We
# truncate the EXTRACTED AUDIO duration, not the file itself, so a
# user uploading a long clip still gets a summary of the first N
# minutes (typically the most informative chunk).
_MAX_AUDIO_SECONDS = 20 * 60


def _can_load_cublas12() -> bool:
    """faster-whisper's CTranslate2 backend is linked against CUDA 12
    and looks for `libcublas.so.12` + `libcudnn_ops_infer.so.8` at
    inference time. PyTorch 2.6+ in this image bundles CUDA 13 libs
    only (`libcublas.so.13`), so even though `ctranslate2.get_cuda_device_count()`
    reports >0, the encode call crashes with
    `Library libcublas.so.12 is not found or cannot be loaded`.
    Probe for the actual .so.12 file before opting in to GPU; if it's
    missing we silently use CPU instead of inheriting a broken model."""
    import ctypes
    for name in ("libcublas.so.12", "libcudnn_ops_infer.so.8"):
        try:
            ctypes.CDLL(name)
        except OSError:
            return False
    return True


# Module-level singletons. We used to use `functools.lru_cache` here but
# the runtime-fallback path needed to REPLACE the cached value (when a
# GPU encode crashed at inference time we wanted future calls to hit a
# CPU model directly). Reassigning `_get_whisper_model.__wrapped__` does
# NOT change what `lru_cache` actually calls — the wrapper holds the
# original function via closure, so the cache-clear path was a no-op
# and every transcribe attempt kept re-probing the GPU. Using a plain
# module global with explicit assignment makes the override trivial
# and reliable.
_WHISPER_MODEL = None
_WHISPER_FORCE_CPU = False


def _get_whisper_model():
    """Load faster-whisper once per process. Returns None when the
    package isn't installed (e.g. the [ml] extras aren't on the
    image yet, or the user opted out).

    When the runtime-fallback path sets `_WHISPER_FORCE_CPU=True`
    (after a GPU encode crashed), the next call rebuilds the model
    on CPU and caches that. The override sticks for the lifetime of
    the process.
    """
    global _WHISPER_MODEL
    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        logger.info("transcribe: faster-whisper not installed; skipping")
        return None
    # `base` is the smallest model that produces usable English
    # transcripts (74 MB int8 / 150 MB fp16). Pick `int8_float16` on
    # GPU so we keep VRAM minimal while running alongside Florence /
    # Qwen / OpenCLIP. CPU path uses `int8` (no float16 support).
    model_size = getattr(settings, "whisper_model_size", "base")
    # Only try CUDA when BOTH a device is present AND the .so.12 libs
    # CTranslate2 needs are loadable AND we haven't already failed once
    # at runtime. Otherwise the GPU model loads fine but crashes inside
    # encode() with a libcublas error.
    if not _WHISPER_FORCE_CPU:
        try:
            import ctranslate2  # type: ignore
            if ctranslate2.get_cuda_device_count() > 0 and _can_load_cublas12():
                logger.info("transcribe: loading whisper on GPU (cuda + cublas12 OK)")
                _WHISPER_MODEL = WhisperModel(
                    model_size, device="cuda", compute_type="int8_float16",
                )
                return _WHISPER_MODEL
        except Exception:
            logger.exception("transcribe: GPU model load failed; falling back to CPU")
    logger.info("transcribe: loading whisper on CPU (int8)")
    _WHISPER_MODEL = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _WHISPER_MODEL


def _force_cpu_and_rebuild():
    """Called by the runtime-fallback path after a GPU encode crashes
    with libcublas/libcudnn errors. Flips the module sentinel so the
    next `_get_whisper_model()` skips the CUDA probe entirely, then
    drops the cached model so the next call rebuilds it."""
    global _WHISPER_MODEL, _WHISPER_FORCE_CPU
    _WHISPER_FORCE_CPU = True
    _WHISPER_MODEL = None
    return _get_whisper_model()


def _extract_audio_wav(raw_bytes: bytes) -> Optional[bytes]:
    """Write video bytes to a temp file, run ffmpeg against it, return a 16 kHz mono WAV blob.

    Whisper natively expects 16 kHz mono. Doing the resample in ffmpeg
    (which is already on PATH) is cheaper than letting faster-whisper
    do it via librosa, and it lets us cap the duration in the same
    pass.

    Uses a temp file (not stdin pipe) for the same reason
    `_extract_keyframe` does: most mp4s have the moov atom at the end
    of the file, and ffmpeg can't seek backwards in a pipe to find
    it. With stdin, ffmpeg fails with `partial file` at offset 0x30 on
    every phone/screen-capture mp4 — the audio never extracts,
    Whisper never runs, and the summary has no transcript signal.
    """
    import tempfile
    import os
    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="audio-", suffix=".bin", delete=False,
        ) as tmp:
            tmp.write(raw_bytes)
            tmp_path = tmp.name
        proc = subprocess.run(
            [
                "ffmpeg",
                "-loglevel", "error",
                "-i", tmp_path,
                "-vn",                          # drop video stream
                "-ac", "1",                     # mono
                "-ar", "16000",                 # 16 kHz
                "-t", str(_MAX_AUDIO_SECONDS),  # truncate
                "-f", "wav",
                "pipe:1",
            ],
            capture_output=True,
            timeout=120,
            check=True,
        )
        return proc.stdout
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # No ffmpeg, container missing audio track, or extraction
        # crashed — let the caller treat absence of transcript as
        # "no signal" rather than an error.
        return None
    finally:
        if tmp_path:
            try: os.unlink(tmp_path)
            except OSError: pass


def transcribe_video_audio(raw_bytes: bytes) -> Optional[str]:
    """Return the transcript of the video's audio track, or None on
    any failure. Empty string means audio existed but was silent.

    Synchronous — call via `asyncio.to_thread` from async callers so
    the asyncio event loop stays responsive during the ~realtime
    transcribe pass.
    """
    model = _get_whisper_model()
    if model is None:
        return None
    wav = _extract_audio_wav(raw_bytes)
    if not wav:
        return None
    # faster-whisper accepts a path, file-like, OR raw numpy float32
    # array. We've got bytes — wrap in BytesIO and let the underlying
    # audio loader (av / soundfile) handle the WAV header.
    from io import BytesIO

    def _run(m):
        return m.transcribe(
            BytesIO(wav),
            # `beam_size=1` is the default for faster-whisper; bumping
            # to 5 improves accuracy slightly at ~2× cost. Stick with
            # 1 because the transcript is one input among many for
            # Qwen — perfect accuracy isn't the bar.
            beam_size=1,
            # `vad_filter=False` — when the audio is short (5-10 s)
            # the VAD heuristic sometimes drops EVERYTHING, leaving
            # an empty mel that crashes inside encode(). Disable it.
            # For long clips we accept the small speed hit; accuracy
            # matters more than transcribe time here.
            vad_filter=False,
            # Auto language detection. faster-whisper writes the
            # detected language to `info.language` — we log it for
            # the operator but don't return it.
            language=None,
        )

    try:
        segments, info = _run(model)
    except RuntimeError as exc:
        # Most common failure: libcublas.so.12 missing at encode time
        # even though the model loaded. Wipe the cached GPU model,
        # build a fresh CPU one, and retry once.
        msg = str(exc)
        if "libcublas" in msg or "libcudnn" in msg or "CUDA" in msg:
            logger.warning("transcribe: GPU encode failed (%s); retrying on CPU", msg.split(chr(10))[0])
            try:
                from faster_whisper import WhisperModel  # type: ignore
                model_size = getattr(settings, "whisper_model_size", "base")
                cpu_model = WhisperModel(model_size, device="cpu", compute_type="int8")
                _get_whisper_model.cache_clear()
                # Re-seed the cache so future calls hit CPU directly.
                _get_whisper_model.__wrapped__ = lambda: cpu_model  # type: ignore[attr-defined]
                segments, info = _run(cpu_model)
            except Exception:
                logger.exception("transcribe: CPU fallback also failed")
                return None
        else:
            logger.exception("transcribe: whisper inference failed")
            return None
    except Exception:
        logger.exception("transcribe: whisper inference failed")
        return None
    parts: list[str] = []
    try:
        for seg in segments:
            if seg.text:
                parts.append(seg.text.strip())
    except Exception:
        # Streaming-segments crashes are rare but can happen on
        # malformed audio mid-stream. Return whatever we got.
        logger.exception("transcribe: segment stream crashed mid-way")
    transcript = " ".join(parts).strip()
    if transcript:
        logger.info(
            "transcribe: %d chars (%.1f s audio, lang=%s)",
            len(transcript),
            info.duration if hasattr(info, "duration") else -1,
            getattr(info, "language", "?"),
        )
    return transcript
