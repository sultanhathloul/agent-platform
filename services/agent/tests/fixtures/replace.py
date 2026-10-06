"""A model gateway that lets every call go through Context Assembly and LiteLLM, then
replaces the returned text for chosen roles (CLAUDE.md rule 6, ADR-0075): how a test
makes a model "say" fake PII or a policy violation. A streamed call is replayed to
`on_token` in small chunks, as a model would stream it."""

import dataclasses
from collections.abc import Callable


class ReplacingGateway:
    def __init__(self, inner, replies: dict[str, str | list[str] | Callable[[], str]], *, chunk: int = 7):
        self._inner = inner
        self._replies = {role: list(v) if isinstance(v, list) else v for role, v in replies.items()}
        self._chunk = chunk
        self.calls: list[str] = []

    def _next(self, role: str) -> str | None:
        reply = self._replies.get(role)
        if isinstance(reply, list):
            return reply.pop(0) if len(reply) > 1 else reply[0]
        return reply() if callable(reply) else reply

    async def call(self, decision, package, *, on_token=None, temperature=None):
        self.calls.append(decision.role)
        text = self._next(decision.role)
        if text is None:
            return await self._inner.call(decision, package, on_token=on_token, temperature=temperature)
        result = await self._inner.call(decision, package, on_token=None, temperature=temperature)
        if on_token is not None:
            for start in range(0, len(text), self._chunk):
                on_token(text[start : start + self._chunk])
        return result.model_copy(update={"text": text})


def replacing(services, replies: dict, **kwargs):
    gateway = ReplacingGateway(services.gateway, replies, **kwargs)
    return dataclasses.replace(services, gateway=gateway), gateway
