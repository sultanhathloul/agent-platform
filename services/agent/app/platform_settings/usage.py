"""What is in use now, for the settings page's usage section (owner decision 2026-10-22, item 7b,
ADR-0282): counts only, read from the process's own counters. No database, no Redis, no user id or
name, and no work per request beyond reading these counters."""

from typing import Any

from app.resilience import deadline


def usage(services) -> dict[str, Any]:
    total, tenants, top_user = services.turn_limits.snapshot()
    caps = services.turn_limits.caps
    attachments = services.attachments
    return {
        "turns": {
            "in_flight": total,
            "capacity": caps.platform,
            "per_user_max": caps.per_user,
            "top_user_in_flight": top_user,
            "tenants": [
                {"tenant_id": tenant, "in_flight": count, "limit": caps.tenant_cap(tenant)}
                for tenant, count in sorted(tenants.items())
            ],
        },
        "mcp": services.broker.mcp_usage(),
        "registrations_in_background": attachments.registrations_in_background(),
        "tasks_abandoned": sum(deadline.ABANDONED.values()),
    }
