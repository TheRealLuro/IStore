"""Email delivery (Phase 13 — C6) with HTML + plaintext multipart.

Single entry point `send_email(to, subject, body, html_body=None)`
that uses SMTP when configured, otherwise logs to the console (dev
mode). When `html_body` is provided, the message is sent as
multipart/alternative — the plaintext is the fallback for clients
that can't render HTML, and the HTML body carries the styled
sign-in button / branded layout.

Environment variables (set by `scripts/setup.py`):
  SMTP_HOST       — empty disables real delivery (console-only).
  SMTP_PORT       — default 587.
  SMTP_USER, SMTP_PASS — STARTTLS auth.
  SMTP_FROM       — From: header value.
  FRONTEND_BASE_URL — for building reset / verify / signin links.
"""
from __future__ import annotations

import logging
import smtplib
import sys
from email.message import EmailMessage

from backend.config import settings

logger = logging.getLogger(__name__)


def send_email(
    to: str,
    subject: str,
    body: str,
    html_body: str | None = None,
) -> bool:
    """Deliver `body` (plain text) — and optionally `html_body` — to
    `to`. Returns True on success / on console-log in dev mode;
    False only on a real SMTP error so callers don't need to
    differentiate "no SMTP configured" from "SMTP works".

    When `html_body` is provided the message is sent as
    multipart/alternative. Gmail/Outlook/Apple Mail all prefer the
    HTML part; plaintext is the fallback for terminal clients and
    spam filters that down-rank HTML-only messages.
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
    if html_body:
        # add_alternative on an EmailMessage with an existing text
        # content body promotes the message to multipart/alternative
        # with both parts; the HTML part is presented first per RFC
        # 2046, which tells clients to prefer it.
        msg.add_alternative(html_body, subtype="html")

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


# ---------- HTML shell ------------------------------------------------------
#
# Shared layout for every transactional email: centered card on a
# dark background, "neuthek" wordmark at top, content in the middle,
# small "you're getting this because…" footer. All inline styles
# because most webmail clients strip <style> blocks. Tested in
# Gmail (web + iOS), Outlook web, Apple Mail. The button uses an
# anchor styled like a button — bulletproof across clients (some
# strip <button>).

_BRAND_BG = "#0a0a0a"
_BRAND_INK = "#fafafa"
_BRAND_INK_DIM = "#a3a3a3"
_BRAND_ACCENT = "#fafafa"
_BRAND_ACCENT_INK = "#0a0a0a"
_BRAND_CARD_BG = "#171717"
_BRAND_BORDER = "#262626"


def _email_shell(
    headline: str,
    intro_html: str,
    primary_button: dict | None = None,
    alt_html: str | None = None,
    footer_html: str | None = None,
) -> str:
    """Build a complete HTML message body.

    Arguments
    ---------
    headline       — h1 text, e.g. "Sign in to neuthek".
    intro_html     — first paragraph(s), already-escaped HTML. We
                     never interpolate user-supplied substrings here
                     today; if that changes the caller must escape.
    primary_button — None or {"label": str, "href": str}. Renders
                     as a centered pill-shaped anchor.
    alt_html       — secondary content block (e.g. the 6-digit code
                     chip or recovery-code grid), HTML.
    footer_html    — copy below the divider; defaults to the
                     generic "you can ignore this email" boilerplate.
    """
    button_html = ""
    if primary_button:
        button_html = (
            f'<table role="presentation" cellspacing="0" cellpadding="0" border="0" '
            f'  align="center" style="margin:24px auto 8px">'
            f'  <tr><td style="border-radius:999px;background:{_BRAND_ACCENT}">'
            f'    <a href="{primary_button["href"]}" target="_blank" rel="noopener" '
            f'       style="display:inline-block;padding:13px 28px;font-size:15px;'
            f'              font-weight:600;font-family:-apple-system,BlinkMacSystemFont,'
            f'              \'Segoe UI\',sans-serif;color:{_BRAND_ACCENT_INK};'
            f'              text-decoration:none;border-radius:999px;letter-spacing:0.01em">'
            f'      {primary_button["label"]}'
            f'    </a>'
            f'  </td></tr>'
            f'</table>'
        )

    alt_block = f'<div style="margin-top:16px">{alt_html}</div>' if alt_html else ""

    footer = footer_html or (
        '<p style="margin:24px 0 0;font-size:12px;line-height:1.6;'
        f'color:{_BRAND_INK_DIM}">'
        "If you didn&rsquo;t request this email, you can safely ignore "
        "it — no action is needed."
        "</p>"
    )

    return f"""\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>neuthek</title>
