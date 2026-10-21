"""Concurrency limits at three levels: per user, per tenant and platform-wide (CLAUDE.md rule 12a;
the PR #12 review, decisions 1 and 4; ADR-0262, ADR-0265).

A limiter counts what is in flight and refuses at once past any of its limits: there is no queue
and no wait (load shedding). `try_acquire` returns a Slot, released once, however many times
`release` is called, or the level that refused (`user`, `tenant` or `platform`).

The counters live in this process: one agent replica in v1. docs/BACKLOG.md phase 12 moves them to
Redis with the other limits before a second replica runs."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

Refusal = Literal["user", "tenant", "platform"]


@dataclass(frozen=True)
class Caps:
    """`per_user`: each user's limit. `per_tenant`: each tenant's share, None for no tenant limit of
    its own (the platform's alone applies); `tenant_overrides` set one tenant's share by its id.
    `platform`: the whole process, None for none."""

    per_user: int
    per_tenant: int | None = None
    tenant_overrides: Mapping[str, int] = field(default_factory=dict)
    platform: int | None = None

    def tenant_cap(self, tenant_id: str) -> int | None:
        return self.tenant_overrides.get(tenant_id, self.per_tenant)


class Slot:
    """One unit in flight; `release` is idempotent."""

    def __init__(self, limiter: "ConcurrencyLimiter", tenant_id: str, user_id: str):
        self._limiter = limiter
        self._key = (tenant_id, user_id)
        self._released = False

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._limiter._release(*self._key)


class ConcurrencyLimiter:
    def __init__(self, caps: Caps):
        self.caps = caps
        self._users: Counter[tuple[str, str]] = Counter()
        self._tenants: Counter[str] = Counter()
        self._total = 0

    def try_acquire(self, tenant_id: str, user_id: str) -> Slot | Refusal:
        caps = self.caps
        if self._users[(tenant_id, user_id)] >= caps.per_user:
            return "user"
        tenant_cap = caps.tenant_cap(tenant_id)
        if tenant_cap is not None and self._tenants[tenant_id] >= tenant_cap:
            return "tenant"
        if caps.platform is not None and self._total >= caps.platform:
            return "platform"
        self._users[(tenant_id, user_id)] += 1
        self._tenants[tenant_id] += 1
        self._total += 1
        return Slot(self, tenant_id, user_id)

    def in_flight(self, tenant_id: str | None = None, user_id: str | None = None) -> int:
        """What is in flight for a user, a tenant or the whole process (tests, diagnostics)."""
        if user_id is not None and tenant_id is not None:
            return self._users[(tenant_id, user_id)]
        if tenant_id is not None:
            return self._tenants[tenant_id]
        return self._total

    def _release(self, tenant_id: str, user_id: str) -> None:
        self._users[(tenant_id, user_id)] -= 1
        if self._users[(tenant_id, user_id)] <= 0:
            del self._users[(tenant_id, user_id)]
        self._tenants[tenant_id] -= 1
        if self._tenants[tenant_id] <= 0:
            del self._tenants[tenant_id]
        self._total -= 1
