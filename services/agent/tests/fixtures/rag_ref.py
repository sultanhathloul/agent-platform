"""The reference RAG MCP server for the contract suite (phase 05, D2): `mcp_ref.rag` run in the
test's event loop with uvicorn on the loopback interface, over the test database (schema
`rag_ref`, as the app role here; its own role in compose), MinIO and the mock LiteLLM. The
platform reaches it through its MCP client with the shipped config/mcp.yaml entry `rag`, so the
pinned schemas are checked too, and MCPDocumentBackend is the backend under test."""

import asyncio

import pytest
import uvicorn

from app.mcp.client import MCPClient
from app.mcp.config import load_mcp_config, resolve
from app.rag.mcp import MCPDocumentBackend
from tests.fixtures.mcp_server import free_port

TOKEN = "test-rag-ref-token"


@pytest.fixture(scope="session")
async def rag_ref_backend(settings, database_url, litellm):
    from mcp_ref.rag.server import build_app, build_pipeline

    port = free_port()
    pipeline = build_pipeline(settings, database_url)
    app = build_app(pipeline, token=TOKEN, host="127.0.0.1", port=port)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    config = load_mcp_config(settings.config_dir / "mcp.yaml")
    config = config.model_copy(update={"servers": [s for s in config.servers if s.id == "rag"]})
    resolved = resolve(
        config,
        profile="default",
        allow_external=False,
        environ={"MCP_RAG_URL": f"http://127.0.0.1:{port}/mcp", "MCP_RAG_TOKEN": TOKEN},
    )
    client = MCPClient(resolved, hash_key=settings.content_hash_key, client_version="test")
    client.start()
    assert await client.wait_connected(20), client.health()
    assert client.servers["rag"].disabled == {}, client.health()  # the shipped pins match the server
    yield MCPDocumentBackend(client, "rag")
    await client.stop()
    server.should_exit = True
    await asyncio.wait_for(task, 10)
