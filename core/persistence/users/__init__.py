"""Storage mixins for user accounts and auth-tier tables.

Each mixin owns CRUD for one auth-related table. Composed into
`RuntimeStore` via multiple inheritance in `storage/store.py`.
"""

from core.persistence.users.email_verification import EmailVerificationMixin
from core.persistence.users.login_failures import LoginFailuresMixin
from core.persistence.users.security_events import SecurityEventsMixin
from core.persistence.users.user_identities import UserIdentitiesMixin
from core.persistence.users.users import UsersMixin

__all__ = [
    "EmailVerificationMixin",
    "LoginFailuresMixin",
    "SecurityEventsMixin",
    "UserIdentitiesMixin",
    "UsersMixin",
]
