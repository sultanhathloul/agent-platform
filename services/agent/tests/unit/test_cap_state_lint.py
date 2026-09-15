"""README §18.4: a capability reads and writes only its own `cap_state` namespace.

run_capabilities hands each capability a copy of its own slice (ADR-0047). This
lint rule makes reaching for anything else a failure in `make test`: capability
code may not name another capability's namespace (`cap.<slug>`) or the AgentState
field `cap_state` as a key; it uses `runtime.context.cap_state` and returns
`CapabilityOutput(cap_state=...)`. Every capability package is checked, the
test-only probe included (ADR-0062)."""

import ast
import re
from pathlib import Path

import pytest
import yaml

AGENT_ROOT = Path(__file__).resolve().parents[2]
CAPABILITY_ROOTS = (AGENT_ROOT / "app" / "capabilities", AGENT_ROOT / "tests" / "fixtures" / "capabilities")
_NAMESPACE = re.compile(r"^cap\.[a-z][a-z0-9_]*$")


def cap_state_violations(source: str, own_namespace: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if node.value == "cap_state":
            found.append(
                f"line {node.lineno}: the key 'cap_state' reaches AgentState; use runtime.context.cap_state"
            )
        elif _NAMESPACE.match(node.value) and node.value != own_namespace:
            found.append(f"line {node.lineno}: {node.value!r} is another capability's namespace")
    return found


def _packages() -> list[tuple[Path, str]]:
    return [
        (manifest.parent, yaml.safe_load(manifest.read_text(encoding="utf-8"))["state_namespace"])
        for root in CAPABILITY_ROOTS
        for manifest in sorted(root.glob("*/manifest.yaml"))
    ]


@pytest.mark.parametrize(
    "source",
    [
        'def run(state, runtime):\n    return state["cap_state"]["cap.sql"]\n',
        'history = runtime.context.cap_state.get("cap.web")\n',
        'getattr(snapshot, "cap_state")\n',
        'OTHER = "cap.kb"\n',
        'update = {"cap.docs": {"planted": True}}\n',
    ],
    ids=["state-subscript", "other-key", "getattr", "constant", "dict-key"],
)
def test_another_namespace_or_the_state_field_is_a_violation(source):
    assert cap_state_violations(source, "cap.general")


@pytest.mark.parametrize(
    "source",
    [
        'CapabilityOutput(findings="", cap_state=dict(runtime.context.cap_state))\n',
        'NAMESPACE = "cap.general"\n',
        '"""Keeps its state apart from cap.sql; see cap_state in the contract."""\n',
    ],
    ids=["runtime-slice", "own-namespace", "prose"],
)
def test_the_own_namespace_and_the_runtime_slice_are_allowed(source):
    assert cap_state_violations(source, "cap.general") == []


def test_every_capability_package_follows_the_rule():
    packages = _packages()
    assert {namespace for _, namespace in packages} >= {"cap.general", "cap.probe"}
    offenders = {
        str(path.relative_to(AGENT_ROOT)): found
        for directory, namespace in packages
        for path in sorted(directory.rglob("*.py"))
        if (found := cap_state_violations(path.read_text(encoding="utf-8"), namespace))
    }
    assert offenders == {}
