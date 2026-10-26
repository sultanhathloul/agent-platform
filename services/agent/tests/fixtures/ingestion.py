"""The internal backend's ingestion as the ingest service builds it (owner decision R4), on a
test session's services: the agent itself never runs it, so tests that index a file run the
pipeline, or the worker, here."""

from app.rag.setup import Documents, build_documents

_BUILT: dict[int, Documents] = {}


def ingestion(services) -> Documents:
    key = id(services.db)
    if key not in _BUILT:
        _BUILT[key] = build_documents(
            services.settings,
            db=services.db,
            redis=services.redis,
            embeddings=services.embeddings,
            models_config=services.models_config,
            counter=services.assembler.counter,
            attachments=services.attachments.store,
            objects=services.attachments.objects,
            kbs=services.kbs.store if services.kbs is not None else None,
        )
    return _BUILT[key]
