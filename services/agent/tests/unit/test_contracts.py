"""The committed contracts match the code (README §6.3, ADR-0044). A change to the
agent API or the event types must regenerate docs/ (`make openapi`) and bump the
API version in the same change."""

import json
import os
from pathlib import Path

from app.api.export import events_schema, openapi_schema

STALE = "is stale: run `make openapi` and bump the API version"


def _docs() -> Path:
    """<repo>/docs in a checkout; DOCS_DIR in the test container, where compose mounts it."""
    if os.environ.get("DOCS_DIR"):
        return Path(os.environ["DOCS_DIR"])
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            return parent / "docs"
    raise AssertionError("docs/ not found; set DOCS_DIR")


def _committed(relative: str) -> dict:
    path = _docs() / relative
    assert path.is_file(), f"{path} is missing"
    return json.loads(path.read_text(encoding="utf-8"))


def _plain(doc: dict) -> dict:
    return json.loads(json.dumps(doc, ensure_ascii=False))


def test_events_schema_matches_docs():
    assert _plain(events_schema()) == _committed("events.schema.json"), f"docs/events.schema.json {STALE}"


def test_agent_openapi_matches_docs():
    assert _plain(openapi_schema()) == _committed("openapi/agent.json"), f"docs/openapi/agent.json {STALE}"


def test_invalid_request_is_documented_as_the_400_envelope():
    responses = openapi_schema()["paths"]["/v1/turns"]["post"]["responses"]
    assert "422" not in responses
    assert responses["400"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorEnvelope")
