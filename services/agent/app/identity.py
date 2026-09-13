"""The principal the gateway sends with every turn (README §6.1).

Identity is set by the gateway, never by the model. The agent only sees
`permissions` (ADR-0006); it never interprets Keycloak roles. `max_tier` is the
model tier ceiling the gateway derived from the caller's roles (ADR-0046).
"""

import hashlib
import hmac
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Locale = Literal["ar", "en"]


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1)
    tenant_id: str = Field(default="default", min_length=1)
    permissions: tuple[str, ...] = ()
    max_tier: str | None = None
    display_name: str = ""
    locale: Locale = "ar"

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def can_read_kb(self, slug: str) -> bool:
        return f"kb:{slug}:read" in self.permissions


def hash_user_id(user_id: str, salt: str) -> str:
    """Stable pseudonymous id for traces (README §12: user_id is hashed)."""
    return hmac.new(salt.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:32]
