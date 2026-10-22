"""Model calls under one deadline each (timeout budget PR, scope E, I4; ADR-0257).

The finding (docs/BACKLOG.md, Medium): model calls ran under a 300 s per-operation timeout inside
capability limits of 30 or 60 s, and LiteLLM retried up to twice that inside the agent's 300 s;
embeddings (300 s) and rerank (60 s) sat inside docs_search's 45 s. Now each call has a wall-clock
deadline from send to the last chunk, min(its ceiling in config/models.yaml `timeouts`, what remains
of the call around it); LiteLLM is told to give up 1 s before it and retries nothing; the agent
retries once, only before the first streamed token.

A slow OpenAI-compatible provider runs in the test container (tests/fixtures/slow_upstream.py)
behind LiteLLM's test-only `slow-*` aliases (infra/litellm/config.mock.yaml) and counts the
requests open at it. Each changed path is tried with a hung, a trickling and a flooding provider
while another user's call runs (rule 12h), asserting that user's time and the event-loop lag.
Timings are printed for docs/PROGRESS.md (rule 11).

One residual is measured, not fixed here (docs/RESILIENCE.md, docs/BACKLOG.md phase 12): LiteLLM
relays a flooding stream on its own event loop, so another user's call through LiteLLM waits while
it does; the agent's own loop does not. The flood test bounds that wait by the flood's own
duration."""

import asyncio
import dataclasses
import random
import time
import uuid

import pytest

from app.context.package import AssemblyRequest
from app.context.tokens import default_counter
from app.graph.calls import prepare_call
from app.graph.deps import TurnContext
from app.identity import Principal
from app.models.embeddings import EmbeddingGateway
from app.models.gateway import LITELLM_MARGIN_S, ModelGateway, ModelUnavailable
from app.rag.rerank import Reranker
from app.resilience import deadline
from app.tools.spec import ToolFailure, ToolOutput, ToolSpec
from tests.fixtures.slow_upstream import FLOOD_CHUNKS, SlowUpstream
from tests.fixtures.timing import LagProbe

pytestmark = pytest.mark.integration

LAG_BOUND_S = 0.1
OTHER_MARGIN_S = 1.0  # another user's call may take this much longer than alone, at most
EDGE_S = 0.5  # how far past its deadline a call may end
SEED = 20261021  # the retry's jitter, drawn from a seeded generator (the PR #12 review, decision 10)


@pytest.fixture(scope="module")
def upstream():
    server = SlowUpstream()
    server.start()
    yield server
    server.stop()


def _config(services, *, alias: str | None = None, reasoning_s: float | None = None, **timeouts):
    config = services.models_config
    tiers = dict(config.tiers)
    if alias is not None:
        tiers["standard"] = tiers["standard"].model_copy(update={"alias": alias})
    bounds = config.timeouts
    if reasoning_s is not None:
        bounds = bounds.model_copy(update={"roles": {**bounds.roles, "reasoning": reasoning_s}})
    if "query_s" in timeouts:
        bounds = bounds.model_copy(update={"embedding": {**bounds.embedding, "query": timeouts["query_s"]}})
    if "ingest_s" in timeouts:
        bounds = bounds.model_copy(update={"embedding": {**bounds.embedding, "ingest": timeouts["ingest_s"]}})
    if "rerank_s" in timeouts:
        bounds = bounds.model_copy(update={"rerank": timeouts["rerank_s"]})
    return config.model_copy(update={"tiers": tiers, "timeouts": bounds})


def _on(services, alias: str, reasoning_s: float, *, rng: random.Random | None = None):
    config = _config(services, alias=alias, reasoning_s=reasoning_s)
    gateway = ModelGateway(services.settings, config, default_counter(), rng=rng)
    return dataclasses.replace(services, models_config=config, gateway=gateway)


