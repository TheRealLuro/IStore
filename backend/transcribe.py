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


@lru_cache(maxsize=1)
def _get_whisper_model():
    """Load faster-whisper once per process. Returns None when the
    package isn't installed (e.g. the [ml] extras aren't on the
    image yet, or the user opted out)."""
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
    try:
        # GPU-preferred. faster-whisper auto-detects CUDA via
        # CTranslate2; falls back to CPU when CUDA isn't usable.
        import ctranslate2  # type: ignore
        if ctranslate2.get_cuda_device_count() > 0:
            return WhisperModel(
                model_size, device="cuda", compute_type="int8_float16",
            )
    except Exception:
        pass
    return WhisperModel(model_size, device="cpu", compute_type="int8")


def _extract_audio_wav(raw_bytes: bytes) -> Optional[bytes]:
    """Pipe video bytes through ffmpeg, return a 16 kHz mono WAV blob.

    Whisper natively expects 16 kHz mono. Doing the resample in ffmpeg
    (which is already on PATH) is cheaper than letting faster-whisper
    do it via librosa, and it lets us cap the duration in the same
    pass."""
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-loglevel", "error",
                "-i", "pipe:0",
                "-vn",                          # drop video stream
                "-ac", "1",                     # mono
                "-ar", "16000",                 # 16 kHz
                "-t", str(_MAX_AUDIO_SECONDS),  # truncate
                "-f", "wav",
                "pipe:1",
            ],
            input=raw_bytes,
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
    try:
        segments, info = model.transcribe(
            BytesIO(wav),
            # `beam_size=1` is the default for faster-whisper; bumping
            # to 5 improves accuracy slightly at ~2× cost. Stick with
            # 1 because the transcript is one input among many for
            # Qwen — perfect accuracy isn't the bar.
            beam_size=1,
            # `vad_filter=True` strips silence chunks before
            # transcription. Big win on screen-recordings with long
            # quiet stretches (cuts ~30-50% of inference time on
            # those clips).
            vad_filter=True,
            # Auto language detection. faster-whisper writes the
            # detected language to `info.language` — we log it for
            # the operator but don't return it.
            language=None,
        )
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
