"""Shared tool registry. Capabilities contribute ToolSpecs at startup
(manifest `tools()`); no capability code lives here."""

from app.identity import Principal
from app.tools.spec import ToolSpec


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, str]] = {}

    def register(self, spec: ToolSpec, capability: str) -> None:
        if spec.name in self._tools:
            raise ValueError(
                f"tool {spec.name!r} registered twice ({self._tools[spec.name][1]}, {capability})"
            )
        self._tools[spec.name] = (spec, capability)

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> ToolSpec:
        return self._tools[name][0]

    def capability_of(self, name: str) -> str:
        return self._tools[name][1]

    def for_principal(self, principal: Principal) -> list[ToolSpec]:
        return [spec for spec, _ in self._tools.values() if principal.has(spec.required_permission)]