</head>
<body style="margin:0;padding:0;background:{_BRAND_BG};color:{_BRAND_INK};
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
             -webkit-font-smoothing:antialiased">
  <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%"
         style="background:{_BRAND_BG};padding:32px 16px">
    <tr><td align="center">
      <table role="presentation" cellspacing="0" cellpadding="0" border="0"
             style="max-width:480px;width:100%;background:{_BRAND_CARD_BG};
                    border:1px solid {_BRAND_BORDER};border-radius:16px;
                    padding:32px;color:{_BRAND_INK}">
        <tr><td>
          <div style="font-size:14px;font-weight:600;letter-spacing:0.08em;
                      color:{_BRAND_INK_DIM};text-transform:uppercase;margin-bottom:24px">
            neuthek
          </div>
          <h1 style="margin:0 0 16px;font-size:22px;line-height:1.3;font-weight:700;
                     color:{_BRAND_INK}">{headline}</h1>
          <div style="font-size:14px;line-height:1.6;color:{_BRAND_INK}">
            {intro_html}
          </div>
          {button_html}
          {alt_block}
          {footer}
        </td></tr>
      </table>
      <div style="font-size:11px;color:{_BRAND_INK_DIM};margin-top:18px;line-height:1.6">
        neuthek &middot; your AI-aware personal cloud
      </div>
    </td></tr>
  </table>