async def _call(services, *, on_token=None):
    """One `reasoning` call through Context Assembly and LiteLLM; (result or exception, seconds)."""
    ctx = TurnContext(
        services=services,
        principal=Principal(user_id=f"u-mb-{uuid.uuid4().hex[:6]}"),
        request_id="r",
        turn_id="t",
    )
    request = AssemblyRequest(
        node="answer",
        needs=["system", "user_message"],
        system_prompt="answer.system",
        locale="ar",
        state={"user_message": "ما عاصمة المملكة؟"},
    )
    prepared = await prepare_call(ctx, request, role="reasoning", step="answer")
    started = time.monotonic()
    try:
        outcome = await services.gateway.call(prepared.decision, prepared.package, on_token=on_token)
    except Exception as exc:  # noqa: BLE001 (the test asserts which)
        outcome = exc
    return outcome, time.monotonic() - started


async def _with_another_user(services, slow):
    """Runs `slow` while another user's ordinary call runs; returns (slow's outcome, its seconds,
    the other's seconds, the other alone, the worst loop lag)."""
    _, alone = await _call(services)
    with LagProbe() as probe:
        task = asyncio.create_task(slow)
        await asyncio.sleep(0.3)
        other, other_took = await _call(services)
        outcome, took = await task
    assert not isinstance(other, Exception), other
    return outcome, took, other_took, alone, probe.worst


def _checked(other_took: float, alone: float, lag: float) -> None:
    assert lag < LAG_BOUND_S, lag
    assert other_took < alone + OTHER_MARGIN_S, (other_took, alone, lag)


# ------------------------------------------------------------------ chat calls


@pytest.mark.parametrize("kind", ["hung", "trickle"])
async def test_a_hung_or_trickling_provider_ends_by_the_calls_ceiling(services, litellm, upstream, kind):
    ceiling = 3.0
    slow = _on(services, f"slow-{kind}", reasoning_s=ceiling)
    upstream.reset()
    outcome, took, other_took, alone, lag = await _with_another_user(services, _call(slow))
    assert isinstance(outcome, ModelUnavailable), outcome
    assert ceiling - LITELLM_MARGIN_S - EDGE_S <= took <= ceiling + EDGE_S, took
    _checked(other_took, alone, lag)
    closed = await upstream.settled(kind, within_s=1.5)
    print(f"E model {kind}: ModelUnavailable at {took:.2f} s (ceiling {ceiling:.0f} s); another user's call "
          f"{other_took:.2f} s ({alone:.2f} s alone); loop lag max {lag * 1000:.1f} ms; provider request "
          f"closed {closed:.2f} s after")  # fmt: skip


async def test_a_flooding_provider_never_stalls_the_loop(services, litellm, upstream):
    slow = _on(services, "slow-flood", reasoning_s=90.0)
    tokens: list[str] = []
    outcome, took, other_took, alone, lag = await _with_another_user(
        services, _call(slow, on_token=tokens.append)
    )
    assert not isinstance(outcome, Exception), outcome
    assert len(outcome.text) == FLOOD_CHUNKS == len(tokens)
    assert lag < LAG_BOUND_S, lag  # the agent's loop
    # LiteLLM's loop relays the flood: another user's call through it waits, never past the flood.
    assert other_took < took + OTHER_MARGIN_S, (other_took, took)
    print(f"E model flood: {FLOOD_CHUNKS} one-character chunks in {took:.2f} s; another user's call "
          f"{other_took:.2f} s ({alone:.2f} s alone); loop lag max {lag * 1000:.1f} ms")  # fmt: skip


async def test_the_deadline_around_a_call_ends_it_whatever_its_ceiling(services, litellm, upstream):
    slow = _on(services, "slow-hung", reasoning_s=90.0)
    started = time.monotonic()
    with pytest.raises(deadline.DeadlineExceeded) as ended, deadline.within(2.0):
        outcome, _ = await _call(slow)
        raise outcome
    took = time.monotonic() - started
    assert ended.value.inherited
    assert took <= 2.0 + EDGE_S, took
    print(f"E model inside a 2 s caller: ended at {took:.2f} s with the caller's deadline (ceiling 90 s)")


