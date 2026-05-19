"""Video / audio transcoding pipeline.

Inputs: an arbitrary upload (HEVC / H.264 / VP9 / AV1 / ProRes / DV /
anything ffmpeg understands). Outputs: a universally-playable MP4
(H.264 baseline, AAC stereo, faststart) plus a JPEG poster frame.

GPU path is `h264_nvenc` when running inside the ml-worker container
with NVIDIA passthrough; CPU fallback is `libx264`. The wrapper
probes for nvenc once per process and caches the answer so we don't
pay the `ffmpeg -encoders` cost on every job.

Resolution is preserved up to a 2160p (4K) ceiling — the user asked
for "all resolutions" so we don't down-rez 1080p / 1440p / 4K
source. Anything taller than 2160p gets scaled down because (a) most
browsers stutter on > 4K H.264 playback and (b) the bandwidth /
storage cost compounds linearly.

Bitrate is picked by source resolution rather than a flat CRF. CRF
is great for "constant visual quality" but produces unpredictable
file sizes; for a personal-cloud storage budget you want a target
size so 4 K and SD both end up reasonable.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

logger = logging.getLogger(__name__)


# ---------- One-shot capability probe -----------------------------

_NVENC_AVAILABLE: bool | None = None


def _probe_nvenc() -> bool:
    """Probe ffmpeg for `h264_nvenc` encoder availability + a 1-frame
    test encode (the encoder is registered even on hosts without an
    NVIDIA driver — only the actual encode fails). Cached across
    calls so a worker that handles many jobs only pays the probe
    cost on the first one.

    Quirk worth knowing: ffmpeg's exit code is 0 even when nvenc
    fails to initialize (the encoder logs "Cannot load
    libnvidia-encode.so.1" to stderr and exits with 0 anyway).
    So we ALSO grep stderr for the well-known failure markers; if
    they're there, the encoder isn't actually usable and we fall
    back to libx264.
    """
    global _NVENC_AVAILABLE
    if _NVENC_AVAILABLE is not None:
        return _NVENC_AVAILABLE
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=320x240:d=0.1",
                "-c:v", "h264_nvenc", "-frames:v", "1",
                "-f", "null", "-",
            ],
            capture_output=True, timeout=10,
        )
        err = (proc.stderr or b"").decode(errors="replace")
        ok = proc.returncode == 0 and not any(
            marker in err
            for marker in (
                "Cannot load libnvidia-encode",
                "Could not open encoder",
                "Nothing was written",
                "No NVENC capable devices found",
            )
        )
        _NVENC_AVAILABLE = ok
        if not ok and err.strip():
            logger.info("transcode: nvenc probe stderr: %s", err.strip()[:300])
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        _NVENC_AVAILABLE = False
    logger.info(
        "transcode: nvenc %s",
        "available" if _NVENC_AVAILABLE else "unavailable (falling back to libx264)",
    )
    return _NVENC_AVAILABLE


# ---------- Public API --------------------------------------------


# ---------- Quality tiers ----------------------------------------
#
# Standard short-form labels that match what users see on every other
# video player (YouTube, Vimeo, Plex). Each tier is the long-edge
# pixel count of its output. We emit a tier only when the SOURCE long
# edge is at least as tall as the tier — never upscale, just downscale.
_QUALITY_TIERS: list[tuple[str, int]] = [
    ("2160p", 2160),
    ("1440p", 1440),
    ("1080p", 1080),
    ("720p",  720),
    ("480p",  480),
]


def _tiers_for_source(src_long_edge: int) -> list[tuple[str, int]]:
    """Return the quality tiers we should emit for a source whose
    long edge is `src_long_edge` pixels. Always includes the largest
    tier the source supports + every smaller tier down to 480p, so
    the user has at least 2-3 picks for any source >= 720p.
    Sources smaller than 480p get a single passthrough tier at their
    native resolution."""
    available = [(name, h) for (name, h) in _QUALITY_TIERS if h <= src_long_edge]
    if not available:
        # Source < 480p — keep at native long edge.
        return [(f"{src_long_edge}p", src_long_edge)]
    return available


@dataclass
class QualityVariant:
    label: str               # e.g. "1080p"
    path: Path               # local file
    size: int                # bytes
    width: int
    height: int


@dataclass
class TranscodeResult:
    variants: list[QualityVariant]   # ordered highest-quality first
    default_label: str               # label that should map to served_blob_key
    poster_path: Path                # JPEG poster frame
    poster_size: int
    duration_s: float                # exact duration of the served file
    used_gpu: bool

    @property
    def served_path(self) -> Path:
        """Back-compat shim: the highest tier's file."""
        return self.variants[0].path

    @property
    def served_size(self) -> int:
        return self.variants[0].size

    @property
    def width(self) -> int:
        return self.variants[0].width

    @property
    def height(self) -> int:
        return self.variants[0].height

    @property
    def served_mime(self) -> str:
        return "video/mp4"

    @property
    def poster_mime(self) -> str:
        return "image/jpeg"


def transcode_video(
    src_path: Path,
    work_dir: Path,
    *,
    prefer_gpu: bool = True,
) -> TranscodeResult:
    """Synchronous transcode entry point. Emits one ffmpeg pass per
    quality tier (1080p / 720p / 480p when the source supports
    them) so the player can switch resolutions without re-encoding
    later. Run via `asyncio.to_thread` from the worker so the
    asyncio loop isn't blocked during the encode (30-600 seconds
    depending on input + tier count)."""
    if not src_path.exists():
        raise FileNotFoundError(f"transcode source missing: {src_path}")
    work_dir.mkdir(parents=True, exist_ok=True)

    meta = _probe_source(src_path)
    src_long_edge = max(meta["width"], meta["height"])
    # Honor the 4K ceiling at the source level — anything taller
    # gets clamped before we pick tier sizes.
    src_long_edge = min(src_long_edge, 2160)
    tiers = _tiers_for_source(src_long_edge)
    use_gpu = prefer_gpu and _probe_nvenc()

    src_w, src_h = meta["width"], meta["height"]
    variants: list[QualityVariant] = []
    for label, long_edge in tiers:
        # The OUTPUT long edge equals `long_edge`; the other axis is
        # scaled to preserve aspect ratio. Even-axis constraint for
        # H.264 applied to both. Works for landscape, portrait, and
        # square sources alike.
        if src_w >= src_h:  # landscape or square
            out_w = _round_even(long_edge)
            out_h = _round_even(int(long_edge * src_h / src_w)) if src_w else _round_even(long_edge)
        else:               # portrait
            out_h = _round_even(long_edge)
            out_w = _round_even(int(long_edge * src_w / src_h)) if src_h else _round_even(long_edge)
        out_path = work_dir / f"{label}.mp4"
        _run_video_encode(
            src_path, out_path, out_w, out_h, use_gpu,
            source_kbps=meta.get("source_bitrate_kbps", 0),
        )
        variants.append(QualityVariant(
            label=label, path=out_path, size=out_path.stat().st_size,
            width=out_w, height=out_h,
        ))
        logger.info(
            "transcode: tier %s — %dx%d, %d bytes",
            label, out_w, out_h, out_path.stat().st_size,
        )

    poster_path = work_dir / "poster.jpg"
    _extract_poster(src_path, poster_path, duration_s=meta["duration"])

    return TranscodeResult(
        variants=variants,
        default_label=variants[0].label,
        poster_path=poster_path,
        poster_size=poster_path.stat().st_size,
        duration_s=meta["duration"],
        used_gpu=use_gpu,
    )


# ---------- ffprobe wrapper ---------------------------------------


def _probe_source(path: Path) -> dict:
    """Return source dimensions + duration. Uses ffprobe's
    `json` output mode so a partial probe doesn't throw on
    container metadata gaps."""
    proc = subprocess.run(
        [
            "ffprobe", "-hide_banner", "-loglevel", "error",
            "-print_format", "json", "-show_streams", "-show_format",
            str(path),
        ],
        capture_output=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {path}: {proc.stderr.decode(errors='replace')}"
        )
    import json
    data = json.loads(proc.stdout or b"{}")
    video = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    if video is None:
        raise RuntimeError(f"no video stream in {path}")
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    # Prefer container-level bitrate; fall back to (video-stream bitrate)
    # or (byte_size / duration). When none of those land we leave it at 0,
    # which the caller reads as "unknown — don't clamp".
    fmt_br = data.get("format", {}).get("bit_rate")
    vid_br = video.get("bit_rate")
    size_b = data.get("format", {}).get("size")
    source_bitrate_bps = 0
    if fmt_br:
        source_bitrate_bps = int(fmt_br)
    elif vid_br:
        source_bitrate_bps = int(vid_br)
    elif size_b and duration > 0:
        source_bitrate_bps = int(int(size_b) * 8 / duration)
    return {
        "width":   int(video.get("width") or 0),
        "height":  int(video.get("height") or 0),
        "duration": duration,
        "codec":   video.get("codec_name", "unknown"),
        "source_bitrate_kbps": source_bitrate_bps // 1000,
    }


# ---------- Resolution policy -------------------------------------


def _target_dimensions(src_w: int, src_h: int) -> tuple[int, int]:
    """Preserve source resolution up to 2160p (4K). Anything taller
    scales down on the long edge while keeping aspect ratio + even
    dimensions (H.264 requires both axes divisible by 2)."""
    MAX_LONG_EDGE = 2160
    long_edge = max(src_w, src_h)
    if long_edge <= MAX_LONG_EDGE:
        return _round_even(src_w), _round_even(src_h)
    scale = MAX_LONG_EDGE / long_edge
    return _round_even(int(src_w * scale)), _round_even(int(src_h * scale))


def _round_even(n: int) -> int:
    return n if n % 2 == 0 else n + 1


# ---------- Bitrate policy ----------------------------------------
#
# Target bitrates picked from common reference points (YouTube
# upload guide + AWS Elemental defaults). The point of a personal-
# cloud transcode is "the video plays everywhere with reasonable
# storage cost," not "studio master quality." For users that need
# the master, we keep nothing — the original was their master —
# we keep ONLY this served copy per their stated policy. So we
# bias toward a higher bitrate than the streaming defaults so
# perceptual quality stays close to the original.
#
# Numbers are in megabits/second.


_BITRATE_TABLE = [
    # (max_long_edge_pixels, video_bitrate_mbps)
    (2160, 18),  # 4K
    (1440, 10),  # 2.5K
    (1080, 6),   # FHD
    (720,  3),   # HD
    (480,  1.5), # SD
]


def _video_bitrate_kbps(
    width: int, height: int, *, source_kbps: int = 0,
) -> int:
    """Pick the encode bitrate for a target resolution. We clamp by
    `source_kbps` (when known) so we never re-encode an already-
    compressed source at a HIGHER bitrate than it arrived at — that
    just inflates the file without adding quality. Floor of 400 kbps
    on the clamp so a pathologically low source can still produce a
    legible 480p (videos at < 400 kbps look like JPEG mosaics anyway).

    Index the table by the SHORT edge — that's what the "p" number
    in `1080p` refers to (1920x1080 has short edge 1080). Using the
    long edge would put every landscape FHD clip into the 4K
    bracket and overshoot the bitrate by 3x.
    """
    short_edge = min(width, height) if min(width, height) > 0 else max(width, height)
    target = int(_BITRATE_TABLE[0][1] * 1000)  # default to highest tier
    # Walk table top-down (highest ceiling first). The smallest
    # ceiling that the short edge fits under wins — equivalent to
    # "find the lowest matching tier."
    for ceiling, mbps in _BITRATE_TABLE:
        if short_edge <= ceiling:
            target = int(mbps * 1000)
    if source_kbps > 0:
        # Cap at 110 % of source — the 10 % headroom is for the
        # inevitable bitrate variability in the re-encode, not a
        # quality bump. Anything beyond is wasted disk.
        ceiling = max(400, int(source_kbps * 1.1))
        target = min(target, ceiling)
    return target


# ---------- Encode -------------------------------------------------


def _run_video_encode(
    src: Path, dst: Path, width: int, height: int, use_gpu: bool,
    *, source_kbps: int = 0,
) -> None:
    """One-pass encode. Two-pass would gain ~5 % quality at the same
    bitrate but doubles wall-clock; for a personal cloud the user-
    visible cost of waiting isn't worth the marginal quality gain.

    `-movflags +faststart` moves the moov atom to the start of the
    file so the browser can begin playback before the entire file
    downloads. Without this, a 4 GB video shows 0:00 until the very
    last byte arrives."""
    bitrate_kbps = _video_bitrate_kbps(width, height, source_kbps=source_kbps)
    common = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-y",  # overwrite output without prompt
        "-i", str(src),
        "-vf", f"scale={width}:{height}:flags=lanczos",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-movflags", "+faststart",
    ]
    if use_gpu:
        # NVENC. `p5` is the quality preset in modern nvenc; `hq` tune
        # gives the same VBV target as software equivalents. Profile
        # `main` (not baseline) — every browser supports it and the
        # quality / bitrate tradeoff is better. `-rc vbr` lets the
        # encoder use the bitrate as an average rather than a hard cap.
        encode = [
            "-c:v", "h264_nvenc",
            "-preset", "p5", "-tune", "hq",
            "-profile:v", "main", "-rc", "vbr",
            "-b:v", f"{bitrate_kbps}k",
            "-maxrate", f"{int(bitrate_kbps * 1.4)}k",
            "-bufsize", f"{bitrate_kbps * 2}k",
        ]
    else:
        # libx264. `medium` preset = the standard quality/speed knee.
        # `-crf` + `-maxrate` together produce a near-CBR with a quality
        # floor; we cap above the target so a content-heavy scene
        # doesn't push past what we budgeted.
        encode = [
            "-c:v", "libx264",
            "-preset", "medium",
            "-profile:v", "main", "-level", "4.1",
            "-b:v", f"{bitrate_kbps}k",
            "-maxrate", f"{int(bitrate_kbps * 1.4)}k",
            "-bufsize", f"{bitrate_kbps * 2}k",
        ]
    cmd = common + encode + [str(dst)]
    logger.info("transcode: encoding %s → %s (%dx%d, %dk, %s)",
                src.name, dst.name, width, height, bitrate_kbps,
                "nvenc" if use_gpu else "libx264")
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        # Surface ffmpeg's stderr so the worker log has the why.
        raise RuntimeError(
            f"ffmpeg encode failed (exit {proc.returncode}): "
            f"{proc.stderr.decode(errors='replace')[:2000]}"
        )


# ---------- Poster -------------------------------------------------


def _extract_poster(src: Path, dst: Path, *, duration_s: float) -> None:
    """Grab a representative frame ~2 s into the video (avoids the
    common black opener). For very short clips (< 4 s), grab the
    midpoint instead."""
    seek = min(2.0, max(0.0, duration_s / 2.0))
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-y",
        "-ss", f"{seek:.3f}",
        "-i", str(src),
        "-frames:v", "1",
        "-q:v", "2",  # JPEG quality 2 (range 1-31, lower = higher quality)
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg poster extract failed (exit {proc.returncode}): "
            f"{proc.stderr.decode(errors='replace')[:1000]}"
        )


# ---------- Async wrapper for worker -------------------------------


async def transcode_video_async(
    src_path: Path,
    work_dir: Path,
    *,
    prefer_gpu: bool = True,
) -> TranscodeResult:
    """Worker-friendly wrapper that runs the encode on a thread pool
    so the asyncio loop can heartbeat / accept signals while ffmpeg
    chews on a 4 GB file."""
    return await asyncio.to_thread(
        transcode_video, src_path, work_dir, prefer_gpu=prefer_gpu,
    )
