"""Regression tests for U3 + S4 — share signed-URL hardening.

## U3 — share signed URLs not bound to recipient

Before, the HMAC payload was `(share_id, variant, expires)`. Two
recipients of the same image would mint identical URLs; either URL
served bytes to anyone. Audit log fired once per URL mint, not per
fetch.

Now the payload is `(share_id, recipient_user_id, variant, expires)`.
URL contains `?uid=<recipient>` and the byte-serving endpoint
verifies the HMAC against the supplied uid.

## S4 — `share.asset.viewed` audit fired at URL mint, not byte fetch

A leaked URL redeemed 47 times left 1 audit row. Owner couldn't
distinguish 1 view from 47 redemptions.

Now `share.asset.viewed` fires inside `share_signed_download`,
once per byte-serve. URL mint gets a lighter `share.asset.url_minted`
event so the dashboard can still distinguish "user opened the
share page" from "bytes were served."

These tests cover the signed_urls layer (signing/verifying with
recipient binding) and a static check on the api.shares handler
shape (audit firing inside the byte-fetch handler, not the URL
mint). End-to-end with a real DB lives in the integration suite.
"""
from __future__ import annotations

import inspect
import time
import uuid

import pytest

from backend.signed_urls import (
    make_signed_share_download,
    sign_share_download,
    verify_share_download,
)


# ---------- U3: HMAC layer ----------


def test_sign_share_download_binds_recipient_into_signature() -> None:
    """The same (share_id, variant, expires) signed for two
    different recipients must produce different HMACs."""
    share_id = uuid.uuid4()
    recipient_a = uuid.uuid4()
    recipient_b = uuid.uuid4()
    expires = int(time.time()) + 60

    sig_a = sign_share_download(share_id, recipient_a, "served", expires)
    sig_b = sign_share_download(share_id, recipient_b, "served", expires)
    assert sig_a != sig_b, (
        "URLs for two different recipients of the same share must "
        "produce different HMACs. The pre-U3 implementation produced "
        "identical sigs (audit U3 — leaked URL works for anyone)."
    )


def test_verify_share_rejects_mismatched_recipient() -> None:
    """A URL minted for recipient A must NOT verify when presented
    with recipient B's uid in the query string."""
    share_id = uuid.uuid4()
    recipient_a = uuid.uuid4()
    recipient_b = uuid.uuid4()
    expires = int(time.time()) + 60
    sig = sign_share_download(share_id, recipient_a, "served", expires)

    assert verify_share_download(
        share_id=share_id, recipient_user_id=recipient_a,
        variant="served", expires=expires, sig=sig,
    ), "Sig should verify for the recipient it was minted for."

    assert not verify_share_download(
        share_id=share_id, recipient_user_id=recipient_b,
        variant="served", expires=expires, sig=sig,
    ), (
        "Sig minted for recipient_a must not verify when presented "
        "with recipient_b's uid. The audit log at fetch time relies "
        "on this — a forged uid would otherwise let an attacker "
        "produce a clean audit row under someone else's identity."
    )


def test_make_signed_share_url_carries_uid_query_param() -> None:
    """The URL emitted by make_signed_share_download must contain
    the recipient uid so the verifier (which is on the receiving
    end of the URL params) can rebuild the same payload."""
    share_id = uuid.uuid4()
    recipient = uuid.uuid4()
    out = make_signed_share_download(
        base_url="http://test/",
        share_id=share_id,
        recipient_user_id=recipient,
        variant="served",
    )
    assert f"uid={recipient}" in out["url"], (
        "Signed URL is missing the uid query param. The byte-serving "
        "endpoint can't verify the recipient binding without it."
    )
    assert "expires=" in out["url"]
    assert "sig=" in out["url"]


# ---------- S4: audit-row placement ----------


def test_share_signed_download_audits_at_fetch_time() -> None:
    """Static-shape check: `share.asset.viewed` audit-log call must
    live inside `share_signed_download` (the byte-fetch handler).
    A regression that moves it back to `share_asset_url` (the URL
    mint endpoint) would silently undo the per-fetch trail."""
    from backend.api import shares as shares_mod
    fetch_src = inspect.getsource(shares_mod.share_signed_download)
    mint_src = inspect.getsource(shares_mod.share_asset_url)

    # Look for the call-site shape `action="share.asset.viewed"`,
    # not any incidental textual mention. Counts only real
    # audit-log invocations, not comments that happen to name the
    # action.
    fetch_calls = fetch_src.count('action="share.asset.viewed"')
    mint_calls = mint_src.count('action="share.asset.viewed"')
    assert fetch_calls >= 1, (
        "`share.asset.viewed` audit no longer fires inside "
        "share_signed_download. The S4 fix has been reverted; "
        "per-redemption trail is gone."
    )
    assert mint_calls == 0, (
        "`share.asset.viewed` audit reintroduced in share_asset_url. "
        "That re-creates the S4 gap — owner sees one row whether the "
        "URL is redeemed once or 47 times. The URL-mint audit should "
        "use the separate `share.asset.url_minted` action instead."
    )


def test_share_asset_url_audits_url_mint_separately() -> None:
    """The URL-mint endpoint logs a DIFFERENT action
    (`share.asset.url_minted`) so the owner can still distinguish
    'recipient opened the share page' from 'bytes were served'."""
    from backend.api import shares as shares_mod
    mint_src = inspect.getsource(shares_mod.share_asset_url)
    assert "share.asset.url_minted" in mint_src, (
        "The URL-mint action lost its separate audit row. The "
        "dashboard can no longer tell page-load apart from "
        "byte-serve."
    )


def test_share_signed_download_passes_uid_to_verifier() -> None:
    """The fetch handler must accept `uid` as a URL param and pass
    it to verify_share_download. Catches a refactor that drops the
    uid binding from the verify call."""
    from backend.api import shares as shares_mod
    src = inspect.getsource(shares_mod.share_signed_download)
    assert "recipient_user_id=" in src or "recipient_user_id =" in src, (
        "share_signed_download no longer passes recipient_user_id to "
        "verify_share_download. The HMAC would still verify but "
        "wouldn't be bound to the recipient anymore — anyone with "
        "the URL could redeem it."
    )
    # Also: the handler signature should declare `uid: UUID`.
    sig = inspect.signature(shares_mod.share_signed_download)
    assert "uid" in sig.parameters, (
        "share_signed_download is missing the `uid` URL parameter."
    )
