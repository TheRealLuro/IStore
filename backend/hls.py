"""HLS adaptive-bitrate transcoding pipeline.

Produces a YouTube-style multi-bitrate streaming layout:

    master.m3u8                ← variant playlist referencing all renditions
    master/playlist.m3u8       ← rendition playlist for the master tier
    master/segment_001.ts      ← MPEG-TS segments (~6 s each)
    master/segment_002.ts
    ...
    720p/playlist.m3u8
    720p/segment_NNN.ts
    480p/playlist.m3u8
    480p/segment_NNN.ts
    poster.jpg                 ← still poster frame

The player (`hls.js` on Chrome/Firefox/Edge, native `<video>` on
Safari/iOS) downloads segments one quality at a time, measures the
throughput, and adapts up or down at each 6 s segment boundary. The
master.m3u8 is small (~1 KB) — it's a text manifest listing the
per-rendition playlists with their advertised bandwidths.

We emit ONE rendition per ffmpeg invocation instead of one combined
filter graph because (a) it's vastly easier to debug encode failures
on a per-tier basis, and (b) consumer NVIDIA cards limit concurrent
NVENC sessions to 3 — running them serially is well under that
budget. Total wall time is still close to realtime since GPU encode
runs at >1× speed on every modern card.

The MASTER rendition tracks the source resolution (capped at 2160p
on the long edge). Lower renditions are 720p and 480p, emitted only
when the source has the headroom for them — a 540p source gets just
a master at 540p, since downscaling to 480p saves ~10 % and adding
a redundant lower tier isn't worth the segments.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from backend.transcode import (
    _extract_poster, _probe_nvenc, _probe_source, _round_even,
)

logger = logging.getLogger(__name__)

# Per-rendition target bitrates. Same ladder YouTube publishes in
# their upload guide for "good" quality at each resolution. Audio is
# the same across renditions on phones / talking-head clips — for
# higher-fidelity music we could bump 128 kbps higher but the
# perceptual gain in the streaming context is negligible.
_LADDERS_BY_LONG_EDGE = [
    # (min_source_long_edge, label, target_long_edge, video_kbps, audio_kbps)
    # The MASTER tier is always emitted — its target equals
    # min(source_long_edge, 2160). We compute its bitrate at
    # encode time based on resolution.
    (1080, "720p", 720,  2500, 96),
    (720,  "480p", 480,  1000, 64),
]
_MASTER_LONG_EDGE_CAP = 2160  # absolute hard ceiling (4K)
_MASTER_BITRATE_TABLE = [
    # (max_long_edge, video_kbps)
    (2160, 12000),
    (1440, 8000),
    (1080, 5000),
    (720,  3000),
    (480,  1500),
]


@dataclass
class HlsRendition:
    """One quality tier in the HLS output."""

    label: str           # e.g. "master", "720p", "480p"
    width: int
    height: int
    video_kbps: int
    audio_kbps: int
    playlist_path: Path                       # local <label>/playlist.m3u8
    segment_paths: list[Path] = field(default_factory=list)
    segment_bytes: int = 0                    # sum of all .ts file sizes

    @property
    def bandwidth_bps(self) -> int:
        """Advertised bandwidth — sum of video + audio bitrates in bits/s.
        Used in the EXT-X-STREAM-INF line of the master manifest."""
        return (self.video_kbps + self.audio_kbps) * 1000


@dataclass
class HlsResult:
    """Full output of `transcode_to_hls`. Caller is responsible for
    uploading these to MinIO + recording the master playlist path in
    `images.served_variants`."""

    master_playlist_path: Path     # local master.m3u8
    renditions: list[HlsRendition]
    poster_path: Path
    poster_size: int
    duration_s: float
    used_gpu: bool

    @property
    def total_bytes(self) -> int:
        """Wall-cost: every file we'll upload to MinIO."""
        playlists = self.master_playlist_path.stat().st_size + sum(
            r.playlist_path.stat().st_size for r in self.renditions
        )
        segments = sum(r.segment_bytes for r in self.renditions)
        return playlists + segments + self.poster_size


