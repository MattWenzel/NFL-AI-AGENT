"""Google OAuth sign-in endpoints.

Two routes, both GETs (Google redirects the browser, so a POST body isn't
possible). CSRF-exempt by the usual rule (safe methods); `/callback`'s own
`state` query parameter is the anti-CSRF proof — we generated it at `/start`
and stashed it in the pending-flow registry.

Failure paths all redirect to `/?oauth_error=<code>` so the frontend can
surface a single-line banner, rather than rendering a server-side error
page that would break the SPA.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from backend.api.dependencies import (
    get_google_oauth_service,
    get_process_state,
)
from backend.server.request_context import audit_from_request
from backend.server.session import set_auth_cookies
from backend.server.process_state import AppProcessState
from backend.application.oauth.google.errors import (
    GoogleOAuthDisabledError,
    GoogleOAuthEmailUnverifiedError,
    GoogleOAuthInvalidStateError,
    GoogleOAuthServiceError,
)
from backend.application.oauth.google.service import GoogleOAuthService
from backend.application.oauth.google.types import SignInOutcome

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/oauth/google", tags=["auth"])


def _redirect_error(reason: str) -> RedirectResponse:
    # 302 back to the SPA root with a query param the frontend reads.
    return RedirectResponse(
        url=f"/?{urlencode({'oauth_error': reason})}",
        status_code=302,
    )


@router.get("/start")
async def start(
    request: Request,
    service: GoogleOAuthService = Depends(get_google_oauth_service),
    process_state: AppProcessState = Depends(get_process_state),
):
    # Rate-limit start requests per IP so a script can't spam state rows.
    try:
        process_state.register_limiter.check(request)
    except Exception:  # noqa: BLE001 — RateLimiter raises HTTPException
        return _redirect_error("rate_limited")
    try:
        auth_url = await service.begin_signin(audit_from_request(request))
    except GoogleOAuthDisabledError:
        return _redirect_error("oauth_disabled")
    except GoogleOAuthServiceError as exc:
        logger.warning("Google OAuth start failed: %s", exc)
        return _redirect_error("start_failed")
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    service: GoogleOAuthService = Depends(get_google_oauth_service),
):
    # Google sends ?error=access_denied etc. when the user cancels at the
    # consent screen. Bounce back with a generic message.
    if error:
        logger.info("Google OAuth callback received provider error: %s", error)
        return _redirect_error("cancelled")
    if not code or not state:
        return _redirect_error("missing_params")

    try:
        outcome = await service.complete_callback(
            code=code, state=state, audit=audit_from_request(request)
        )
    except GoogleOAuthInvalidStateError:
        return _redirect_error("invalid_state")
    except GoogleOAuthEmailUnverifiedError:
        return _redirect_error("email_unverified")
    except GoogleOAuthDisabledError:
        return _redirect_error("oauth_disabled")
    except GoogleOAuthServiceError as exc:
        logger.warning("Google OAuth callback failed: %s", exc)
        return _redirect_error("signin_failed")

    if isinstance(outcome, SignInOutcome):
        response = RedirectResponse(url="/", status_code=302)
        set_auth_cookies(response=response, session=outcome.session, request=request)
        return response

    # LinkOutcome — already-authenticated user finished a settings-initiated
    # link. Redirect back to settings with a flash, keep existing cookies.
    return RedirectResponse(url="/?oauth_linked=google", status_code=302)
