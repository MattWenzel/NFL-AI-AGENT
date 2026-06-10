"""Transactional email via Resend.

Thin wrapper so callers don't import the SDK directly. If `RESEND_API_KEY`
is unset, sends are no-ops with a warning log — dev setups aren't blocked,
but EMAIL_VERIFICATION_REQUIRED=1 in prod still requires the key.

To swap providers later (Postmark, SES, SMTP), replace the internals of
`_send` and keep the public signatures. Callers stay unchanged.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from backend.domain.auth.errors import EmailError
from backend.config import APP_BASE_URL, EMAIL_FROM_ADDRESS, RESEND_API_KEY

logger = logging.getLogger(__name__)


_resend_configured = False


def _ensure_resend():
    global _resend_configured
    if _resend_configured:
        return True
    if not RESEND_API_KEY or not EMAIL_FROM_ADDRESS:
        return False
    try:
        import resend  # type: ignore
    except ImportError:
        logger.warning("resend package not installed — email sends will no-op")
        return False
    resend.api_key = RESEND_API_KEY
    _resend_configured = True
    return True


def _send(*, to: str, subject: str, html: str, text: str) -> None:
    if not _ensure_resend():
        logger.warning(
            "Email send suppressed (RESEND_API_KEY or EMAIL_FROM_ADDRESS unset): to=%s subject=%r",
            to,
            subject,
        )
        return
    import resend  # type: ignore

    try:
        resend.Emails.send(
            {
                "from": EMAIL_FROM_ADDRESS,
                "to": [to],
                "subject": subject,
                "html": html,
                "text": text,
            }
        )
    except Exception as exc:
        logger.exception("Resend send failed to %s: %s", to, exc)
        raise EmailError(f"Could not send email: {exc}") from exc


def build_verification_link(token: str) -> str:
    return f"{APP_BASE_URL}/#/verify?{urlencode({'token': token})}"


def send_verification_email(*, to: str, token: str) -> None:
    link = build_verification_link(token)
    subject = "Verify your NFL StatSource account"
    text = (
        f"Welcome to NFL StatSource!\n\n"
        f"Click the link below to verify your email and finish signing up:\n{link}\n\n"
        f"The link expires in 24 hours. If you didn't sign up, ignore this email."
    )
    html = (
        f"<p>Welcome to NFL StatSource!</p>"
        f"<p><a href=\"{link}\">Click here to verify your email</a> and finish signing up.</p>"
        f"<p>Or paste this link into your browser:<br><code>{link}</code></p>"
        f"<p>The link expires in 24 hours. If you didn't sign up, ignore this email.</p>"
    )
    _send(to=to, subject=subject, html=html, text=text)


def send_password_reset_email(*, to: str, token: str) -> None:
    """Reset link for POST /auth/request-password-reset. The frontend's
    AuthWall parses the `#/reset?token=…` hash into its reset form."""
    link = f"{APP_BASE_URL}/#/reset?{urlencode({'token': token})}"
    subject = "Reset your NFL StatSource password"
    text = (
        f"Someone (hopefully you) asked to reset your NFL StatSource password.\n\n"
        f"Click the link below to set a new password:\n{link}\n\n"
        f"The link expires in 24 hours. If you didn't request this, ignore this email."
    )
    html = (
        f"<p>Someone (hopefully you) asked to reset your NFL StatSource password.</p>"
        f"<p><a href=\"{link}\">Click here to set a new password</a>.</p>"
        f"<p>Or paste this link into your browser:<br><code>{link}</code></p>"
        f"<p>The link expires in 24 hours. If you didn't request this, ignore this email.</p>"
    )
    _send(to=to, subject=subject, html=html, text=text)
