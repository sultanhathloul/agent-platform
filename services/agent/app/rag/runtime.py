"""The retrieval library as tool handlers reach it (phase 04). A ToolSpec's handler takes
only its arguments (README §9), and capabilities register their tools before the
services exist, so the core binds the shared retriever here once it is built
(`app.main.build_services`). Capability code calls `retriever()`; it never builds a
store, a client or a connection itself."""

from app.rag.retrieve import HybridRetriever

_retriever: HybridRetriever | None = None


def bind(retriever: HybridRetriever) -> None:
    global _retriever
    _retriever = retriever


def retriever() -> HybridRetriever:
    if _retriever is None:
        raise RuntimeError("the retrieval library is not bound; app.main.build_services binds it")
    return _retriever
