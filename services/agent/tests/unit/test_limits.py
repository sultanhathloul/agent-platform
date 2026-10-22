"""Concurrency limits per user, per tenant and platform-wide (the PR #12 review, decisions 1 and 4;
ADR-0262, ADR-0265): refused at once past any of them, released once."""

from app.attachments.limits import UploadLimits
from app.resilience.limits import Caps, ConcurrencyLimiter
from app.resilience.turn import TurnConfig

TYPES = {"txt": {"extensions": ["txt"], "mimes": ["text/plain"]}}


def test_a_users_third_is_refused_and_a_release_frees_one():
    limiter = ConcurrencyLimiter(Caps(per_user=2, platform=20))
    first, second = limiter.try_acquire("t", "a"), limiter.try_acquire("t", "a")
    assert limiter.try_acquire("t", "a") == "user"
    assert limiter.try_acquire("t", "b") not in ("user", "tenant", "platform")
    first.release()
    first.release()  # idempotent: one release only
    assert limiter.in_flight("t", "a") == 1
    assert limiter.try_acquire("t", "a") not in ("user", "tenant", "platform")
    assert second.released is False


def test_a_tenant_share_refuses_that_tenant_only():
    limiter = ConcurrencyLimiter(Caps(per_user=2, per_tenant=10, platform=20))
    for user in range(5):
        for _ in range(2):
            assert limiter.try_acquire("x", f"u{user}") not in ("user", "tenant", "platform")
    assert limiter.try_acquire("x", "u9") == "tenant"
    assert limiter.try_acquire("y", "v0") not in ("user", "tenant", "platform")


def test_by_default_one_tenant_may_use_the_whole_platform():
    limiter = ConcurrencyLimiter(Caps(per_user=2, platform=20))
    for user in range(10):
        for _ in range(2):
            assert limiter.try_acquire("x", f"u{user}") not in ("user", "tenant", "platform")
    assert limiter.try_acquire("y", "v0") == "platform"


def test_a_tenant_override_replaces_the_share():
    limiter = ConcurrencyLimiter(Caps(per_user=5, per_tenant=2, tenant_overrides={"big": 4}, platform=20))
    for _ in range(4):
        assert limiter.try_acquire("big", "u") not in ("user", "tenant", "platform")
    assert limiter.try_acquire("big", "u") == "tenant"
    limiter.try_acquire("small", "u")
    limiter.try_acquire("small", "u")
    assert limiter.try_acquire("small", "u") == "tenant"


def test_the_turn_limits_come_from_config_turn_yaml():
    config = TurnConfig(deadline_s=120, answer_reserve_s=45, evidence_fetch_s=10, gateway_margin_s=15)
    limiter = config.limiter(platform_capacity=20)
    assert limiter.caps == Caps(per_user=2, per_tenant=None, tenant_overrides={}, platform=20)
    shared = TurnConfig(
        deadline_s=120,
        answer_reserve_s=45,
        evidence_fetch_s=10,
        gateway_margin_s=15,
        per_tenant_max=10,
        per_tenant_overrides={"ministry": 15},
    )
    assert shared.limiter(20).caps.tenant_cap("ministry") == 15
    assert shared.limiter(20).caps.tenant_cap("other") == 10


def test_registrations_count_against_the_users_uploads_in_flight():
    limits = UploadLimits(max_file_bytes=1, max_files_per_conversation=1, max_pdf_pages=1, types=TYPES)
    caps = limits.registrations().caps
    assert (caps.per_user, caps.per_tenant, caps.platform) == (2, None, 50)
    tenant = UploadLimits(
        max_file_bytes=1,
        max_files_per_conversation=1,
        max_pdf_pages=1,
        types=TYPES,
        max_concurrent_uploads_per_tenant=6,
        tenant_concurrent_uploads={"big": 12},
    )
    assert tenant.registrations().caps.tenant_cap("big") == 12
    assert tenant.registrations().caps.tenant_cap("other") == 6
