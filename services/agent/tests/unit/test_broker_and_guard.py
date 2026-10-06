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


async def _echo(args: EchoArgs) -> ToolOutput:
    return ToolOutput(content=args.text, sources=[{"id": "s1"}])


def _broker(spec) -> ToolBroker:
    registry = ToolRegistry()
    registry.register(spec, capability="demo")
    return ToolBroker(registry, GuardEngine())


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
    assert [(e.checkpoint, e.policy_id, e.action) for e in outcome.events] == [(Checkpoint.INPUT, None, "allow")]


async def test_ok_result_is_normalized_and_events_emitted():
    events = []
    result = await _broker(_spec(_echo)).execute(
        "echo", {"text": "مرحبا"}, principal=ALLOWED, emit=lambda t, d: events.append((t, d))
    )
    assert (result.status, result.content, result.sources) == ("ok", "مرحبا", [{"id": "s1"}])
    assert [t for t, _ in events] == ["tool_start", "tool_end"]
    assert events[1][1]["status"] == "ok" and events[1][1]["call_id"] == events[0][1]["call_id"]


async def test_missing_permission_is_denied_without_running():
    ran = []

    async def handler(args):
        ran.append(args)
        return ToolOutput(content="x")

    result = await _broker(_spec(handler)).execute("echo", {"text": "x"}, principal=Principal(user_id="u"))
    assert result.status == "denied" and ran == []


async def test_invalid_arguments_are_an_error():
    result = await _broker(_spec(_echo)).execute("echo", {"wrong": 1}, principal=ALLOWED)
    assert result.status == "error" and "invalid arguments" in result.error


async def test_timeout_retries_then_errors():
    calls = []

    async def slow(args):
        calls.append(1)
        await asyncio.sleep(1)
        return ToolOutput(content="late")

    result = await _broker(_spec(slow, timeout_s=0.05, max_retries=1)).execute(
        "echo", {"text": "x"}, principal=ALLOWED
    )
    assert result.status == "error" and "timeout" in result.error
    assert len(calls) == 2 and result.retries == 1


async def test_duplicate_tool_registration_fails():
    registry = ToolRegistry()
    registry.register(_spec(_echo), capability="a")
    try:
        registry.register(_spec(_echo), capability="b")
    except ValueError as exc:
        assert "registered twice" in str(exc)
    else:
        raise AssertionError("expected ValueError")
