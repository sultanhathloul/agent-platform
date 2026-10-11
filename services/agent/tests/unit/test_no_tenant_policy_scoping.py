"""Owner decision (ADR-0107): tenants are not policy groups. No seed policy, test fixture
or realm entry gives a user a tenant in order to select a policy; role-scoped policies and
the `policy:` realm roles do that."""

import json
import os
from pathlib import Path

import yaml

from app.settings import _default_config_dir

AGENT = Path(__file__).resolve().parents[2]


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
    people = [user for user in realm["users"] if not user.get("serviceAccountClientId")]
    tenants = {user["username"]: (user.get("attributes") or {}).get("tenant_id") for user in people}
    assert tenants == {name: ["default"] for name in ("admin", "analyst", "viewer", "evaluator")}
    # A service account calls the Admin API only; it never takes a turn, so it has no tenant.
    accounts = [user for user in realm["users"] if user.get("serviceAccountClientId")]
    assert all("tenant_id" not in (user.get("attributes") or {}) for user in accounts)


def test_no_realm_group_client_or_mapper_hands_out_another_tenant():
    """A tenant could also come from a group attribute or a hard-coded claim mapper."""
    realm = json.loads(_realm_export().read_text(encoding="utf-8"))
    for group in realm.get("groups", []):
        assert "tenant_id" not in (group.get("attributes") or {}), group["name"]
    for client in realm.get("clients", []):
        for mapper in client.get("protocolMappers", []):
            config = mapper.get("config") or {}
            if config.get("claim.name") == "tenant_id":
                assert mapper["protocolMapper"] == "oidc-usermodel-attribute-mapper", mapper["name"]
                assert config.get("user.attribute") == "tenant_id", mapper["name"]
