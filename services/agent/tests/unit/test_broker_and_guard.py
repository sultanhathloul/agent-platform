import asyncio

from pydantic import BaseModel

from app.guardrails.checkpoints import Checkpoint
from app.guardrails.engine import GuardEngine
from app.identity import Principal
from app.tools.broker import ToolBroker
from app.tools.registry import ToolRegistry
from app.tools.spec import ToolOutput, ToolSpec


class EchoArgs(BaseModel):
    text: str


def _spec(handler, *, name="echo", timeout_s=1.0, max_retries=0, permission="cap:demo"):
    return ToolSpec(
        name=name,
        description_ar="صدى",
        description_en="echo",
        input_schema=EchoArgs,
        risk="read",
        required_permission=permission,
        timeout_s=timeout_s,
        max_retries=max_retries,
        handler=handler,
    )


async def _echo(args: EchoArgs, ctx) -> ToolOutput:
    return ToolOutput(content=args.text, sources=[{"id": "s1"}])


def _broker(spec, collections=None) -> ToolBroker:
    registry = ToolRegistry()
    registry.register(spec, capability="demo")
    return ToolBroker(registry, GuardEngine(), {"demo": [spec.name], "other": []}, collections)


ALLOWED = Principal(user_id="u", permissions=("cap:demo",))


def test_seven_checkpoints():
    assert [c.value for c in Checkpoint] == [
        "INPUT",
        "TOOL_LIST",
        "PRE_TOOL",
        "POST_TOOL",
        "OUTPUT",
        "DATA",
        "MODEL_TIER",
    ]


async def test_engine_allows_unchanged_without_policies():
    outcome = await GuardEngine().evaluate(Checkpoint.INPUT, {"message": "نص"})
    assert outcome.action == "allow" and outcome.payload == {"message": "نص"}
    # The checkpoint ran and is recorded, with no policy behind it (C1).
    assert [(e.checkpoint, e.policy_id, e.action) for e in outcome.events] == [
        (Checkpoint.INPUT, None, "allow")
    ]


async def test_ok_result_is_normalized_and_events_emitted():
    events = []
    result = await _broker(_spec(_echo)).execute(
        "echo", {"text": "مرحبا"}, caller="demo", principal=ALLOWED, emit=lambda t, d: events.append((t, d))
    )
    assert (result.status, result.content, result.sources) == ("ok", "مرحبا", [{"id": "s1"}])
    assert [t for t, _ in events] == ["tool_start", "tool_end"]
    assert events[1][1]["status"] == "ok" and events[1][1]["call_id"] == events[0][1]["call_id"]


async def test_missing_permission_is_denied_without_running():
    ran = []

    async def handler(args, ctx):
        ran.append(args)
        return ToolOutput(content="x")

    result = await _broker(_spec(handler)).execute(
        "echo", {"text": "x"}, caller="demo", principal=Principal(user_id="u")
    )
    assert result.status == "denied" and ran == []


async def test_invalid_arguments_are_an_error():
    result = await _broker(_spec(_echo)).execute("echo", {"wrong": 1}, caller="demo", principal=ALLOWED)
    assert result.status == "error" and "invalid arguments" in result.error


async def test_timeout_retries_then_errors():
    calls = []

    async def slow(args, ctx):
        calls.append(1)
        await asyncio.sleep(1)
        return ToolOutput(content="late")

    result = await _broker(_spec(slow, timeout_s=0.05, max_retries=1)).execute(
        "echo", {"text": "x"}, caller="demo", principal=ALLOWED
    )
    assert result.status == "error" and "timeout" in result.error
    assert len(calls) == 2 and result.retries == 1


async def test_the_scope_comes_from_the_principal_and_the_turn_never_the_arguments():
    """I1: the session collection, plus the knowledge bases both permitted and selected, kept to
    the kinds the caller's manifest declares."""
    seen = []

    async def handler(args, ctx):
        seen.append(ctx.scope)
        return ToolOutput(content="x")

    principal = Principal(
        user_id="u-scope", tenant_id="t-scope", permissions=("cap:demo", "kb:moe:read", "kb:hr:read")
    )
    for kinds, expected in [
        (["session", "kb"], ("sess_c-1", "kb_moe")),
        (["session"], ("sess_c-1",)),
        (["kb"], ("kb_moe",)),
        ([], ()),
    ]:
        seen.clear()
        broker = _broker(_spec(handler), collections={"demo": kinds})
        result = await broker.execute(
            "echo",
            {"text": "x"},
            caller="demo",
            principal=principal,
            conversation_id="c-1",
            selected_kbs=("moe", "sales"),  # sales is selected but not permitted; hr permitted, not selected
        )
        assert result.status == "ok"
        assert [(s.tenant_id, s.user_id, s.collections) for s in seen] == [("t-scope", "u-scope", expected)]


async def test_a_tool_the_callers_manifest_does_not_declare_is_refused_and_audited():
    ran, outcomes, events = [], [], []

    async def handler(args, ctx):
        ran.append(args)
        return ToolOutput(content="x")

    result = await _broker(_spec(handler)).execute(
        "echo",
        {"text": "x"},
        caller="other",
        principal=ALLOWED,
        emit=lambda t, d: events.append(t),
        on_guard=outcomes.append,
    )
    assert (result.status, result.error) == ("denied", "undeclared_tool")
    assert ran == [] and events == []  # nothing ran, nothing streamed
    [record] = [e for o in outcomes for e in o.events]
    assert (record.checkpoint, record.action, record.reason, record.audit_only) == (
        Checkpoint.PRE_TOOL,
        "block",
        "undeclared_tool",
        True,
    )
    assert "other" in record.detail and "echo" in record.detail
    # A tool that is not registered at all is refused the same way, never a KeyError.
    missing = await _broker(_spec(handler)).execute("nowhere", {}, caller="demo", principal=ALLOWED)
    assert (missing.status, missing.error) == ("denied", "undeclared_tool")


async def test_duplicate_tool_registration_fails():
    registry = ToolRegistry()
    registry.register(_spec(_echo), capability="a")
    try:
        registry.register(_spec(_echo), capability="b")
    except ValueError as exc:
        assert "registered twice" in str(exc)
    else:
        raise AssertionError("expected ValueError")
