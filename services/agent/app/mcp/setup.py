"""Builds the MCP client from config/mcp.yaml and the environment (D4, D5, I16)."""

import os

from app.mcp.client import MCPClient
from app.mcp.config import load_mcp_config, resolve
from app.settings import Settings


def build_mcp_client(settings: Settings, version: str) -> MCPClient:
    path = settings.mcp_config or settings.config_dir / "mcp.yaml"
    config = load_mcp_config(path)
    resolved = resolve(
        config,
        profile=settings.mcp_profile,
        allow_external=settings.mcp_allow_external,
        environ=dict(os.environ),
    )
    return MCPClient(resolved, hash_key=settings.content_hash_key, client_version=version)