async def test_the_whole_call_fallback_included_ends_by_its_deadline(services, litellm, upstream):
    """As heavy → standard in config.yaml: `slow-fallback` is hung and falls back to `slow-hung`."""
    ceiling = 3.0
    slow = _on(services, "slow-fallback", reasoning_s=ceiling)
    upstream.reset()
    outcome, took = await _call(slow)
    assert isinstance(outcome, ModelUnavailable), outcome
    assert took <= ceiling + EDGE_S, took
    closed = await upstream.settled("hung", within_s=1.5)
    print(f"E model fallback: ended at {took:.2f} s (ceiling {ceiling:.0f} s), {upstream.arrived['hung']} "
          f"request(s) reached the provider, all closed {closed:.2f} s after")  # fmt: skip


@pytest.mark.parametrize("status", ["429", "500"])
async def test_one_retry_before_the_first_token_only_with_time_left(services, litellm, upstream, status):
    upstream.reset()
    backoff_s = random.Random(SEED).uniform(*services.models_config.timeouts.retry.backoff_s)
    outcome, took = await _call(_on(services, f"slow-{status}", reasoning_s=20.0, rng=random.Random(SEED)))
    assert isinstance(outcome, ModelUnavailable), outcome
    assert upstream.arrived[f"status{status}"] == 2  # the call and one retry
    assert backoff_s <= took <= backoff_s + EDGE_S, (took, backoff_s)  # the seeded backoff, once
    upstream.reset()
    outcome, _ = await _call(_on(services, f"slow-{status}", reasoning_s=8.0))  # under 10 s left: no retry
    assert isinstance(outcome, ModelUnavailable) and upstream.arrived[f"status{status}"] == 1
    print(f"E model {status}: one retry after {took:.2f} s (seeded backoff {backoff_s:.2f} s) "
          "with 20 s left; "
          "none with 8 s left")  # fmt: skip


class _Arrival:
    """Wraps the capped transport's inner transport to note when LiteLLM's answer arrived."""

    def __init__(self, inner):
        self.inner, self.at = inner, None

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        self.at = time.monotonic()
        return response

    async def aclose(self):
        await self.inner.aclose()


@pytest.mark.parametrize("status", ["500", "429"])
async def test_a_2_mib_error_body_is_refused_unread_by_code(services, litellm, upstream, status):
    """Decision 3 (ADR-0266): the SDK reads and parses an error body whole; the agent's transport
    refuses it past 1 MiB before the SDK reads it (its Content-Length says so), and the call fails
    ModelUnavailable, unretried, at once after the answer arrives. Measured residual: LiteLLM itself
    spends about 5 s per MiB turning the provider's error into its own before the agent sees a byte
    (docs/RESILIENCE.md), on its own loop, as with a flood; the agent's loop does not lag."""
    upstream.reset()
    upstream.status_body_bytes = 2 * 1024 * 1024
    slow = _on(services, f"slow-{status}", reasoning_s=20.0)
    capped = slow.gateway._client._client._transport
    arrival = capped._inner = _Arrival(capped._inner)
    ended: list[float] = []

    async def refused():
        result = await _call(slow)
        ended.append(time.monotonic())
        return result

    try:
        outcome, took, other_took, alone, lag = await _with_another_user(services, refused())
    finally:
        upstream.status_body_bytes = 0
    assert isinstance(outcome, ModelUnavailable) and "refused" in str(outcome), outcome
    assert upstream.arrived[f"status{status}"] == 1  # never retried
    refused_after = ended[0] - arrival.at
    assert refused_after < 1.0, refused_after
    assert lag < LAG_BOUND_S, lag  # the agent's loop
    assert other_took < took + OTHER_MARGIN_S, (other_took, took)  # LiteLLM's loop, as with a flood
    print(f"3 model {status} with a 2 MiB body: {outcome}, {refused_after * 1000:.0f} ms after LiteLLM's "
          f"answer arrived ({took:.2f} s in all, LiteLLM's own); another user's call {other_took:.2f} s "
          f"({alone:.2f} s alone); loop lag max {lag * 1000:.1f} ms")  # fmt: skip


