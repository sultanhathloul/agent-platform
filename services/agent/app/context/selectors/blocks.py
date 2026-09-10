"""Block producers of the phase 01 pipeline. Summary, memory, retrieval and
tool-result steps join in later phases."""

from typing import Any

from app.context.budget import Budget
from app.context.package import ContextPackage
from app.context.selectors.base import Step

_ROLE_BY_TYPE = {"human": "user", "user": "user", "ai": "assistant", "assistant": "assistant"}


def _message_role_and_text(message: Any) -> tuple[str, str] | None:
    """Accepts LangGraph messages (BaseMessage) and plain {role, content} dicts."""
    if isinstance(message, dict):
        kind, content = message.get("role"), message.get("content")
    else:
        kind, content = getattr(message, "type", None), getattr(message, "content", None)
    role = _ROLE_BY_TYPE.get(kind or "")
    if role is None or not isinstance(content, str):
        return None
    return role, content


class SystemInstructions(Step):
    name = "system_instructions"
    block = "system"

    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage:
        req = package.request
        prompt = self.deps.prompts.render(
            req.system_prompt,
            req.locale,
            tools=req.tools,
            has_evidence=any(f.get("findings") for f in req.findings),
            policy_hints=req.policy_hints,
        )
        package.blocks.append(self.make_block("system", "system", prompt.text, prompt=prompt.ref))
        package.prompt_refs.append(prompt.ref)
        return package


class StateDigest(Step):
    name = "state_digest"
    block = "state_digest"

    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage:
        req, state = package.request, package.request.state
        facts = {
            "selected_kbs": state.get("selected_kbs") or [],
            "decisions": state.get("decisions") or [],
            "open_threads": state.get("open_threads") or [],
            "artifacts": [a.get("name") or a.get("id", "") for a in state.get("artifacts") or []],
        }
        if not any(facts.values()):
            # Nothing to report. An empty digest costs tokens and measurably
            # degrades the fast tier's answers.
            return package
        prompt = self.deps.prompts.render("context.state_digest", req.locale, **facts)
        package.blocks.append(self.make_block("state_digest", "system", prompt.text, prompt=prompt.ref))
        package.prompt_refs.append(prompt.ref)
        return package


class SlidingWindow(Step):
    """The last N messages, raw. `state.messages` holds earlier turns only; the
    current user message is added by UserMessage."""

    name = "sliding_window"
    block = "window"

    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage:
        history = package.request.state.get("messages") or []
        for message in history[-self.deps.window_messages :]:
            parsed = _message_role_and_text(message)
            if parsed:
                role, text = parsed
                package.blocks.append(self.make_block("window", role, text))
        return package


class CapabilityFindings(Step):
    name = "capability_findings"
    block = "findings"

    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage:
        req = package.request
        findings = [f for f in req.findings if f.get("findings")]
        if findings:
            prompt = self.deps.prompts.render("context.findings", req.locale, findings=findings)
            package.blocks.append(self.make_block("findings", "system", prompt.text, prompt=prompt.ref))
            package.prompt_refs.append(prompt.ref)
        return package


class UserMessage(Step):
    name = "user_message"
    block = "user_message"

    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage:
        text = package.request.state.get("user_message", "")
        package.blocks.append(self.make_block("user_message", "user", text))
        return package
