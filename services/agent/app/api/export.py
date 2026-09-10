"""Writes the agent's published contracts (README §6.3).

    uv run python -m app.api.export                # events schema + OpenAPI
    uv run python -m app.api.export --events-only  # events schema only

Outputs docs/events.schema.json and docs/openapi/agent.json at the repo root.
"""

import argparse
import json
from pathlib import Path

from app.api.events import EVENT_ADAPTER


def _repo_docs() -> Path:
    """<repo>/docs in a checkout; ./docs anywhere else (e.g. inside an image)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            return parent / "docs"
    return Path("docs")


REPO_DOCS = _repo_docs()


def events_schema() -> dict:
    schema = EVENT_ADAPTER.json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://agent-platform.local/schemas/events.v1.json",
        "title": "AgentEvent",
        "description": "One SSE event streamed by POST /v1/turns (agent) and POST /v1/chat/turns (gateway).",
        **schema,
    }


def openapi_schema() -> dict:
    from app.main import create_app

    return create_app(start_background=False).openapi()


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", type=Path, default=REPO_DOCS)
    parser.add_argument("--events-only", action="store_true")
    args = parser.parse_args()

    _write(args.docs / "events.schema.json", events_schema())
    if not args.events_only:
        _write(args.docs / "openapi" / "agent.json", openapi_schema())


if __name__ == "__main__":
    main()
