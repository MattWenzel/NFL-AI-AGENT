"""Storage mixins for user accounts and auth-tier tables.

Each mixin owns CRUD for one auth-related table. Composed into
`RuntimeStore` via multiple inheritance in `storage/store.py`.
"""

from backend.persistence.users.email_verification import EmailVerificationMixin
from backend.persistence.users.login_failures import LoginFailuresMixin
from backend.persistence.users.security_events import SecurityEventsMixin
from backend.persistence.users.user_identities import UserIdentitiesMixin
from backend.persistence.users.users import UsersMixin

__all__ = [
    "EmailVerificationMixin",
    "LoginFailuresMixin",
    "SecurityEventsMixin",
    "UserIdentitiesMixin",
    "UsersMixin",
]
