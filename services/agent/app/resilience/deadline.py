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
the deadline, so that work is bound by its own ceilings only."""

import asyncio
import contextvars
import math
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import anyio

_END: ContextVar[float] = ContextVar("deadline_end", default=math.inf)


class DeadlineExceeded(TimeoutError):
    """A scope of `within` ended its work. `inherited`: the enclosing deadline, not the call's own
    ceiling, set the end (the caller above ran out of time, not this call)."""

    def __init__(self, inherited: bool):
        super().__init__("deadline exceeded")
        self.inherited = inherited


@dataclass(frozen=True)
class Budget:
    """One scope's end, on the event loop's clock, and whether the enclosing deadline set it."""

    end: float
    inherited: bool

    @property
    def remaining(self) -> float:
        return self.end - now()

    @property
    def expired(self) -> bool:
        return now() >= self.end


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


async def drain(task: asyncio.Task, grace_s: float = DRAIN_GRACE_S) -> None:
    """Cancels `task` and waits up to `grace_s` for it to end, shielded from the enclosing
    cancellation, so a child the caller owns has released what it holds before the caller goes on
    (rule 12f). Its outcome is not raised."""
    task.cancel()
    with anyio.CancelScope(shield=True), anyio.move_on_after(grace_s):
        await asyncio.wait([task])


def detached_context() -> contextvars.Context:
    """The current context without any enclosing deadline, for asyncio.create_task(context=...)."""
    context = contextvars.copy_context()
    context.run(_END.set, math.inf)
    return context


@contextmanager
def within(ceiling_s: float, margin_s: float = 0.0) -> Iterator[Budget]:
    """Runs the block until min(now + ceiling_s, enclosing − margin_s); raises DeadlineExceeded
    when that end cancelled it. No time left: the block is cancelled at its first await."""
    start = now()
    enclosing = deadline() - margin_s
    own = start + ceiling_s
    budget = Budget(end=min(own, enclosing), inherited=enclosing <= own)
    token = _END.set(budget.end)
    try:
        with anyio.CancelScope(deadline=budget.end) as scope:
            yield budget
    finally:
        _END.reset(token)
    if scope.cancelled_caught:
        raise DeadlineExceeded(budget.inherited)
