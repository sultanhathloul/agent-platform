"""One deadline per call, nested by construction (CLAUDE.md rule 12c, T3; ADR-0250, ADR-0251).

Every bound on a call is opened with `within(ceiling_s, margin_s)`: an anyio cancel scope that
ends the work inside it at min(now + ceiling_s, the enclosing deadline − margin_s). An inner bound
is therefore never past the one around it, whatever the configured ceilings say. Past its end the
work is cancelled (its remote request aborted by the library that holds it) and `within` raises
DeadlineExceeded, a TimeoutError, which says whether the call's own ceiling or the enclosing
deadline ended it.

The enclosing deadline is anyio.current_effective_deadline() within a task. A task does not inherit
its parent's anyio scopes (asyncio.create_task: LangGraph's nodes, a capability's isolated task;
measured: the child reads inf), so the end of the innermost scope opened here also travels in one
context variable, which a task copies when it is created; the earlier of the two is the enclosing
deadline (ADR-0251). Outside every scope the deadline is infinite and a call takes its own ceiling.

`bounded(ceiling_s, margin_s)` is the same arithmetic without a scope: the bound to hand a library
that takes a timeout of its own (an HTTP client, a semaphore wait). `detached_context()` is the
context for work that belongs to no caller (a status read that outlives its bound, ADR-0245; a
registration that outlives the upload's answer, ADR-0254): a copy of the current one without
the deadline, so that work is bound by its own ceilings only. `horizon(margin_s)` sets an earlier
enclosing deadline for what starts inside it without a scope of its own (ADR-0263)."""

import asyncio
import contextvars
import logging
import math
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import anyio
from opentelemetry import metrics

log = logging.getLogger(__name__)

_END: ContextVar[float] = ContextVar("deadline_end", default=math.inf)
# Set in a detached context: the work belongs to no caller (ADR-0251, ADR-0265).
_DETACHED: ContextVar[bool] = ContextVar("deadline_detached", default=False)


class DeadlineExceeded(TimeoutError):
    """A scope of `within` ended its work. `inherited`: the enclosing deadline, not the call's own
    ceiling, set the end (the caller above ran out of time, not this call)."""

    def __init__(self, inherited: bool):
        super().__init__("deadline exceeded")
        self.inherited = inherited


class Budget:
    """One scope's end, on the event loop's clock, and whether the enclosing deadline set it.
    `extend` moves the end later, in the task that opened the scope: the turn, once its answer's
    last token is sent, keeps the room its post-answer reserve promises (ADR-0263)."""

    def __init__(self, end: float, inherited: bool, scope: anyio.CancelScope | None = None):
        self.end = end
        self.inherited = inherited
        self._scope = scope

    @property
    def remaining(self) -> float:
        return self.end - now()

    @property
    def expired(self) -> bool:
        return now() >= self.end

    def extend(self, end: float) -> None:
        """Moves this scope's end to `end` when later; only in the task that opened it."""
        if end <= self.end:
            return
        self.end = end
        if self._scope is not None:
            self._scope.deadline = end
        _END.set(end)


def now() -> float:
    return anyio.current_time()


def deadline() -> float:
    """The enclosing deadline: the earlier of this task's anyio scopes and the one it inherited."""
    return min(anyio.current_effective_deadline(), _END.get())


def remaining() -> float:
    return deadline() - now()


def bounded(ceiling_s: float, margin_s: float = 0.0) -> float:
    """min(ceiling, what remains of the enclosing deadline − margin); may be ≤ 0: no time left."""
    return min(ceiling_s, remaining() - margin_s)


# How long a cancelled child task is given to end before its parent goes on without it.
DRAIN_GRACE_S = 1.0
# Tasks that ignored their cancellation past DRAIN_GRACE_S, by the node that owned them: counted
# here and on the `agent.tasks_abandoned` metric (the PR #12 review, decision 8, ADR-0268).
ABANDONED: Counter[str] = Counter()
_abandoned_metric = metrics.get_meter("agent").create_counter(
    "agent.tasks_abandoned", description="Tasks that ignored their cancellation past the drain grace"
)


async def drain(task: asyncio.Task, grace_s: float = DRAIN_GRACE_S, *, name: str) -> None:
    """Cancels `task` and waits up to `grace_s` for it to end, shielded from the enclosing
    cancellation, so a child the caller owns has released what it holds before the caller goes on
    (rule 12f). Its outcome is not raised. A task still running past `grace_s` ignored its
    cancellation: it is logged `task_abandoned` with `name`, the node that owned it, counted, and
    left behind; only process isolation could stop it (docs/RESILIENCE.md, residuals)."""
    task.cancel()
    with anyio.CancelScope(shield=True), anyio.move_on_after(grace_s):
        await asyncio.wait([task])
    if not task.done():
        ABANDONED[name] += 1
        _abandoned_metric.add(1, {"node": name})
        log.warning("task_abandoned: a task of %s ignored its cancellation for %.1f s", name, grace_s)


def detached_context() -> contextvars.Context:
    """The current context without any enclosing deadline, for asyncio.create_task(context=...).
    Work started in it is background work (`detached()`): an MCP server gives it at most half of
    its slots and one deadline for the slot and the call (app/mcp/client.py, ADR-0265)."""
    context = contextvars.copy_context()
    context.run(_END.set, math.inf)
    context.run(_DETACHED.set, True)
    return context


def detached() -> bool:
    """Whether this runs as background work, in a context from `detached_context()`."""
    return _DETACHED.get()


@contextmanager
def within(ceiling_s: float, margin_s: float = 0.0) -> Iterator[Budget]:
    """Runs the block until min(now + ceiling_s, enclosing − margin_s); raises DeadlineExceeded
    when that end cancelled it. No time left: the block is cancelled at its first await."""
    start = now()
    enclosing = deadline() - margin_s
    own = start + ceiling_s
    end = min(own, enclosing)
    token = _END.set(end)
    try:
        with anyio.CancelScope(deadline=end) as scope:
            budget = Budget(end=end, inherited=enclosing <= own, scope=scope)
            yield budget
    finally:
        _END.reset(token)
    if scope.cancelled_caught:
        raise DeadlineExceeded(budget.inherited)


@contextmanager
def horizon(margin_s: float) -> Iterator[float]:
    """Sets the enclosing deadline `margin_s` before the current one for the calls and tasks
    started inside the block, without cancelling this task at it: each of them bounds itself
    (`within`) and ends there on its own terms. The guards on the whole answer run so, so a check
    the horizon cuts follows its policy's on_error rather than being cancelled with its node, and
    persistence keeps its `persist_s` (ADR-0263)."""
    end = deadline() - margin_s
    token = _END.set(end)
    try:
        yield end
    finally:
        _END.reset(token)
