from dataclasses import dataclass

from app.memory.config import MemoryConfig
from app.memory.retriever import MemoryRetriever
from app.memory.store import MemoryStore
from app.memory.writer import MemoryWriter


@dataclass(frozen=True)
class MemoryService:
    """Long-term memory (README §8): policy, store, retriever and writer."""

    config: MemoryConfig
    store: MemoryStore
    retriever: MemoryRetriever
    writer: MemoryWriter