def _master_bitrate_for(long_edge: int) -> int:
    """Pick the master-rendition target bitrate based on its
    long edge. We never re-encode above 4K so the table covers
    everything up to 2160. Falls through to 1500 kbps for tiny
    sources (rare — phone recordings are normally 720p+)."""
    for ceiling, kbps in _MASTER_BITRATE_TABLE:
        if long_edge <= ceiling:
            return kbps
    return 1500


def _scaled_dims(
    src_w: int, src_h: int, target_long_edge: int,
) -> tuple[int, int]:
    """Compute the output dimensions for a given target long-edge,
    preserving aspect ratio. Always rounds to even values because
    H.264 / NVENC require both axes divisible by 2."""
    if src_w >= src_h:  # landscape or square
        out_w = _round_even(target_long_edge)
        out_h = _round_even(int(target_long_edge * src_h / src_w)) if src_w else _round_even(target_long_edge)
    else:               # portrait
        out_h = _round_even(target_long_edge)
        out_w = _round_even(int(target_long_edge * src_w / src_h)) if src_h else _round_even(target_long_edge)
    return out_w, out_h


def _encode_one_rendition(
    src: Path, out_dir: Path, *, label: str, width: int, height: int,
    video_kbps: int, audio_kbps: int, segment_seconds: int,
    use_gpu: bool,
) -> Path:
    """Run one ffmpeg invocation that emits the playlist + segments
    for a single quality tier. Returns the playlist path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    playlist = out_dir / "playlist.m3u8"
    segment_pattern = str(out_dir / "segment_%03d.ts")

    cmd: list[str] = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        # Lanczos scaler — same quality knob the legacy mp4 path uses.
        "-vf", f"scale={width}:{height}:flags=lanczos",
        "-pix_fmt", "yuv420p",
        # Keyframe alignment — every ladder gets a keyframe at the
        # same time positions so the segment boundaries align across
        # renditions. Without this, `hls.js` can't switch quality
        # mid-stream cleanly; it has to wait for the next natural
        # keyframe which may not exist.
        "-force_key_frames", f"expr:gte(t,n_forced*{segment_seconds})",
        # Audio config — `0:a:0?` keeps it optional so videos without
        # audio (screen recordings, GIF-style clips) still encode.
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k", "-ac", "2",
    ]
    if use_gpu:
        cmd.extend([
            "-c:v", "h264_nvenc",
            "-preset", "p5", "-tune", "hq",
            "-profile:v", "main", "-rc", "vbr",
            "-b:v", f"{video_kbps}k",
            "-maxrate", f"{int(video_kbps * 1.4)}k",
            "-bufsize", f"{video_kbps * 2}k",
        ])
    else:
        cmd.extend([
            "-c:v", "libx264", "-preset", "medium",
            "-profile:v", "main", "-level", "4.1",
            "-b:v", f"{video_kbps}k",
            "-maxrate", f"{int(video_kbps * 1.4)}k",
            "-bufsize", f"{video_kbps * 2}k",
        ])
    cmd.extend([
        "-f", "hls",
        "-hls_time", str(segment_seconds),
        "-hls_playlist_type", "vod",
        # `independent_segments` declares that each segment can be
        # decoded without depending on earlier segments — required
        # for clean quality switching at segment boundaries.
        "-hls_flags", "independent_segments",
        "-hls_segment_type", "mpegts",
        "-hls_segment_filename", segment_pattern,
        str(playlist),
    ])
    logger.info(
        "hls: encoding %s — %dx%d, %dk video, %dk audio, %s",
        label, width, height, video_kbps, audio_kbps,
        "nvenc" if use_gpu else "libx264",
    )
    proc = subprocess.run(cmd, capture_output=True, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(
            f"hls encode {label} failed (exit {proc.returncode}): "
            f"{proc.stderr.decode(errors='replace')[:2000]}"
        )
    return playlist


def _write_master_manifest(
    dst: Path, renditions: list[HlsRendition],
) -> None:
    """Write the top-level variant playlist. hls.js parses this and
    builds the quality switcher from the EXT-X-STREAM-INF lines.

    `CODECS="avc1.4d401f,mp4a.40.2"` declares H.264 Main @ Level
    4.1 + AAC LC — covered by every modern browser. Without this
    line, conservative players may refuse to start playback until
    they've sniffed the first segment.
    """
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", ""]
    for r in renditions:
        lines.append(
            "#EXT-X-STREAM-INF:"
            f"BANDWIDTH={r.bandwidth_bps},"
            f'RESOLUTION={r.width}x{r.height},'
            'CODECS="avc1.4d401f,mp4a.40.2"'
        )
        lines.append(f"{r.label}/playlist.m3u8")
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")


def transcode_to_hls(
    src_path: Path,
    work_dir: Path,
    *,
    prefer_gpu: bool = True,
    segment_seconds: int = 6,
) -> HlsResult:
    """Synchronous HLS transcode. Run via `asyncio.to_thread` from
    the worker so the loop stays responsive — expect ~realtime wall
    cost on GPU for the master tier, plus parallel-ish encoding for
    each lower rendition.

    Raises RuntimeError on any ffmpeg failure (no partial output).
    """
    if not src_path.exists():
        raise FileNotFoundError(f"hls source missing: {src_path}")
    work_dir.mkdir(parents=True, exist_ok=True)

    meta = _probe_source(src_path)
    src_w, src_h = meta["width"], meta["height"]
    if src_w == 0 or src_h == 0:
        raise RuntimeError(
            f"hls: source has zero-dimension video stream ({src_path})"
        )

    long_edge = max(src_w, src_h)
    use_gpu = prefer_gpu and _probe_nvenc()

    # Build the ladder list. Master is always present.
    master_long_edge = min(long_edge, _MASTER_LONG_EDGE_CAP)
    master_w, master_h = _scaled_dims(src_w, src_h, master_long_edge)
    master_kbps = _master_bitrate_for(master_long_edge)
    ladders_to_run = [
        ("master", master_w, master_h, master_kbps, 128),
    ]
    # Lower ladders: only when the source can give us a meaningful
    # quality at that resolution. Skipping is fine — the player will
    # still adapt down to the lowest rendition we expose, which on a
    # 540p source means "the master at 540p" rather than upscaling
    # waste.
    for min_long, label, target_long, vkbps, akbps in _LADDERS_BY_LONG_EDGE:
        if long_edge >= min_long:
            w, h = _scaled_dims(src_w, src_h, target_long)
            ladders_to_run.append((label, w, h, vkbps, akbps))

    # Encode each rendition.
    renditions: list[HlsRendition] = []
    for label, w, h, vkbps, akbps in ladders_to_run:
        rendition_dir = work_dir / label
        playlist_path = _encode_one_rendition(
            src_path, rendition_dir,
            label=label, width=w, height=h,
            video_kbps=vkbps, audio_kbps=akbps,
            segment_seconds=segment_seconds,
            use_gpu=use_gpu,
        )
        segments = sorted(rendition_dir.glob("segment_*.ts"))
        segment_bytes = sum(s.stat().st_size for s in segments)
        renditions.append(HlsRendition(
            label=label, width=w, height=h,
            video_kbps=vkbps, audio_kbps=akbps,
            playlist_path=playlist_path,
            segment_paths=segments,
            segment_bytes=segment_bytes,
        ))
        logger.info(
            "hls: %s done — %d segments, %d bytes",
            label, len(segments), segment_bytes,
        )

    # Master manifest (variant playlist).
    master_path = work_dir / "master.m3u8"
    _write_master_manifest(master_path, renditions)

    # Poster — same helper as the legacy mp4 path.
    poster_path = work_dir / "poster.jpg"
    _extract_poster(src_path, poster_path, duration_s=meta["duration"])

    return HlsResult(
        master_playlist_path=master_path,
        renditions=renditions,
        poster_path=poster_path,
        poster_size=poster_path.stat().st_size,
        duration_s=meta["duration"],
        used_gpu=use_gpu,
    )


async def transcode_to_hls_async(
    src_path: Path,
    work_dir: Path,
    *,
    prefer_gpu: bool = True,
    segment_seconds: int = 6,
) -> HlsResult:
    """Async wrapper for use in the worker without blocking the loop."""
    import asyncio
    return await asyncio.to_thread(
        transcode_to_hls, src_path, work_dir,
        prefer_gpu=prefer_gpu, segment_seconds=segment_seconds,
    )
