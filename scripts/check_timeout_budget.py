# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6.0"]
# ///
"""The timeout budget, checked (timeout budget PR, I9, ADR-0261; the PR #12 review, decision 9,
ADR-0269; docs/RESILIENCE.md).

Reads the files that set the platform's bounds — config/turn.yaml, config/models.yaml,
config/docs.yaml, config/mcp.yaml, every capability manifest (and docs_search's tool bound),
infra/litellm/config.yaml, the gateway's application.yml, the agent's Dockerfile,
docker-compose.yml and docker-compose.dev.yml, and the agent modules whose margins nest a bound —
and applies the rules of services/agent/app/resilience/budget.py, the one module that holds them
(so a later endpoint that saves one of these files can apply them too). Exits 1 when a rule fails
or a rule the shipped configuration must produce is missing.

    uv run scripts/check_timeout_budget.py             # make test runs it
    uv run scripts/check_timeout_budget.py --self-test # each rule fails on its own violating value"""

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "services/agent"))

from app.resilience import budget  # noqa: E402  (the rules; standard library only)

# The agent modules whose code must use the margins (budget's `*-used` rules).
SOURCES = ("models/gateway.py", "mcp/client.py")


def _duration_s(text: str) -> float:
    """A Spring Boot duration (`60s`, `500ms`, `2m`) in seconds."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m)", str(text).strip())
    if not match:
        raise ValueError(f"not a duration: {text!r}")
    value, unit = float(match[1]), match[2]
    return value / 1000 if unit == "ms" else value * 60 if unit == "m" else value


def _size_mb(text: str) -> float:
    """A Compose memory size (`2g`, `768m`) in MiB."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([gm])", str(text).strip().lower())
    if not match:
        raise ValueError(f"not a memory size: {text!r}")
    return float(match[1]) * (1024 if match[2] == "g" else 1)


def _constant(path: Path, name: str) -> float:
    match = re.search(rf"^{name}\s*=\s*([0-9.]+)", path.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise ValueError(f"{path.relative_to(REPO)}: no {name}")
    return float(match[1])


def _keep_alive(text: str) -> float:
    match = re.search(r'"--timeout-keep-alive",\s*"(\d+)"', text)
    return float(match[1]) if match else 5.0  # uvicorn's own default


def load(repo: Path = REPO) -> dict:
    """Every value the rules read, by the file it comes from."""
    agent = repo / "services/agent"
    config = repo / "config"
    manifests = {
        path.parent.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted((agent / "app/capabilities").glob("*/manifest.yaml"))
    }
    tools = {}
    for path in sorted((agent / "app/capabilities").glob("*/tools.py")):
        for name, bound in re.findall(r'name="([a-z_]+)".*?timeout_s=(\d+(?:\.\d+)?)', path.read_text(), re.DOTALL):
            tools[name] = float(bound)
    gateway = yaml.safe_load((repo / "services/gateway/src/main/resources/application.yml").read_text())
    compose = yaml.safe_load((repo / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    docs = yaml.safe_load((config / "docs.yaml").read_text(encoding="utf-8"))
    return {
        "turn": yaml.safe_load((config / "turn.yaml").read_text(encoding="utf-8")),
        "models": yaml.safe_load((config / "models.yaml").read_text(encoding="utf-8")),
        "docs": docs,
        "mcp": yaml.safe_load((config / "mcp.yaml").read_text(encoding="utf-8")),
        "manifests": manifests,
        "tools": tools,
        "litellm": yaml.safe_load((repo / "infra/litellm/config.yaml").read_text(encoding="utf-8")),
        "gateway": {
            "response_timeout_s": _duration_s(gateway["gateway"]["agent"]["response-timeout"]),
            "keep_alive_s": _duration_s(gateway["gateway"]["agent"]["keep-alive"]),
            "connection_upload_timeout_s": _duration_s(gateway["server"]["tomcat"]["connection-upload-timeout"]),
        },
        "agent_keep_alive_s": _keep_alive((agent / "Dockerfile").read_text(encoding="utf-8")),
        "agent_keep_alive_dev_s": _keep_alive((repo / "docker-compose.dev.yml").read_text(encoding="utf-8")),
        # Sandbox children per document service (ADR-0252): ingest's from config/docs.yaml, the
        # reference RAG server's from its own module.
        "sandbox_workers": {
            "ingest": int(docs["ingest"]["workers"]),
            "mcp-rag-ref": int(_constant(repo / "infra/mcp-ref/mcp_ref/rag/server.py", "WORKERS")),
        },
        "mem_limits_mb": {name: _size_mb(compose[name]["mem_limit"]) for name in ("ingest", "mcp-rag-ref")},
        "constants": {
            "sdk_read_margin_s": _constant(agent / "app/mcp/client.py", "_SDK_READ_MARGIN_S"),
            "attempt_margin_s": _constant(agent / "app/mcp/client.py", "_ATTEMPT_MARGIN_S"),
            "broker_margin_s": _constant(agent / "app/mcp/tools.py", "_BROKER_MARGIN_S"),
            "client_margin_s": _constant(agent / "app/models/gateway.py", "CLIENT_MARGIN_S"),
            "litellm_margin_s": _constant(agent / "app/models/gateway.py", "LITELLM_MARGIN_S"),
        },
        "sources": {name: (agent / "app" / name).read_text(encoding="utf-8") for name in SOURCES},
    }


def report(values: dict) -> int:
    found, missing = budget.verify(values)
    failed = [check for check in found if not check.holds]
    for check in found:
        print(f"  {'ok  ' if check.holds else 'FAIL'}  {check.name} — {check.row}: {check.detail}")
    for name in missing:
        print(f"  FAIL  {name}: expected, but the configuration produced no such rule")
    print(f"timeout budget {len(found) - len(failed)} / {len(failed) + len(missing)} / 0")
    return 1 if failed or missing else 0


def self_test(values: dict) -> int:
    found, missing = budget.verify(values)
    if missing or not all(check.holds for check in found):
        print("  FAIL  self-test: the shipped values must pass first")
        return 1
    problems = budget.self_test(values)
    for problem in problems:
        print(f"  FAIL  self-test: {problem}")
    total = len(budget.mutations(values))
    print(f"timeout budget self-test {total - len(problems)} / {len(problems)} / 0")
    return 1 if problems else 0


if __name__ == "__main__":
    values = load()
    sys.exit(self_test(values) if "--self-test" in sys.argv[1:] else report(values))
