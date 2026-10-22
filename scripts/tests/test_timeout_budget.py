"""The timeout budget's rules name no capability (review round 2 of PR #12, finding (3), ADR-0275):
a capability added at test time gets its rules from what it declares, with no edit to
services/agent/app/resilience/budget.py, and removing one from the registry fails nothing.

    make test-budget   # runs these after the script's own checks"""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_timeout_budget as script  # noqa: E402

from app.resilience import budget  # noqa: E402  (on the path the script set)

SHIPPED = script.load()
FIXTURE = "fixture-cap"
FIXTURE_TOOL = "fixture_lookup"


def _with_fixture(values: dict, *, tool_s: float = 40.0) -> dict:
    """The shipped values plus a test-only capability: an in-process tool with its own bound and an
    MCP tool of a shipped server, the document facade declared."""
    changed = copy.deepcopy(values)
    server = changed["mcp"]["servers"][0]["id"]
    changed["manifests"][FIXTURE] = {
        "tools": [FIXTURE_TOOL, f"{server}.anything"],
        "documents": {"collections": ["session"]},
        "limits": {"timeout_s": 70},
    }
    changed["tools"][FIXTURE_TOOL] = tool_s
    return changed


def _by_name(values: dict) -> dict[str, budget.Check]:
    return {check.name: check for check in budget.checks(values)}


def test_expected_and_mutations_name_no_capability_tool_or_server():
    """`v["docs"]` in budget.py is config/docs.yaml, the platform's document settings, not the
    capability: the names checked are those of the rules and of the tools."""
    declared = [*SHIPPED["manifests"], *SHIPPED["tools"], *(s["id"] for s in SHIPPED["mcp"]["servers"])]
    fixed = [*budget.EXPECTED, *budget.MUTATIONS]
    assert [(name, d) for name in fixed for d in declared if d in name.split(":")] == []
    source = Path(budget.__file__).read_text(encoding="utf-8")
    assert [tool for tool in SHIPPED["tools"] if tool in source] == []


def test_a_capability_added_at_test_time_gets_its_rules():
    values = _with_fixture(SHIPPED)
    server = values["mcp"]["servers"][0]["id"]
    found, missing = budget.verify(values)
    assert missing == [], missing
    names = {check.name for check in found}
    for rule in (
        f"capability-in-room:{FIXTURE}",
        f"tool-in-capability:{FIXTURE}:{FIXTURE_TOOL}",
        f"tool-in-capability:{FIXTURE}:{server}.anything",
        f"facade-search-in-tool:{FIXTURE}:{FIXTURE_TOOL}",
        f"document-server-in-tool:{FIXTURE}:{FIXTURE_TOOL}",
    ):
        assert rule in names, rule
    assert all(check.holds for check in found), [c for c in found if not c.holds]
    # Each of its rules has its mutation, and each mutation fails its own rule.
    assert budget.self_test(values) == []
    assert f"tool-in-capability:{FIXTURE}:{FIXTURE_TOOL}" in budget.mutations(values)


def test_the_added_capabilitys_tool_past_its_limit_fails_its_own_rule():
    checks = _by_name(_with_fixture(SHIPPED, tool_s=70.0))
    assert not checks[f"tool-in-capability:{FIXTURE}:{FIXTURE_TOOL}"].holds


def test_a_declared_tool_with_no_bound_fails_rather_than_drops_out():
    values = _with_fixture(SHIPPED)
    del values["tools"][FIXTURE_TOOL]
    found, missing = budget.verify(values)
    assert missing == []
    failed = [check.name for check in found if not check.holds]
    assert f"tool-in-capability:{FIXTURE}:{FIXTURE_TOOL}" in failed, failed


@pytest.mark.parametrize("slug", sorted(SHIPPED["manifests"]))
def test_removing_a_capability_from_the_registry_fails_nothing(slug):
    values = copy.deepcopy(SHIPPED)
    del values["manifests"][slug]
    found, missing = budget.verify(values)
    assert missing == [], missing
    assert all(check.holds for check in found), [c for c in found if not c.holds]
    assert not any(slug in check.name.split(":")[1:2] for check in found)
    assert budget.self_test(values) == []


def test_removing_all_capabilities_but_one_fails_nothing():
    values = copy.deepcopy(SHIPPED)
    for slug in list(values["manifests"])[1:]:
        del values["manifests"][slug]
    found, missing = budget.verify(values)
    assert missing == [] and all(check.holds for check in found)
    assert budget.self_test(values) == []


def test_deploy_values_match_their_files_and_a_change_is_caught(tmp_path):
    """config/deploy-values.yaml, which the agent reads for the settings page's checks (ADR-0277),
    is what the files it copies say; a change to one of them is reported as drift."""
    assert script.deploy_drift() is None
    repo = tmp_path / "repo"
    for rel in (
        "config/deploy-values.yaml",
        "services/gateway/src/main/resources/application.yml",
        "docker-compose.yml",
        "docker-compose.dev.yml",
        "infra/litellm/config.yaml",
        "infra/mcp-ref/mcp_ref/rag/server.py",
        "services/agent/Dockerfile",
    ):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text((script.REPO / rel).read_text(encoding="utf-8"), encoding="utf-8")
    assert script.deploy_drift(repo) is None
    litellm = repo / "infra/litellm/config.yaml"
    litellm.write_text(litellm.read_text().replace("request_timeout: 85", "request_timeout: 80"))
    assert "differs" in script.deploy_drift(repo)
    (repo / "config/deploy-values.yaml").unlink()
    assert "missing" in script.deploy_drift(repo)


def test_the_agent_and_the_script_build_the_same_values():
    """The agent builds its values from config/deploy-values.yaml, the script from the files
    themselves: the same dict, so a save is checked exactly as make test checks the files."""
    from app.resilience import budget_values

    agent = budget_values.load_values(script.REPO / "config", script.REPO / "services/agent/app")
    assert agent == script.load()
