"""MCP tools as broker tools (phase 05, C1; ADR-0205 to ADR-0208). Every allowlisted tool of an
enabled server becomes a ToolSpec named `{server}.{tool}`, registered under the provider owner
`mcp` (no capability's scope), with no permission of its own: the capability that declares it in
its manifest, and holds its own `cap:{slug}`, is what may call it (ADR-0160).

- Arguments are validated by the broker against the pinned input schema the server listed (I5),
  without the injected fields; a call naming an injected field is refused before anything is
  sent (I6). Until the server is connected and the tool verified, there is no schema and the call
  fails `tool_unavailable` before anything is sent.
- Injected fields are filled here from the ToolContext the broker built from the verified
  principal and the turn: `principal.tenant_id`, `principal.user_id`, `scope.collections` (I6).
- The result is a ToolOutput with the structured content as `data` (every string in it checked at
  POST_TOOL) and as JSON text in `content`, or the text parts (I7); never cached.
- A tool of an external server is marked so; the broker refuses it unless an enabled PRE_TOOL
  policy is scoped to it or to its server (I16).
- Transport operations (`class: transport`, ADR-0230) are never broker tools: `call_transport` is
  how `broker.transport` reaches them for a backend adapter, with the same allowlist, pinned
  schema, injected fields, auth, timeouts, byte cap and breaker as a tool call."""

import copy
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, model_validator

from app.mcp.client import UNAVAILABLE, MCPClient, ServerConnection
from app.mcp.config import ToolEntry
from app.tools.spec import PROVIDER_OWNER, ToolContext, ToolFailure, ToolOutput, ToolSpec

# The broker's own bound on a call: the server's timeout plus room for the queue and the network.
_BROKER_MARGIN_S = 5.0


def public_schema(schema: dict[str, Any], injected: set[str]) -> dict[str, Any]:
    """The input schema without the injected fields: what a model or a capability may see (I6)."""
    public = copy.deepcopy(schema)
    properties = public.get("properties")
    if isinstance(properties, dict):
        for name in injected:
            properties.pop(name, None)
    if isinstance(public.get("required"), list):
        public["required"] = [name for name in public["required"] if name not in injected]
    return public


def _args_model(server: ServerConnection, tool: ToolEntry) -> type[BaseModel]:
    injected = set(tool.injected)

    class MCPArguments(BaseModel):
        model_config = ConfigDict(extra="allow")

        @model_validator(mode="before")
        @classmethod
        def _pinned(cls, data: Any) -> Any:
            if not isinstance(data, dict):
                raise ValueError("arguments must be an object")
            named = sorted(injected & set(data))
            if named:
                raise ValueError(f"injected fields may not be named: {named}")
            schema = server.input_schema(tool.name)
            if schema is not None:
                errors = sum(
                    1 for _ in Draft202012Validator(public_schema(schema, injected)).iter_errors(data)
                )
                if errors:
                    raise ValueError(f"{errors} schema error(s)")
            return data

        @classmethod
        def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
            schema = server.input_schema(tool.name)
            return public_schema(schema, injected) if schema is not None else {}

    MCPArguments.__name__ = f"{server.entry.id}_{tool.name}_arguments"
    return MCPArguments


INVALID_ARGUMENTS = "invalid_arguments"
NOT_TRANSPORT = "not_a_transport_operation"


def _injected(tool: ToolEntry, scope: Any) -> dict[str, Any]:
    """The injected fields from a scope the broker built (ToolScope or DocumentScope) (I6)."""
    values = {
        "principal.tenant_id": scope.tenant_id,
        "principal.user_id": scope.user_id,
        "scope.collections": list(scope.collections),
    }
    return {field: values[source] for field, source in tool.injected.items()}


def _handler(server: ServerConnection, tool: ToolEntry):
    async def handle(args: BaseModel, ctx: ToolContext) -> ToolOutput:
        if server.input_schema(tool.name) is None:
            raise ToolFailure(UNAVAILABLE, {"server": server.entry.id})
        arguments = {**args.model_dump(), **_injected(tool, ctx.scope)}
        result = await server.call(tool, arguments)
        observed: dict[str, str | int | bool] = {"server": server.entry.id, **result.observed}
        if result.dropped:
            observed["dropped"] = ",".join(result.dropped)
        if result.is_error:
            raise ToolFailure("tool_error", observed)
        return ToolOutput(content=result.content, data=result.data, observed=observed)

    return handle


def mcp_tool_specs(client: MCPClient) -> list[ToolSpec]:
    """A ToolSpec per allowlisted tool (`class: tool`) of every server; transport operations are
    never broker tools (ADR-0230)."""
    specs = []
    for server in client.servers.values():
        for tool in server.entry.tools_of("tool"):
            specs.append(
                ToolSpec(
                    name=f"{server.entry.id}.{tool.name}",
                    description_ar=tool.label.ar,
                    description_en=tool.label.en,
                    input_schema=_args_model(server, tool),
                    risk="read",
                    required_permission=None,
                    timeout_s=server.entry.timeout_s + _BROKER_MARGIN_S,
                    max_retries=0,
                    handler=_handler(server, tool),
                    label=tool.label,
                    external=not server.entry.internal,
                )
            )
    return specs


def register_mcp_tools(registry, client: MCPClient) -> list[str]:
    """Registers the MCP tools under the provider owner; returns their names."""
    names = []
    for spec in mcp_tool_specs(client):
        registry.register(spec, PROVIDER_OWNER)
        names.append(spec.name)
    return names


def transport_operations(client: MCPClient) -> set[str]:
    """`{server}.{tool}` of every transport operation: a manifest declaring one fails startup."""
    return {
        f"{server.entry.id}.{tool.name}"
        for server in client.servers.values()
        for tool in server.entry.tools_of("transport")
    }


def transport_servers(client: MCPClient) -> set[str]:
    """The servers with at least one transport operation, which a backend adapter may name."""
    return {s.entry.id for s in client.servers.values() if s.entry.tools_of("transport")}


async def call_transport(
    client: MCPClient,
    server_id: str,
    tool_name: str,
    args: dict[str, Any],
    scope: Any,
    observed: dict[str, str | int | bool],
) -> dict[str, Any]:
    """One transport operation for a backend adapter, through `broker.transport` only
    (ADR-0230): an allowlisted `class: transport` entry, its arguments checked against the pinned
    schema without the injected fields (a call naming one is refused before anything is sent),
    the injected fields filled from `scope`, then the server's call with its auth, timeout, byte
    cap and breaker. Returns the structured result; raises ToolFailure with a content-free code."""
    server = client.servers.get(server_id)
    if server is None:
        raise ToolFailure(UNAVAILABLE)
    observed["server"] = server_id
    tool = next((t for t in server.entry.tools_of("transport") if t.name == tool_name), None)
    if tool is None:
        raise ToolFailure(NOT_TRANSPORT)
    schema = server.input_schema(tool.name)
    if schema is None:
        raise ToolFailure(UNAVAILABLE)
    if set(tool.injected) & set(args):
        raise ToolFailure(INVALID_ARGUMENTS)
    if any(Draft202012Validator(public_schema(schema, set(tool.injected))).iter_errors(args)):
        raise ToolFailure(INVALID_ARGUMENTS)
    try:
        result = await server.call(tool, {**args, **_injected(tool, scope)})
    except ToolFailure as failure:
        observed.update(failure.observed)
        raise
    observed.update(result.observed)
    if result.is_error or not isinstance(result.data, dict):
        raise ToolFailure("tool_error")
    return result.data
