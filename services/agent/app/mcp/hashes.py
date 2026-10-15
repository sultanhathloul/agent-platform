"""`make mcp-hashes` (I5, ADR-0206): lists each configured server's tools and prints the hash of
every input schema, beside the pin config/mcp.yaml holds, for the owner to review into the file.
Nothing is changed: a new or changed hash is a reviewed edit of config/mcp.yaml.

    docker compose exec agent python -m app.mcp.hashes"""

import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.mcp.client import schema_hash
from app.mcp.config import BearerAuth, load_mcp_config
from app.settings import Settings


async def _listed(url: str, token: str) -> dict[str, str]:
    import httpx

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with (
        httpx.AsyncClient(headers=headers, timeout=15) as http,
        streamable_http_client(url, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        return {tool.name: schema_hash(tool.inputSchema) for tool in listed.tools}


async def main() -> int:
    settings = Settings()
    config = load_mcp_config(settings.mcp_config or settings.config_dir / "mcp.yaml")
    status = 0
    for server in config.servers:
        url = os.environ.get(server.url_env, "")
        token = os.environ.get(server.auth.bearer_env, "") if isinstance(server.auth, BearerAuth) else ""
        print(f"# {server.id} ({server.url_env})")
        if not url:
            print("  no URL in the environment")
            status = 1
            continue
        try:
            listed = await _listed(url, token)
        except Exception as exc:  # report and go on to the next server
            print(f"  unreachable ({type(exc).__name__})")
            status = 1
            continue
        pinned = {tool.remote_name: tool for tool in server.tools}
        for name, digest in sorted(listed.items()):
            tool = pinned.get(name)
            if tool is None:
                print(f"  {name}: {digest}  (not allowlisted)")
            elif tool.input_schema_sha256 == digest:
                print(f"  {name}: {digest}  (pinned, matches)")
            else:
                print(f"  {name}: {digest}  (pinned {tool.input_schema_sha256}: MISMATCH)")
                status = 1
        for name in sorted(set(pinned) - set(listed)):
            print(f"  {name}: allowlisted but not listed by the server")
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