async def test_no_retry_after_the_first_token(services, litellm, upstream):
    upstream.reset()
    tokens: list[str] = []
    outcome, _ = await _call(_on(services, "slow-drop", reasoning_s=20.0), on_token=tokens.append)
    assert isinstance(outcome, ModelUnavailable), outcome
    assert tokens and upstream.arrived["drop"] == 1


@pytest.mark.parametrize("kind", ["hung", "trickle"])
async def test_a_cancelled_call_leaves_no_provider_request_past_its_deadline(
    services, litellm, upstream, kind
):
    """The residual (docs/RESILIENCE.md): LiteLLM closes the provider's request at once when the
    agent cancels a stream it is already sending; before the first byte it holds it until the
    request's own timeout, 1 s before the call's deadline, and never longer."""
    ceiling = 6.0
    slow = _on(services, f"slow-{kind}", reasoning_s=ceiling)
    upstream.reset()
    sent = time.monotonic()
    task = asyncio.create_task(_call(slow))
    for _ in range(150):  # bounded polling: until the request is open at the provider
        if upstream.open[kind] == 1:
            break
        await asyncio.sleep(0.02)
    assert upstream.open[kind] == 1
    await asyncio.sleep(max(0.0, 1.0 - (time.monotonic() - sent)))  # cancelled 1 s after send, as before
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    closed = await upstream.settled(kind, within_s=ceiling)
    after_send = time.monotonic() - sent
    if kind == "trickle":
        assert closed < 0.5, closed
    else:
        assert after_send <= ceiling - LITELLM_MARGIN_S + EDGE_S, after_send
    print(
        f"E model cancelled at 1 s, {kind}: provider request closed {after_send:.2f} s after send "
        f"(the call's deadline {ceiling:.0f} s)"
    )


# ------------------------------------------------------------------ embeddings and rerank


@pytest.mark.parametrize("kind", ["hung", "trickle", "flood"])
async def test_a_query_embedding_ends_by_its_ceiling(services, litellm, upstream, kind):
    ceiling = 3.0
    config = _config(services, query_s=ceiling)
    embeddings = EmbeddingGateway(services.settings, config, alias=f"slow-embed-{kind}")
    upstream.reset()

    async def embed():
        started = time.monotonic()
        try:
            outcome = await embeddings.embed("سؤال")
        except Exception as exc:  # noqa: BLE001
            outcome = exc
        return outcome, time.monotonic() - started

    outcome, took, other_took, alone, lag = await _with_another_user(services, embed())
    assert isinstance(outcome, ModelUnavailable), outcome  # a flood is refused at its byte cap, unparsed
    assert took <= ceiling + EDGE_S, took
    if kind == "flood":
        assert lag < LAG_BOUND_S, lag
        assert other_took < took + OTHER_MARGIN_S, (other_took, took)  # LiteLLM's loop, as above
    else:
        _checked(other_took, alone, lag)
    print(f"E embedding {kind}: {type(outcome).__name__} at {took:.2f} s (ceiling {ceiling:.0f} s); another "
          f"user's call {other_took:.2f} s ({alone:.2f} s alone); "
          f"loop lag max {lag * 1000:.1f} ms")  # fmt: skip


