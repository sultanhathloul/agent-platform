"""The retrieval library as tool handlers reach it (phase 04). A ToolSpec's handler takes
only its arguments (README §9), and capabilities register their tools before the
services exist, so the core binds the shared retriever here when the services start
and unbinds it when they stop (`app.main.start_services`, `stop_services`). Capability
code calls `retriever()`; it never builds a store, a client or a connection itself."""

from app.rag.retrieve import HybridRetriever

# The services that are started, most recent last; a process runs one, tests may run several.
_bound: list[HybridRetriever] = []


def bind(retriever: HybridRetriever) -> None:
    _bound.append(retriever)


def unbind(retriever: HybridRetriever) -> None:
    """Called when the services that bound it stop, so a stopped pool is never used."""
    if retriever in _bound:
        _bound.remove(retriever)


def retriever() -> HybridRetriever:
    if not _bound:
        raise RuntimeError("the retrieval library is not bound; app.main.start_services binds it")
    return _bound[-1]
