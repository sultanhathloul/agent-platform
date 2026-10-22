"""Timing probes for the rule 12h tests (CLAUDE.md rule 12h; G7 of phase 05, the timeout budget PR)."""

import asyncio


class LagProbe:
    """The event loop's lag: how late a 10 ms sleep wakes, at worst."""

    def __init__(self):
        self.worst = 0.0
        self._task: asyncio.Task | None = None

    async def _run(self):
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            await asyncio.sleep(0.01)
            self.worst = max(self.worst, loop.time() - started - 0.01)

    def __enter__(self):
        self._task = asyncio.ensure_future(self._run())
        return self

    def __exit__(self, *exc):
        self._task.cancel()
