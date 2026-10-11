"""The principal the gateway sends with every turn (README §6.1).

Identity is set by the gateway, never by the model. The agent only sees
`permissions` (ADR-0006); it never interprets Keycloak roles, except that the realm
roles prefixed `policy:` arrive as `policy_roles` to select role-scoped guard policies
(ADR-0107); they grant nothing. `max_tier` is the
model tier ceiling the gateway derived from the caller's roles (ADR-0046); it is
one of the known tier names, so an unknown value is a 400 (ADR-0049).
"""

import hashlib
import hmac
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.config import TierName

Locale = Literal["ar", "en"]


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1)
    tenant_id: str = Field(default="default", min_length=1)
    permissions: tuple[str, ...] = ()
    max_tier: TierName | None = None
    # Realm roles prefixed `policy:`, the only roles the gateway forwards: they select role-scoped
    # guard policies and grant nothing (ADR-0107).
    policy_roles: tuple[Annotated[str, Field(pattern=r"^policy:[a-z0-9][a-z0-9_-]{0,63}$")], ...] = ()
    display_name: str = ""
    locale: Locale = "ar"

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def can_read_kb(self, slug: str) -> bool:
        return f"kb:{slug}:read" in self.permissions


def hash_user_id(user_id: str, salt: str) -> str:
    """Stable pseudonymous id for traces (README §12: user_id is hashed)."""
    return hmac.new(salt.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:32]
