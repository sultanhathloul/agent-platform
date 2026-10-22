"""A test-only parser that allocates up to its child's memory limit (timeout budget PR, scope B,
I7; ADR-0252). Only docker-compose.memtest.yml runs it, as the entry module of a document service,
for one smoke step; nothing in the platform imports it:

    python -m tests.fixtures.memtest ingest     # app.rag.ingest.service
    python -m tests.fixtures.memtest rag        # mcp_ref.rag.server (mcp-rag-ref)

The sandbox's extractor is wrapped when this module is imported. Sandbox children start with
multiprocessing's spawn, which imports the parent's main module again in the child, so the children
parse with the wrapper too. A file whose bytes start with MARKER allocates 16 MiB blocks, each one
written so it is resident, until the child's address-space limit (RLIMIT_AS) refuses the next
one; it then holds what it has for HOLD_S, so two such children are at their limit at once, frees
it and fails as the limit made it fail (MemoryError: the file ends extraction_aborted). Any other
file is read by the real extractor."""

import asyncio
import sys
import time

from app.rag.ingest import sandbox

MARKER = b"MEMTEST-ALLOCATE-TO-THE-LIMIT"
BLOCK_BYTES = 16 * 1024 * 1024
HOLD_S = 8.0

_extract = sandbox.extract


def _allocating_extract(kind, data, config):
    if not data.startswith(MARKER):
        return _extract(kind, data, config)
    blocks = []
    try:
        while True:
            blocks.append(bytearray(b"\x01") * BLOCK_BYTES)
    except MemoryError:
        pass
    time.sleep(HOLD_S)
    blocks.clear()
    raise MemoryError


sandbox.extract = _allocating_extract


def main() -> None:
    if sys.argv[1:] == ["ingest"]:
        from app.rag.ingest.service import main as ingest

        asyncio.run(ingest())
    elif sys.argv[1:] == ["rag"]:
        from mcp_ref.rag.server import main as rag

        rag()
    else:
        raise SystemExit("usage: python -m tests.fixtures.memtest ingest|rag")


if __name__ == "__main__":
    main()
