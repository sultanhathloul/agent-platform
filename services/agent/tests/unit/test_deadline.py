"""app/resilience/deadline.py (timeout budget PR, I1; ADR-0250, ADR-0251): one deadline per call,
nested by construction, carried into tasks the code creates."""

import asyncio
import math

import pytest

from app.resilience import deadline


async def test_outside_every_scope_a_call_takes_its_own_ceiling():
    assert deadline.remaining() == math.inf
    assert deadline.bounded(20.0) == 20.0


async def test_an_inner_bound_is_never_past_the_enclosing_one():
    with deadline.within(1.0):
        assert deadline.bounded(90.0) == pytest.approx(1.0, abs=0.05)
        assert deadline.bounded(90.0, margin_s=0.4) == pytest.approx(0.6, abs=0.05)
        with deadline.within(90.0) as inner:
            assert inner.inherited
            assert inner.remaining == pytest.approx(1.0, abs=0.05)
        with deadline.within(0.2) as own:
            assert not own.inherited


async def test_a_hung_call_ends_at_its_own_ceiling_and_says_so():
    started = asyncio.get_running_loop().time()
    with pytest.raises(deadline.DeadlineExceeded) as raised, deadline.within(0.3):
        await asyncio.Event().wait()
    assert not raised.value.inherited
    assert isinstance(raised.value, TimeoutError)  # existing `except TimeoutError` handlers still apply
    assert asyncio.get_running_loop().time() - started == pytest.approx(0.3, abs=0.1)


async def test_the_enclosing_deadline_ends_an_inner_call_with_a_longer_ceiling():
    started = asyncio.get_running_loop().time()
    with pytest.raises(deadline.DeadlineExceeded), deadline.within(0.3):
        with pytest.raises(deadline.DeadlineExceeded) as inner, deadline.within(60.0):
            await asyncio.Event().wait()
        assert inner.value.inherited
        raise inner.value
    assert asyncio.get_running_loop().time() - started == pytest.approx(0.3, abs=0.1)


async def test_no_time_left_cancels_at_the_first_await():
    with deadline.within(5.0):
        assert deadline.bounded(20.0, margin_s=10.0) < 0
        with deadline.within(20.0, margin_s=10.0) as budget:
            assert budget.expired and budget.inherited
        with pytest.raises(deadline.DeadlineExceeded), deadline.within(20.0, margin_s=10.0):
            await asyncio.sleep(0)


async def test_a_task_created_inside_a_scope_inherits_its_end():
    """anyio's own scopes do not cross asyncio.create_task; the context variable does."""

    async def child():
        with deadline.within(60.0) as budget:
            await asyncio.Event().wait()
        return budget

    with deadline.within(0.3):
        task = asyncio.create_task(child())
        await asyncio.sleep(0)
    assert deadline.remaining() == math.inf  # the parent's scope is gone; the child keeps its end
    started = asyncio.get_running_loop().time()
    with pytest.raises(deadline.DeadlineExceeded) as raised:
        await task
    assert raised.value.inherited
    assert asyncio.get_running_loop().time() - started < 0.4


async def test_a_task_created_outside_a_scope_is_not_bound_by_it():
    seen = []

    async def child():
        seen.append(deadline.remaining())

    task = asyncio.create_task(child())
    with deadline.within(0.3):
        await task
    assert seen == [math.inf]


async def test_a_scope_extended_in_its_own_task_ends_later():
    """The turn keeps its post-answer reserve once the answer is sent (ADR-0263)."""
    started = asyncio.get_running_loop().time()
    with deadline.within(0.2) as budget:
        budget.extend(deadline.now() + 0.6)
        assert deadline.remaining() == pytest.approx(0.6, abs=0.05)
        await asyncio.sleep(0.4)  # past the first end: not cancelled
    assert asyncio.get_running_loop().time() - started >= 0.4
    with pytest.raises(deadline.DeadlineExceeded), deadline.within(0.2) as budget:
        budget.extend(deadline.now() + 0.1)  # earlier: no change
        await asyncio.sleep(1.0)


async def test_a_horizon_bounds_what_starts_inside_it_without_cancelling_its_own_task():
    """The guards on the whole answer: a check cut at the horizon ends on its own terms (ADR-0263)."""
    results = []

    async def check():
        try:
            with deadline.within(math.inf):
                await asyncio.sleep(5)
        except deadline.DeadlineExceeded as exc:
            results.append(("cut", exc.inherited))

    with deadline.within(1.0):
        with deadline.horizon(0.8):
            assert deadline.remaining() == pytest.approx(0.2, abs=0.05)
            await asyncio.gather(check())
        await asyncio.sleep(0.3)  # the task's own scope is not at the horizon: not cancelled
        assert deadline.remaining() == pytest.approx(0.5, abs=0.1)
    assert results == [("cut", True)]


async def test_a_task_that_ignores_its_cancellation_is_abandoned_counted_and_logged(caplog):
    """Decision 8 of the PR #12 review (ADR-0268): drain gives up after its grace."""

    async def stubborn():
        end = asyncio.get_running_loop().time() + 1.0
        while (left := end - asyncio.get_running_loop().time()) > 0:
            try:
                await asyncio.sleep(left)
            except asyncio.CancelledError:
                continue

    before = deadline.ABANDONED["test:node"]
    task = asyncio.create_task(stubborn())
    await asyncio.sleep(0)
    started = asyncio.get_running_loop().time()
    await deadline.drain(task, 0.2, name="test:node")
    assert asyncio.get_running_loop().time() - started == pytest.approx(0.2, abs=0.1)
    assert deadline.ABANDONED["test:node"] == before + 1
    assert "task_abandoned" in caplog.text and "test:node" in caplog.text
    await asyncio.wait([task], timeout=2)  # it ends on its own


async def test_work_in_a_detached_context_is_background_work():
    seen = []

    async def work():
        seen.append((deadline.detached(), deadline.remaining()))

    with deadline.within(0.5):
        await asyncio.get_running_loop().create_task(work(), context=deadline.detached_context())
        await asyncio.create_task(work())
    assert seen[0] == (True, math.inf)
    assert seen[1][0] is False and seen[1][1] < 1
