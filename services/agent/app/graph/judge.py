"""The llm_judge checker's model call (README §10.2), on the one path every model call
takes (CLAUDE.md rule 6): the `judge` role, the `guard_judge` node of
config/context.yaml, Context Assembly, the tier resolver (so MODEL_TIER ceilings
apply) and LiteLLM. Inside a turn the call is accounted to that turn; the admin
test panel runs it in a turn of its own. A call on a memory candidate or a summary
is content-free: no messages or output in its trace and no recorded context
package (D5, ADR-0110)."""

import uuid
from typing import Any

from app.context.package import AssemblyRequest
from app.graph.calls import call_model, prepare_call
from app.graph.deps import Services, TurnContext
from app.identity import Principal


class GraphJudge:
    def __init__(self) -> None:
        self._services: Services | None = None

    def bind(self, services: Services) -> None:
        """Services hold the engine that holds this judge, so they are bound afterwards."""
        self._services = services

    async def identity(self, *, principal: Principal, locale: str) -> str:
        """The judge prompts' id@version in `locale` and the model alias the call resolves to
        for `principal` (ceilings included): part of the verdict cache key (ADR-0110)."""
        services = self._services
        node = services.assembler.node("guard_judge")
        decision = await services.resolver.resolve("judge", principal=principal)
        alias = services.models_config.tiers[decision.final].alias
        prompts = ",".join(services.prompts.ref(p, locale) for p in (node.system_prompt, node.request_prompt))
        return f"{prompts};alias={alias}"

    async def judge(
        self,
        *,
        principal: Principal,
        locale: str,
        topic: str,
        examples: list[dict[str, Any]],
        text: str,
        turn: TurnContext | None = None,
        content_free: bool = False,
    ) -> str:
        ctx = turn or TurnContext(
            services=self._services, principal=principal, request_id="guard-judge", turn_id=uuid.uuid4().hex
        )
        node = ctx.services.assembler.node("guard_judge")
        request = AssemblyRequest(
            node="guard_judge",
            needs=node.needs,
            system_prompt=node.system_prompt,
            user_prompt=node.request_prompt,
            locale=locale,
            state={},
            prompt_vars={"topic": topic, "examples": examples, "text": text},
            content_free=content_free,
        )
        result = await call_model(ctx, await prepare_call(ctx, request, role="judge"))
        return result.text