@pytest.mark.parametrize("kind", ["hung", "trickle", "flood"])
async def test_an_ingest_batch_embedding_ends_by_its_ceiling(services, litellm, upstream, kind):
    """The ingest path (embed_many, `timeouts.embedding.ingest`, 60 s; here 3 s): a batch of 16
    chunks, as config/docs.yaml embed_batch sends (the PR #12 review, decision 10)."""
    ceiling = 3.0
    embeddings = EmbeddingGateway(
        services.settings, _config(services, ingest_s=ceiling), alias=f"slow-embed-{kind}"
    )
    upstream.reset()

    async def batch():
        started = time.monotonic()
        try:
            outcome = await embeddings.embed_many([f"مقطع {i}" for i in range(16)])
        except Exception as exc:  # noqa: BLE001
            outcome = exc
        return outcome, time.monotonic() - started

    outcome, took, other_took, alone, lag = await _with_another_user(services, batch())
    assert isinstance(outcome, ModelUnavailable), outcome  # a flood is refused at its byte cap, unparsed
    assert took <= ceiling + EDGE_S, took
    if kind == "flood":
        assert lag < LAG_BOUND_S, lag
        assert other_took < took + OTHER_MARGIN_S, (other_took, took)  # LiteLLM's loop, as above
    else:
        _checked(other_took, alone, lag)
    print(f"10 ingest embedding {kind}: {type(outcome).__name__} at {took:.2f} s (ceiling {ceiling:.0f} s); "
          f"another user's call {other_took:.2f} s ({alone:.2f} s alone); "
          f"loop lag max {lag * 1000:.1f} ms")  # fmt: skip


@pytest.mark.parametrize("kind", ["hung", "trickle", "flood"])
async def test_a_rerank_ends_by_its_ceiling(services, litellm, upstream, kind):
    ceiling = 3.0
    reranker = Reranker(services.settings, _config(services, rerank_s=ceiling), alias=f"slow-rerank-{kind}")

    async def rerank():
        started = time.monotonic()
        try:
            outcome = await reranker.rerank("سؤال", ["أ", "ب", "ج"])
        except Exception as exc:  # noqa: BLE001
            outcome = exc
        return outcome, time.monotonic() - started

    outcome, took, other_took, alone, lag = await _with_another_user(services, rerank())
    if kind == "flood":
        # LiteLLM reads the provider's answer and sends the agent its own, scores only; the agent's
        # byte cap stands behind it either way.
        assert outcome == [1.0, 0.5, pytest.approx(1 / 3)]
        assert lag < LAG_BOUND_S, lag
        assert other_took < took + OTHER_MARGIN_S, (other_took, took)  # LiteLLM's loop, as above
    else:
        assert isinstance(outcome, ModelUnavailable), outcome
        assert took <= ceiling + EDGE_S, took
        _checked(other_took, alone, lag)
    print(f"E rerank {kind}: {type(outcome).__name__} at {took:.2f} s (ceiling {ceiling:.0f} s); another "
          f"user's call {other_took:.2f} s ({alone:.2f} s alone); "
          f"loop lag max {lag * 1000:.1f} ms")  # fmt: skip


async def test_a_tools_bound_cuts_the_calls_inside_it(services, litellm, upstream):
    """docs_search's shape: a broker tool whose handler embeds a query. Its 45 s becomes this
    test's 1.5 s; the embedding (ceiling 15 s) inside it is cut to it, and the call ends
    tool_timeout by then."""
    from pydantic import BaseModel

    from app.guardrails.engine import GuardEngine
    from app.tools.broker import ToolBroker
    from app.tools.registry import ToolRegistry

    embeddings = EmbeddingGateway(services.settings, services.models_config, alias="slow-embed-hung")

    class Args(BaseModel):
        query: str

    async def handler(args, ctx):
        await embeddings.embed(args.query)
        return ToolOutput(content="")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="slow_search", description_ar="بحث بطيء", description_en="Slow search", input_schema=Args,
            risk="read", required_permission=None, timeout_s=1.5, max_retries=0, handler=handler,
        ),
        "cap",
    )  # fmt: skip
    broker = ToolBroker(registry, GuardEngine(), {"cap": ["slow_search"]}, {"cap": []})
    started = time.monotonic()
    result = await broker.execute(
        "slow_search", {"query": "سؤال"}, caller="cap", principal=Principal(user_id="u-mb-tool")
    )
    took = time.monotonic() - started
    assert (result.status, result.error) == ("error", "tool_timeout"), result
    assert took <= 1.5 + EDGE_S, took
    assert not isinstance(result, ToolFailure)
    print(f"E broker: a 1.5 s tool with a 15 s embedding inside ended tool_timeout at {took:.2f} s")
