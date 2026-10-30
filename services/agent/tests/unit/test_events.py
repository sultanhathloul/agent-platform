import json

import pytest
from pydantic import ValidationError

from app.api.events import build_event, to_sse
from app.api.export import events_schema

EVENT_TYPES = {
    "artifact_start",
    "artifact_delta",
    "artifact_done",
    "token",
    "tier",
    "guard",
    "tool_start",
    "tool_end",
    "citation",
    "progress",
    "skill",
    "done",
    "error",
    "retract",
}


def test_sse_block_format_keeps_arabic_unescaped():
    event = build_event("token", {"text": "مرحبًا"}, request_id="r1", turn_id="t1", seq=3)
    block = to_sse(event)
    assert block.startswith("event: token\nid: 3\ndata: ")
    assert block.endswith("\n\n")
    data = json.loads(block.split("data: ", 1)[1])
    assert data == {"type": "token", "request_id": "r1", "turn_id": "t1", "seq": 3, "data": {"text": "مرحبًا"}}
    assert "مرحبًا" in block


def test_payload_is_validated_against_its_type():
    with pytest.raises(ValidationError):
        build_event("tool_end", {"call_id": "c", "tool": "x"}, request_id="r", turn_id="t", seq=0)


def test_schema_covers_every_event_type():
    schema = events_schema()
    refs = {ref["$ref"].rsplit("/", 1)[1] for ref in schema["oneOf"]}
    assert len(refs) == len(EVENT_TYPES)
    types = {schema["$defs"][name]["properties"]["type"]["const"] for name in refs}
    assert types == EVENT_TYPES
    for name in refs:
        assert "type" in schema["$defs"][name]["required"]
