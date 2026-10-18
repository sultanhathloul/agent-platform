"""Only app/mcp and the broker hold an MCP connection (ADR-0230, D3, CLAUDE.md rule 6): no other
module of the agent imports the MCP client or reaches a server's connection, so a backend
adapter's calls go through `broker.transport`, and a capability declaring a transport operation
stops the agent at startup."""

import ast
from pathlib import Path

import pytest
import yaml

from app.capabilities.registry import ManifestError

APP = Path(__file__).resolve().parents[2] / "app"
ALLOWED = {APP / "mcp", APP / "tools" / "broker.py"}
CLIENT_NAMES = {"ServerConnection", "MCPClient"}


def violations(source: str, path: str) -> list[str]:
    """What a module outside app/mcp and the broker may not do: import the MCP client module or
    its connection types, or reach a client's `servers` (and through it a connection's `call`)."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source, path)):
        imported = (isinstance(node, ast.ImportFrom) and node.module == "app.mcp.client") or (
            isinstance(node, ast.Import) and any(a.name == "app.mcp.client" for a in node.names)
        )
        if imported:
            found.append(f"{path}:{node.lineno} imports app.mcp.client")
        elif isinstance(node, ast.Name) and node.id in CLIENT_NAMES:
            found.append(f"{path}:{node.lineno} names {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr == "servers":
            found.append(f"{path}:{node.lineno} reaches .servers")
    return found


def _outside() -> list[Path]:
    return [
        path
        for path in APP.rglob("*.py")
        if not any(path == allowed or allowed in path.parents for allowed in ALLOWED)
    ]


def test_no_module_outside_app_mcp_and_the_broker_reaches_a_connection():
    found = [v for path in _outside() for v in violations(path.read_text(encoding="utf-8"), str(path))]
    assert found == []


def test_a_direct_connection_call_outside_app_mcp_fails_the_lint():
    rogue = (
        "from app.mcp.client import ServerConnection\n"
        "async def search(client, args):\n"
        "    conn: ServerConnection = client.servers['rag']\n"
        "    return await conn.call(conn.entry.tool('search'), args)\n"
    )
    found = violations(rogue, "app/rag/rogue.py")
    assert any("imports app.mcp.client" in v for v in found) and any(".servers" in v for v in found)


def _transport_capability(root: Path) -> Path:
    """A declarative capability whose step calls a transport operation of config/mcp.yaml."""
    directory = root / "leaky"
    (directory / "prompts").mkdir(parents=True)
    manifest = {
        "slug": "leaky",
        "version": "1.0.0",
        "name": {"ar": "تجربة", "en": "Leaky"},
        "description": {"ar": "تجربة", "en": "Leaky"},
        "icon": "flask",
        "enabled_by_default": True,
        "permission": "cap:leaky",
        "ui": {"mode": "auto"},
        "routing_hints": {"description": {"ar": "تجربة", "en": "test"}},
        "model": {"min_tier": "standard"},
        "state_namespace": "cap.leaky",
        "tools": ["rag.search"],
        "declarative": {
            "extract": {
                "prompt": "leaky.extract",
                "schema": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
            "steps": [{"id": "s", "tool": "rag.search", "args": {"query": "$.extract.q"}}],
            "findings": {"prompt": "leaky.findings"},
        },
    }
    (directory / "manifest.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8")
    for name in ("extract", "findings"):
        for locale in ("ar", "en"):
            (directory / "prompts" / f"{name}.{locale}.md.j2").write_text(
                f"---\nid: leaky.{name}\nversion: 1.0.0\nrole: context\nlocale: {locale}\n"
                "changelog: [test]\n---\nx\n",
                encoding="utf-8",
            )
    return root


def test_a_manifest_declaring_a_transport_operation_stops_startup(settings, tmp_path):
    from app.main import build_services

    root = _transport_capability(tmp_path)
    with pytest.raises(ManifestError, match="transport operations"):
        build_services(settings, extra_capabilities=[(root, "leaky_pkg")])
