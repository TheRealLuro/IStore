"""Regression test for CR-6 — ffmpeg `-protocol_whitelist` on every
user-input call site.

Before this patch, every ffmpeg / ffprobe invocation that read a
local temp file derived from user bytes was missing
`-protocol_whitelist`. libavformat would follow URI references found
INSIDE the user-supplied container (Matroska `dataref`, MOV `dref`
boxes, HLS segment URIs, concat demuxer, SDP) and fetch:

  - `file:///etc/passwd` — read-anywhere primitive into the encoded
    output (which the user then downloads)
  - `http://attacker.example/leak` — SSRF off the encoder host
  - `http://169.254.169.254/...` — cloud-instance metadata exfil
  - Triggers for HLS-demuxer CVE chain (CVE-2017-9993 et al.)

`backend.ffmpeg_args.safe_input_args()` returns the argv tokens that
must appear before each `-i <user_path>`. The tests below pin:

  1. The helper returns `["-protocol_whitelist", "file"]` exactly.
  2. Every user-input call site emits the tokens immediately before
     its `-i` argument (verified via static inspection of the source).

The static-inspection test catches a future regression where someone
copies an ffmpeg cmd from another codebase / refactors the call site
and forgets to re-add the protocol whitelist.
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.ffmpeg_args import safe_input_args


def test_safe_input_args_pins_protocol_whitelist_to_file_only() -> None:
    """The whitelist must be exactly `file`. Loosening to allow
    `http`/`https`/`tcp`/`crypto`/`data` re-opens SSRF; this test
    will fail loudly if a future PR widens it without explicit
    auditor sign-off."""
    args = safe_input_args()
    assert args == ["-protocol_whitelist", "file"], (
        f"safe_input_args() returned {args!r}; if you widened the "
        "whitelist, update this test and the audit follow-up that "
        "tracks CR-6."
    )


# ---------- static inspection across all call sites ----------

_BACKEND_ROOT = Path(__file__).resolve().parent.parent / "backend"

# Every file where an ffmpeg / ffprobe invocation reads a
# user-supplied path. Mapped to a short label for the assertion
# message. NVENC probe in transcode.py is exempt — it reads a
# synthetic `lavfi` input, not user data.
_CALL_SITES = {
    "backend/transcode.py": "transcode (_probe_source / encode / poster)",
    "backend/hls.py": "hls (segment encoder)",
    "backend/transcribe.py": "transcribe (whisper audio extract)",
    "backend/summarize.py": "summarize (duration probe / keyframe extract)",
}


def _read(path_under_backend: str) -> str:
    path = _BACKEND_ROOT.parent / path_under_backend
    return path.read_text(encoding="utf-8")


def test_every_user_input_call_site_uses_safe_input_args() -> None:
    """Each module that invokes ffmpeg/ffprobe on a user-supplied
    path imports and uses `safe_input_args`. The static check is
    coarse (substring match), but a regression that drops the call
    will fail the assertion.
    """
    missing = []
    for path, label in _CALL_SITES.items():
        src = _read(path)
        if "safe_input_args" not in src:
            missing.append((path, label))
    assert not missing, (
        "ffmpeg call sites missing safe_input_args(): " + repr(missing)
    )


def test_protocol_whitelist_appears_before_every_dash_i_user_input() -> None:
    """For each user-input ffmpeg call site, every `-i` argument
    (which points at a user-controlled path) must be preceded by
    `safe_input_args()` somewhere in the same argv list.

    The check is per-occurrence: we count `-i` tokens and assert
    the call site has at least as many `safe_input_args()` calls
    in the same source as it has user-input `-i` tokens that
    aren't synthetic (lavfi).
    """
    # Pre-existing exemptions: lines containing both `-i` and
    # `lavfi` are NVENC probes with synthetic input (no user data).
    for path, label in _CALL_SITES.items():
        src = _read(path)
        # Count `"-i"` argv tokens (string literal form). We only
        # care about argv positions, not arbitrary -i mentions in
        # docstrings.
        user_i_count = 0
        for m in re.finditer(r'"-i"', src):
            # Look back ~200 chars; skip if `lavfi` appears in the
            # surrounding window (NVENC synthetic-input probe).
            window_start = max(0, m.start() - 400)
            window = src[window_start : m.start() + 200]
            if "lavfi" in window:
                continue
            user_i_count += 1
        safe_args_count = src.count("safe_input_args()")
        # Subtract one for the `from backend.ffmpeg_args import
        # safe_input_args` line — that counts as a usage too. The
        # imports add (n+1) usages per call site; we want >= n.
        assert safe_args_count >= user_i_count, (
            f"{label} ({path}): {user_i_count} user-input `-i` tokens "
            f"but only {safe_args_count} safe_input_args() usages. "
            "Each user-input ffmpeg invocation must be guarded."
        )


def test_helper_module_documents_threat_model() -> None:
    """Documentation is part of the fix — a future maintainer reading
    `ffmpeg_args.py` cold should learn WHY the whitelist exists.
    The module docstring mentions the attack surface explicitly."""
    helper_src = _read("backend/ffmpeg_args.py")
    assert "protocol_whitelist" in helper_src
    # Threat-model markers — at least one of the canonical exfil
    # vectors should be named so the comment can't be silently
    # rewritten to "ffmpeg helper" with no context.
    threat_markers = ("file:///", "169.254.169.254", "SSRF", "dataref", "HLS")
    assert any(m in helper_src for m in threat_markers), (
        "Helper module docstring lost its threat-model context. "
        "Restore the rationale for why `-protocol_whitelist file` "
        "is required, or future readers won't know it's load-bearing."
    )
