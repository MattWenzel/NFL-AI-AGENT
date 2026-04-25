"""Canonical names for security-audit events.

Every value written to `security_events.event_type` must be a member of
this enum. Strings literals scattered across services were typo-prone
(a misspelled `"login_succes"` would silently land in the audit table
as a new category); the enum gives a single source of truth and lets
static analysis catch typos at the call site.

`(str, Enum)` is the pre-3.11 spelling of `StrEnum`. Instances are
real strings, so existing code that compares against literal strings
(tests, log filters) keeps working without changes — `AuditEvent.LOGIN_SUCCESS == "login_success"` is True.
"""

from __future__ import annotations

from enum import Enum


class AuditEvent(str, Enum):
    # Authentication lifecycle
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGIN_LOCKED = "login_locked"
    LOGIN_BLOCKED_UNVERIFIED = "login_blocked_unverified"
    LOGOUT = "logout"
    PASSWORD_CHANGED = "password_changed"
    ACCOUNT_DELETED = "account_deleted"

    # Email verification
    REGISTRATION_PENDING = "registration_pending"
    EMAIL_VERIFIED = "email_verified"
    VERIFICATION_RESENT = "verification_resent"
    VERIFICATION_RESENT_IGNORED = "verification_resent_ignored"

    # OAuth (Codex + Google)
    OAUTH_SIGNIN_STARTED = "oauth_signin_started"
    OAUTH_SIGNIN_SUCCEEDED = "oauth_signin_succeeded"
    OAUTH_SIGNIN_FAILED = "oauth_signin_failed"
    OAUTH_LINK_STARTED = "oauth_link_started"
    OAUTH_LINKED = "oauth_linked"
    OAUTH_UNLINKED = "oauth_unlinked"
    OAUTH_LINK_REJECTED = "oauth_link_rejected"

    # API key management
    API_KEY_SET = "api_key_set"
    API_KEY_CLEARED = "api_key_cleared"

    # CSRF
    CSRF_REJECTED = "csrf_rejected"