</body>
</html>"""


# ---------- templates ------------------------------------------------------


def send_verify_email(to: str, token: str) -> bool:
    link = f"{settings.frontend_base_url}/verify?token={token}"
    text = (
        "Welcome to neuthek!\n\n"
        "Please confirm your email address by clicking the link below "
        "(valid for 1 hour):\n\n"
        f"{link}\n\n"
        "If you didn't create an account, you can ignore this message."
    )
    html = _email_shell(
        headline="Welcome to neuthek",
        intro_html=(
            "<p style='margin:0'>Confirm your email address to finish "
            "setting up your account. The link below is valid for "
            "<strong>1 hour</strong>.</p>"
        ),
        primary_button={"label": "Confirm email", "href": link},
        footer_html=(
            '<p style="margin:24px 0 0;font-size:12px;line-height:1.6;'
            f'color:{_BRAND_INK_DIM}">'
            "If you didn&rsquo;t create a neuthek account, you can safely "
            "ignore this email."
            "</p>"
        ),
    )
    return send_email(to, "Confirm your neuthek email", text, html)


def send_reset_email(to: str, token: str) -> bool:
    link = f"{settings.frontend_base_url}/reset?token={token}"
    text = (
        "We received a request to reset your neuthek password.\n\n"
        f"Use this link within 15 minutes:\n\n{link}\n\n"
        "If you didn't ask to reset your password, you can safely "
        "ignore this email — your password is unchanged."
    )
    html = _email_shell(
        headline="Reset your password",
        intro_html=(
            "<p style='margin:0'>Click the button below to set a new "
            "password. This link expires in <strong>15 minutes</strong>.</p>"
        ),
        primary_button={"label": "Reset password", "href": link},
        footer_html=(
            '<p style="margin:24px 0 0;font-size:12px;line-height:1.6;'
            f'color:{_BRAND_INK_DIM}">'
            "If you didn&rsquo;t ask to reset your password, you can safely "
            "ignore this email — your password is unchanged."
            "</p>"
        ),
    )
    return send_email(to, "Reset your neuthek password", text, html)


def send_signin_link_email(to: str, token: str, code: str | None = None) -> bool:
    """Magic-link sign-in (passwordless). The user typed their email
    on the auth screen; we mail them BOTH a one-shot link AND a 6-
    digit code so they can sign in either way:
      - Click the button → /signin?token=… consumes the JWT and lands
        them in the gallery.
      - Type the code into the "Enter sign-in code" form on the
        auth screen — useful when the email is on a different
        device than the one they want to sign in on (TV, kiosk,
        another laptop).
    Both expire together after 15 minutes; using either invalidates
    the other.

    `code` is Optional to keep the helper backwards-compatible with
    any pre-update call site; new callers should always pass it.
    """
    link = f"{settings.frontend_base_url}/signin?token={token}"
    if code:
        text = (
            "Sign in to neuthek by clicking the link below, OR enter "
            "the 6-digit code on the sign-in page.\n\n"
            f"Link:\n{link}\n\n"
            f"Or use this code:\n\n  {code}\n\n"
            "Both expire in 15 minutes and can only be used once. "
            "If you didn't request this email, you can safely ignore "
            "it — no one can sign in without the link or code."
        )
        # Spaced code for visual scannability + monospace so it's
        # clearly a code (not a phone number or order ID).
        code_pretty = f"{code[:3]} {code[3:]}"
        alt = (
            '<div style="margin-top:24px;text-align:center">'
            f'  <div style="font-size:11px;font-weight:600;letter-spacing:0.08em;'
            f'              color:{_BRAND_INK_DIM};text-transform:uppercase;margin-bottom:8px">'
            "    OR ENTER THIS CODE"
            "  </div>"
            f'  <div style="display:inline-block;padding:14px 22px;'
            f'              background:{_BRAND_BG};border:1px solid {_BRAND_BORDER};'
            f'              border-radius:12px;font-family:ui-monospace,SFMono-Regular,'
            f'              \'SF Mono\',Menlo,monospace;font-size:24px;font-weight:600;'
            f'              letter-spacing:0.20em;color:{_BRAND_INK}">'
            f"    {code_pretty}"
            "  </div>"
            "</div>"
        )
        html = _email_shell(
            headline="Sign in to neuthek",
            intro_html=(
                "<p style='margin:0'>Click the button below to sign in. "
                "If you can&rsquo;t use the link (signing in on a "
                "different device), type the 6-digit code on the sign-in "
                "page instead. Both expire in <strong>15 minutes</strong>.</p>"
            ),
            primary_button={"label": "Sign in to neuthek", "href": link},
            alt_html=alt,
            footer_html=(
                '<p style="margin:24px 0 0;font-size:12px;line-height:1.6;'
                f'color:{_BRAND_INK_DIM}">'
                "If you didn&rsquo;t request this sign-in, you can safely "
                "ignore this email. Each link and code can only be used "
                "once."
                "</p>"
            ),
        )
    else:
        text = (
            "Sign in to neuthek by clicking the link below.\n\n"
            f"{link}\n\n"
            "The link is valid for 15 minutes and can only be used once. "
            "If you didn't request this email, you can safely ignore it — "
            "no one can sign in without clicking the link."
        )
        html = _email_shell(
            headline="Sign in to neuthek",
            intro_html=(
                "<p style='margin:0'>Click the button below to sign in. "
                "This link expires in <strong>15 minutes</strong>.</p>"
            ),
            primary_button={"label": "Sign in to neuthek", "href": link},
        )
    return send_email(to, "Your neuthek sign-in link", text, html)


def send_recovery_codes_email(to: str, codes: list[str]) -> bool:
    """Sent once when codes are (re)generated. Codes themselves are
    only ever shown once; we don't keep plaintext in the DB."""
    formatted = "\n".join(f"  {c}" for c in codes)
    text = (
        "Your new neuthek recovery codes are:\n\n"
        f"{formatted}\n\n"
        "Each code can be used once to sign in if you lose access to "
        "your password. Save them somewhere safe — you won't be able "
        "to see them again."
    )
    # Codes laid out two-up in a grid so the HTML version is
    # scannable too. Monospace + larger letter-spacing for OCR-
    # friendliness if the user prints the email.
    rows = []
    for i in range(0, len(codes), 2):
        left = codes[i]
        right = codes[i + 1] if i + 1 < len(codes) else ""
        rows.append(
            f'<tr>'
            f'  <td style="padding:8px;background:{_BRAND_BG};border:1px solid {_BRAND_BORDER};'
            f'             border-radius:8px;font-family:ui-monospace,monospace;'
            f'             font-size:15px;color:{_BRAND_INK};text-align:center">'
            f"    {left}"
            "  </td>"
            f'  <td style="padding:8px;background:{_BRAND_BG};border:1px solid {_BRAND_BORDER};'
            f'             border-radius:8px;font-family:ui-monospace,monospace;'
            f'             font-size:15px;color:{_BRAND_INK};text-align:center">'
            f"    {right}"
            "  </td>"
            "</tr>"
        )
    grid = (
        '<table role="presentation" cellspacing="6" cellpadding="0" border="0" '
        '       width="100%" style="margin-top:16px;border-collapse:separate;border-spacing:6px">'
        + "".join(rows) +
        "</table>"
    )
    html = _email_shell(
        headline="Your recovery codes",
        intro_html=(
            "<p style='margin:0'>Each code can be used once to sign in "
            "if you lose access to your password or authenticator. "
            "<strong>Save them somewhere safe</strong> &mdash; you won&rsquo;t "
            "be able to see them again.</p>"
        ),
        alt_html=grid,
        footer_html=(
            '<p style="margin:24px 0 0;font-size:12px;line-height:1.6;'
            f'color:{_BRAND_INK_DIM}">'
            "We store only hashes of these codes &mdash; if you lose them, "
            "regenerate a fresh set from Settings &rarr; Security &rarr; "
            "Recovery codes. Doing so invalidates this list."
            "</p>"
        ),
    )
    return send_email(to, "Your neuthek recovery codes", text, html)
