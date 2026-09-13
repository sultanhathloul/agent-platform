"""The capability import rule (README §18.4, ADR-0052): ruff's banned-api check,
configured in app/capabilities/ruff.toml, refuses the LangGraph APIs that reach
the main graph's runtime. These tests prove each ban fires under
app/capabilities/ and nowhere else, and lint app/capabilities/ itself, so
`make test` fails on a violation."""

import subprocess
import sys
from pathlib import Path

import pytest

AGENT_ROOT = Path(__file__).resolve().parents[2]

BANNED = [
    "from langgraph.config import get_config",
    "from langgraph.config import get_stream_writer",
    "from langgraph.config import get_store",
    "from langgraph.checkpoint.memory import InMemorySaver",
    "from langgraph.checkpoint.base import BaseCheckpointSaver",
    "import langgraph.checkpoint.memory",
    "import langgraph.config\n\nlanggraph.config.get_config()",
    # Control-flow escapes the core's firewall converts to capability_failed (ADR-0053).
    "from langgraph.errors import ParentCommand",
    "from langgraph.errors import GraphInterrupt",
    "import langgraph.errors",
    "from langgraph.types import Command",
    "from langgraph.types import interrupt",
    "import langgraph.pregel",
    "from langgraph.pregel.main import Pregel",
]


def _ruff(*args: str, source: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "--select",
            "TID251",
            "--output-format",
            "concise",
            *args,
        ],
        input=source,
        capture_output=True,
        text=True,
        cwd=AGENT_ROOT,
        check=False,
    )


@pytest.mark.parametrize("source", BANNED)
def test_capability_code_cannot_import_the_graph_runtime(source):
    result = _ruff("--stdin-filename", "app/capabilities/example/graph.py", "-", source=f"{source}\n")
    assert result.returncode == 1 and "TID251" in result.stdout, result.stdout + result.stderr


def test_the_rule_applies_to_capabilities_only():
    source = "from langgraph.checkpoint.memory import InMemorySaver\n\nprint(InMemorySaver)\n"
    result = _ruff("--stdin-filename", "app/graph/example.py", "-", source=source)
    assert result.returncode == 0, result.stdout + result.stderr


def test_capability_code_follows_the_rule():
    result = _ruff("app/capabilities")
    assert result.returncode == 0, result.stdout + result.stderr
