"""Email delivery (Phase 13 — C6).

Single entry point `send_email(to, subject, body)` that uses SMTP when
configured, otherwise logs to the console (dev mode). Templates live
inline as f-strings — no Jinja for the handful of transactional mails
we actually send.

Environment variables (set by `scripts/setup.py`):
  SMTP_HOST       — empty disables real delivery (console-only).
  SMTP_PORT       — default 587.
  SMTP_USER, SMTP_PASS — STARTTLS auth.
  SMTP_FROM       — From: header value.
  FRONTEND_BASE_URL — for building reset / verify links.
"""
from __future__ import annotations

import logging
import smtplib
import sys
from email.message import EmailMessage
from typing import Optional

from backend.config import settings

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, body: str) -> bool:
    """Deliver `body` to `to`. Returns True on success / on console-log
    in dev mode; False only on a real SMTP error so callers don't need
    to differentiate "no SMTP configured" from "SMTP works".
    """
    if not settings.smtp_host:
        # Dev-mode fallback: print to stderr unconditionally. We can't
        # rely on logger.info because uvicorn's default config silences
        # non-uvicorn loggers below WARNING — verification links would
        # vanish, which makes the whole flow untestable locally.
        sys.stderr.write(
            f"\n[email-stub] To: {to}\nSubject: {subject}\n{body}\n"
            f"{'-' * 60}\n"
        )
        sys.stderr.flush()
        return True

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
            s.ehlo()
            try:
                s.starttls()
            except smtplib.SMTPNotSupportedError:
                pass  # plaintext relay (rare in prod, common in CI)
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_pass or "")
            s.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as e:
        logger.exception("smtp send failed: %s", e)
        return False


# ---------- templates ------------------------------------------------------


def send_verify_email(to: str, token: str) -> bool:
    link = f"{settings.frontend_base_url}/verify?token={token}"
    body = (
        "Welcome to neuthek!\n\n"
        "Please confirm your email address by clicking the link below "
        "(valid for 1 hour):\n\n"
        f"{link}\n\n"
        "If you didn't create an account, you can ignore this message."
    )
    return send_email(to, "Confirm your neuthek email", body)


def send_reset_email(to: str, token: str) -> bool:
    link = f"{settings.frontend_base_url}/reset?token={token}"
    body = (
        "We received a request to reset your neuthek password.\n\n"
        f"Use this link within 15 minutes:\n\n{link}\n\n"
        "If you didn't ask to reset your password, you can safely "
        "ignore this email — your password is unchanged."
    )
    return send_email(to, "Reset your neuthek password", body)


def send_signin_link_email(to: str, token: str) -> bool:
    """Magic-link sign-in (passwordless). The user typed their email
    on the auth screen; we mail them a one-shot link that lands on
    /signin?token=… and trades the JWT-shaped token for a session
    JWT via POST /auth/email-link/consume. 15-minute TTL, single-use.
    """
    link = f"{settings.frontend_base_url}/signin?token={token}"
    body = (
        "Sign in to neuthek by clicking the link below.\n\n"
        f"{link}\n\n"
        "The link is valid for 15 minutes and can only be used once. "
        "If you didn't request this email, you can safely ignore it — "
        "no one can sign in without clicking the link."
    )
    return send_email(to, "Your neuthek sign-in link", body)


def send_recovery_codes_email(to: str, codes: list[str]) -> bool:
    """Sent once when codes are (re)generated. Codes themselves are
    only ever shown once; we don't keep plaintext in the DB."""
    formatted = "\n".join(f"  {c}" for c in codes)
    body = (
        "Your new neuthek recovery codes are:\n\n"
        f"{formatted}\n\n"
        "Each code can be used once to sign in if you lose access to "
        "your password. Save them somewhere safe — you won't be able "
        "to see them again."
    )
    return send_email(to, "Your neuthek recovery codes", body)
