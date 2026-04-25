"""Storage mixins for user accounts and auth-tier tables.

Composed into `RuntimeStore` via multiple inheritance:

- UsersMixin              — users + user_api_keys + auth_sessions
- UserIdentitiesMixin     — user_identities (password / google / future)
- LoginFailuresMixin      — login_failures (per-email lockout)
- EmailVerificationMixin  — email_verification tokens
- SecurityEventsMixin     — security_events audit log
"""

from backend.lib.storage.users.email_verification import EmailVerificationMixin
from backend.lib.storage.users.identities import UserIdentitiesMixin
from backend.lib.storage.users.login_failures import LoginFailuresMixin
from backend.lib.storage.users.security_events import SecurityEventsMixin
from backend.lib.storage.users.users import UsersMixin

__all__ = [
    "EmailVerificationMixin",
    "LoginFailuresMixin",
    "SecurityEventsMixin",
    "UserIdentitiesMixin",
    "UsersMixin",
]
