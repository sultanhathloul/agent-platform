"""Owner decision (ADR-0107): tenants are not policy groups. No seed policy, test fixture
or realm entry gives a user a tenant in order to select a policy; role-scoped policies and
the `policy:` realm roles do that."""

import json
import os
import re
from pathlib import Path

import yaml

from app.settings import _default_config_dir

AGENT = Path(__file__).resolve().parents[2]
_TENANT_VALUE = re.compile(r"""tenant_id["']?\s*[:=]\s*f?["']([^"']+)["']""")
_TENANT_SCOPE = re.compile(r"""["']tenant:""")


def _config_dir() -> Path:
    return Path(os.environ.get("CONFIG_DIR") or _default_config_dir())


def _realm_export() -> Path:
    for candidate in (os.environ.get("REALM_EXPORT"), "/srv/infra/keycloak/realm-export.json"):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return AGENT.parents[1] / "infra" / "keycloak" / "realm-export.json"


def test_no_seed_policy_is_scoped_to_a_tenant():
    for path in sorted((_config_dir() / "policies").glob("*.yaml")):
        policy = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert not str(policy["scope"]).startswith("tenant:"), f"{path.name} is scoped to a tenant"
    for path in sorted((AGENT / "app" / "capabilities").glob("*/policies/*.yaml")):
        assert not str(yaml.safe_load(path.read_text(encoding="utf-8"))["scope"]).startswith("tenant:"), path


def test_every_realm_user_is_in_the_default_tenant():
    realm = json.loads(_realm_export().read_text(encoding="utf-8"))
    for user in realm["users"]:
        tenants = (user.get("attributes") or {}).get("tenant_id", ["default"])
        assert tenants == ["default"], f"realm user {user['username']} has tenant_id {tenants}"


def test_no_test_fixture_sets_a_tenant_and_no_integration_test_scopes_a_policy_to_one():
    """Integration tests may still use another tenant to prove isolation (memory, ownership);
    none makes a tenant-scoped policy, and the shared fixtures name no tenant but default."""
    for path in [AGENT / "conftest.py", *(AGENT / "tests" / "fixtures").rglob("*.py")]:
        values = {v for v in _TENANT_VALUE.findall(path.read_text(encoding="utf-8")) if v != "default"}
        assert not values, f"{path.relative_to(AGENT)} sets tenant_id to {sorted(values)}"
    for path in [
        *(AGENT / "tests" / "integration").glob("*.py"),
        *(AGENT / "tests" / "fixtures").rglob("*.py"),
    ]:
        assert not _TENANT_SCOPE.search(path.read_text(encoding="utf-8")), (
            f"{path.relative_to(AGENT)} scopes a policy to a tenant"
        )
