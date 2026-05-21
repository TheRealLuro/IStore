"""Regression test for A8 — JWT revocation via token_version.

The `VersionedJWTStrategy` adds a `tv` claim to every minted JWT
and rejects tokens whose claim doesn't match the user row's
`token_version`. Bumping the column (in
`UserManager.on_after_reset_password` or `two_factor_disable`)
therefore invalidates every live session for that user.

These tests exercise the strategy directly with stub user objects
so we don't need the DB. End-to-end coverage (real Postgres,
hitting `/users/me` with a stale JWT after a password reset) lives
in the integration suite that's currently blocked by an unrelated
migration gap; the unit-level pin here catches the regression that
matters: a future PR that drops the `tv` claim from `write_token`
or stops verifying it in `read_token`.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt as pyjwt
import pytest

from backend.auth.users import VersionedJWTStrategy


def _stub_user(*, token_version: int = 1, user_id=None):
    """Minimal user stand-in. `read_token`'s parent only needs `id`,
    `is_active`, and `is_verified` — we don't go through the
    UserManager path so those aren't critical here."""
    return SimpleNamespace(
        id=user_id or uuid.uuid4(),
        token_version=token_version,
        is_active=True,
        is_verified=True,
    )


def _strategy() -> VersionedJWTStrategy:
    return VersionedJWTStrategy(
        secret="test-secret-key-at-least-32-chars-long",
        lifetime_seconds=3600,
    )


@pytest.mark.asyncio
async def test_write_token_embeds_tv_claim() -> None:
    strat = _strategy()
    user = _stub_user(token_version=7)
    token = await strat.write_token(user)
    # Decode without verifying audience (we just want the payload).
    payload = pyjwt.decode(
        token, strat.decode_key, algorithms=["HS256"], audience=strat.token_audience,
    )
    assert payload[strat.CLAIM_TOKEN_VERSION] == 7
    assert payload["sub"] == str(user.id)


@pytest.mark.asyncio
async def test_write_token_handles_missing_attr_defaults_to_one() -> None:
    """A user row without a `token_version` attribute (legacy ORM
    instance somehow) should default to 1, not crash."""
    strat = _strategy()
    user = SimpleNamespace(id=uuid.uuid4(), is_active=True, is_verified=True)
    token = await strat.write_token(user)
    payload = pyjwt.decode(
        token, strat.decode_key, algorithms=["HS256"], audience=strat.token_audience,
    )
    assert payload[strat.CLAIM_TOKEN_VERSION] == 1


@pytest.mark.asyncio
async def test_read_token_accepts_matching_tv() -> None:
    strat = _strategy()
    user = _stub_user(token_version=3)
    token = await strat.write_token(user)

    # Mock user_manager to return the same user (current_tv=3)
    user_manager = AsyncMock()
    user_manager.parse_id.return_value = user.id
    user_manager.get.return_value = user

    result = await strat.read_token(token, user_manager)
    assert result is not None
    assert result.id == user.id


@pytest.mark.asyncio
async def test_read_token_rejects_mismatched_tv() -> None:
    """The attack we're defending: attacker has a JWT minted when
    token_version was 3; the user has since reset their password
    (token_version is now 4). The token must be rejected."""
    strat = _strategy()
    old_user = _stub_user(token_version=3)
    token = await strat.write_token(old_user)

    # Same user object, but now token_version=4 (post-reset).
    bumped_user = _stub_user(token_version=4, user_id=old_user.id)
    user_manager = AsyncMock()
    user_manager.parse_id.return_value = old_user.id
    user_manager.get.return_value = bumped_user

    result = await strat.read_token(token, user_manager)
    assert result is None, (
        "Token with stale tv=3 must be rejected after the user's "
        "token_version bumped to 4."
    )


@pytest.mark.asyncio
async def test_read_token_treats_missing_claim_as_tv_one() -> None:
    """Backwards-compat: tokens minted by the pre-A8 build have no
    `tv` claim. They must still validate against the default
    `token_version=1` for the deploy window."""
    strat = _strategy()
    # Manually craft a JWT WITHOUT the tv claim (simulates pre-A8 mint).
    from fastapi_users.jwt import generate_jwt
    user_id = uuid.uuid4()
    legacy_token = generate_jwt(
        {"sub": str(user_id), "aud": strat.token_audience},
        strat.encode_key,
        strat.lifetime_seconds,
        algorithm="HS256",
    )

    user = _stub_user(token_version=1, user_id=user_id)
    user_manager = AsyncMock()
    user_manager.parse_id.return_value = user_id
    user_manager.get.return_value = user

    result = await strat.read_token(legacy_token, user_manager)
    assert result is not None, (
        "Pre-A8 token (no `tv` claim) should authenticate against a "
        "user with token_version=1 — no forced sign-out at deploy."
    )


@pytest.mark.asyncio
async def test_read_token_rejects_invalid_tv_type() -> None:
    """A maliciously hand-crafted token with `tv='abc'` must be
    rejected (not crash, not coerce)."""
    strat = _strategy()
    from fastapi_users.jwt import generate_jwt
    user_id = uuid.uuid4()
    bad_token = generate_jwt(
        {"sub": str(user_id), "aud": strat.token_audience, "tv": "abc"},
        strat.encode_key,
        strat.lifetime_seconds,
        algorithm="HS256",
    )

    user = _stub_user(token_version=1, user_id=user_id)
    user_manager = AsyncMock()
    user_manager.parse_id.return_value = user_id
    user_manager.get.return_value = user

    result = await strat.read_token(bad_token, user_manager)
    assert result is None


@pytest.mark.asyncio
async def test_read_token_returns_none_for_none_token() -> None:
    """The parent contract: None in → None out. We add code on top
    of the parent, so a None token shouldn't NPE in the new path."""
    strat = _strategy()
    user_manager = AsyncMock()
    assert await strat.read_token(None, user_manager) is None


def test_model_has_token_version_column() -> None:
    """The migration + model edit must both land — a regression
    that adds the migration without the model column (or vice
    versa) would silently make every JWT read fail."""
    from backend.models import User
    assert hasattr(User, "token_version"), (
        "User.token_version column is missing from the ORM model. "
        "The migration 0040_user_token_version.py adds it; the model "
        "needs the matching `mapped_column` declaration."
    )
