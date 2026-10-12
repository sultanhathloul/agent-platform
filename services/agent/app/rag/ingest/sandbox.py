"""Parsing in a child process (owner decision R4). A file is untrusted input to parsers written
in C and Python; each one is extracted and chunked in a fresh child process with a CPU-time
limit, an address-space limit and a wall-clock timeout (config/docs.yaml `ingest.sandbox`).
A child stopped by a limit, or one that dies, ends the file as failed with
`extraction_aborted`; the ingest service and every other job go on. The child returns data
only: the chunks, the page count, or a failure reason code; never an exception's message,
which can quote the document (S5).

The caps on extracted text and on chunks (`extraction.max_chars`, `chunking.max_chunks`) are
applied here too, and chunking runs in the child, off any event loop."""

import multiprocessing
import resource
from dataclasses import dataclass

from app.rag.chunk import chunk_segments
from app.rag.config import DocsConfig
from app.rag.extract import ExtractionFailed, extract
from app.rag.types import Chunk, DocumentKind, FailureReason


@dataclass(frozen=True)
class Parsed:
    pages: int | None
    chunks: list[Chunk]


class Aborted(ExtractionFailed):
    def __init__(self) -> None:
        super().__init__("extraction_aborted")


def parse(kind: DocumentKind, data: bytes, config: DocsConfig) -> Parsed:
    """Extraction and chunking with the caps; what the child runs."""
    from app.context.tokens import default_counter

    document = extract(kind, data, config)
    if sum(len(segment.text) for segment in document.segments) > config.extraction.max_chars:
        raise ExtractionFailed("text_too_long", pages=document.pages)
    chunks = chunk_segments(
        document.segments,
        default_counter(),
        max_tokens=config.chunking.max_tokens,
        overlap_tokens=config.chunking.overlap_tokens,
    )
    if len(chunks) > config.chunking.max_chunks:
        raise ExtractionFailed("too_many_chunks", pages=document.pages)
    if not chunks:
        raise ExtractionFailed("empty_document", pages=document.pages)
    return Parsed(pages=document.pages, chunks=chunks)


def outcome(kind: DocumentKind, data: bytes, config: DocsConfig) -> tuple:
    """What the child sends back: data only, a failure as its reason code, a defect as its
    exception's type name, never a message (S5)."""
    try:
        parsed = parse(kind, data, config)
        return ("ok", parsed.pages, [chunk.model_dump() for chunk in parsed.chunks])
    except ExtractionFailed as failure:
        return ("failed", failure.reason, failure.pages)
    except MemoryError:
        return ("failed", "extraction_aborted", None)
    except Exception as exc:  # a parser defect: its type only, never its message
        return ("error", type(exc).__name__, None)


def _child(conn, kind: DocumentKind, data: bytes, config: DocsConfig) -> None:
    limits = config.ingest.sandbox
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds + 1))
    memory = limits.memory_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    message = outcome(kind, data, config)
    try:
        conn.send(message)
    finally:
        conn.close()


class ParserDefect(Exception):
    """The parser raised something other than an extraction failure; carries its type name."""


def run_sandboxed(kind: DocumentKind, data: bytes, config: DocsConfig) -> Parsed:
    """Blocking: call it from a thread. Raises ExtractionFailed (extraction_aborted when the
    child was stopped) or ParserDefect."""
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    child = context.Process(target=_child, args=(send, kind, data, config), daemon=True)
    child.start()
    send.close()
    message = None
    try:
        if receive.poll(config.ingest.sandbox.timeout_seconds):
            message = receive.recv()
    except (EOFError, OSError):
        message = None  # the child died before it answered: a limit stopped it
    finally:
        receive.close()
        child.join(timeout=5)
        if child.is_alive():
            child.kill()
            child.join()
    if message is None:
        raise Aborted()
    status = message[0]
    if status == "ok":
        return Parsed(pages=message[1], chunks=[Chunk.model_validate(chunk) for chunk in message[2]])
    if status == "failed":
        reason: FailureReason = message[1]
        raise ExtractionFailed(reason, pages=message[2])
    raise ParserDefect(message[1])
