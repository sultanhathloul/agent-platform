"""config/mcp.yaml: the MCP servers the platform may call and the tools allowed from each (D4,
ADR-0198, ADR-0204). Validated at startup; a file that breaks a rule stops the agent with the
rule it broke.

- Transport is Streamable HTTP only (I2): `stdio` is refused, the agent spawns no process.
- Read-class tools only (D6): a model-callable tool (`class: tool`, the default) with `risk:
  write` is refused until approvals exist (phase 11), and no delete tool is ever allowed: a tool
  whose name or remote name holds delete, remove, drop or purge is refused.
- Transport operations (`class: transport`, ADR-0230) are a backend adapter's calls to its
  server: never broker tools, never declared by a capability, callable only through
  `broker.transport`. One may write on its own server (`register` starts indexing there) and is
  marked so; it still never deletes.
- Injected fields (I6) are filled by the broker from the principal and the scope; their sources
  are a closed list.
- The URL and any token come from the environment (`url_env`, `bearer_env`): this file holds no
  address and no secret.

Which servers are enabled is decided per environment (D5, I16, ADR-0199, ADR-0217): a server is
enabled when the platform's profile is one of its `profiles`; an external server (`internal:
false`) also needs `MCP_ALLOW_EXTERNAL=true` and is never enabled in the airgap profile, whatever
.env says. Capability-agnostic: no server or capability is named in code."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.tools.spec import Label

AIRGAP_PROFILE = "airgap"
InjectedSource = Literal["principal.tenant_id", "principal.user_id", "scope.collections"]

_SLUG = r"^[a-z][a-z0-9_]{0,62}$"
_REMOTE = r"^[A-Za-z0-9_.-]{1,128}$"
_ENV = r"^[A-Z][A-Z0-9_]{0,127}$"
_DELETING = re.compile(r"delete|remove|drop|purge", re.IGNORECASE)


class MCPConfigError(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BearerAuth(_Strict):
    bearer_env: str = Field(pattern=_ENV, description="The environment variable holding the token.")


OperationClass = Literal["tool", "transport"]


class ToolEntry(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(pattern=_SLUG, description="The tool's name in the platform: {server}.{name}.")
    remote_name: str = Field(pattern=_REMOTE, description="The tool's name on the server.")
    # `tool`: a broker tool a capability may declare (D6: read only). `transport`: an operation a
    # backend adapter calls through broker.transport only, never a broker tool (ADR-0230).
    class_: OperationClass = Field(default="tool", alias="class")
    risk: Literal["read", "write"]
    label: Label
    input_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    injected: dict[str, InjectedSource] = {}

    @model_validator(mode="after")
    def _read_only_and_never_deleting(self) -> "ToolEntry":
        if self.risk == "write" and self.class_ == "tool":
            raise ValueError(
                f"tool {self.name!r} is risk: write; write-class MCP tools are refused until approvals "
                "exist (D6, ADR-0230)"
            )
        if _DELETING.search(self.name) or _DELETING.search(self.remote_name):
            raise ValueError(f"tool {self.name!r} deletes; no delete tool is ever allowed (D6, ADR-0166)")
        for field_name in self.injected:
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$", field_name):
                raise ValueError(f"tool {self.name!r}: invalid injected field name {field_name!r}")
        return self


class ServerEntry(_Strict):
    id: str = Field(pattern=_SLUG, description="Logical and stable: the {server} of every tool name.")
    transport: Literal["streamable_http", "stdio"] = "streamable_http"
    url_env: str = Field(pattern=_ENV)
    auth: Literal["none"] | BearerAuth = "none"
    internal: bool
    profiles: list[str] = Field(min_length=1)
    timeout_s: float = Field(default=10.0, gt=0, le=120)
    max_result_bytes: int = Field(default=262144, gt=0, le=16 * 1024 * 1024)
    max_concurrency: int = Field(default=8, ge=1, le=256)
    tools: list[ToolEntry] = Field(min_length=1)

    @field_validator("transport")
    @classmethod
    def _http_only(cls, value: str) -> str:
        if value != "streamable_http":
            raise ValueError("only Streamable HTTP is allowed; the agent spawns no server process (I2)")
        return value

    @model_validator(mode="after")
    def _unique_tools(self) -> "ServerEntry":
        names = [t.name for t in self.tools]
        if len(names) != len(set(names)):
            raise ValueError(f"server {self.id!r} lists a tool twice")
        remotes = [t.remote_name for t in self.tools]
        if len(remotes) != len(set(remotes)):
            raise ValueError(f"server {self.id!r} allows a remote tool twice")
        return self

    def tool(self, name: str) -> ToolEntry:
        return next(t for t in self.tools if t.name == name)

    def tools_of(self, operation_class: OperationClass) -> list[ToolEntry]:
        return [t for t in self.tools if t.class_ == operation_class]


class MCPConfig(_Strict):
    servers: list[ServerEntry] = []

    @model_validator(mode="after")
    def _unique_servers(self) -> "MCPConfig":
        ids = [s.id for s in self.servers]
        if len(ids) != len(set(ids)):
            raise ValueError("a server id is listed twice")
        return self


def load_mcp_config(path: Path) -> MCPConfig:
    """The allowlist at `path`; no file means no server."""
    if not path.is_file():
        return MCPConfig()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return MCPConfig.model_validate(raw)
    except (ValidationError, yaml.YAMLError) as exc:
        raise MCPConfigError(f"{path}: invalid MCP allowlist\n{exc}") from exc


@dataclass(frozen=True)
class Resolved:
    """A server as this environment runs it: enabled with its URL and token, or disabled and why."""

    entry: ServerEntry
    enabled: bool
    reason: str = ""  # why disabled: profile, external_not_allowed, external_in_airgap, no_url
    url: str = ""
    token: str = ""


def resolve(
    config: MCPConfig, *, profile: str, allow_external: bool, environ: dict[str, str]
) -> list[Resolved]:
    """Each server enabled or not in this environment (D5, I16). A disabled server registers no
    tool; the reason is content-free and shown in /v1/health."""
    resolved: list[Resolved] = []
    for entry in config.servers:
        reason = ""
        if profile not in entry.profiles:
            reason = "profile"
        elif not entry.internal and profile == AIRGAP_PROFILE:
            reason = "external_in_airgap"
        elif not entry.internal and not allow_external:
            reason = "external_not_allowed"
        url = environ.get(entry.url_env, "").strip()
        if not reason and not url:
            reason = "no_url"
        token = ""
        if not reason and isinstance(entry.auth, BearerAuth):
            token = environ.get(entry.auth.bearer_env, "").strip()
            if not token:
                reason = "no_token"
        resolved.append(Resolved(entry=entry, enabled=not reason, reason=reason, url=url, token=token))
    return resolved
