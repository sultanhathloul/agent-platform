"""Memory value types shared by Context Assembly and the memory store (README §8)."""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.identity import Principal


class MemoryScope(BaseModel):
    """Whose memory a read or write touches. Always the turn's or the request's
    principal, set by core code; retrieval never crosses (tenant_id, user_id)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)

    @classmethod
    def of(cls, principal: Principal) -> "MemoryScope":
        return cls(tenant_id=principal.tenant_id, user_id=principal.user_id)


@dataclass(frozen=True)
class MemoryHit:
    id: str
    kind: str
    text: str
    similarity: float
