"""What a `model.call` span records (README §12, §19.3, §20; ADR-0072): the prompts
the call used, why its tier was chosen, and the model LiteLLM served it with."""

import json
import uuid

import pytest

from tests.fixtures.http import post_turn

pytestmark = pytest.mark.integration


async def test_model_call_records_prompts_tier_reason_and_the_served_model(client, spans, litellm):
    principal = {
        "user_id": f"u-{uuid.uuid4().hex[:8]}",
        "tenant_id": "default",
        "permissions": ["cap:general"],
    }
    events = await post_turn(client, conversation_id=str(uuid.uuid4()), principal=principal)
    assert events[-1]["type"] == "done"

    [answer] = [
        s
        for s in spans.get_finished_spans()
        if s.name == "model.call" and s.attributes.get("model.role") == "reasoning"
    ]
    attributes = answer.attributes
    prompts = json.loads(attributes["langfuse.observation.metadata.prompts"])
    assert any(ref.startswith("answer.system@") for ref in prompts)
    assert attributes["langfuse.observation.metadata.tier_reason"] == next(
        e["data"]["reason"] for e in events if e["type"] == "tier"
    )
    # The mock deployment behind `standard` (infra/litellm/config.mock.yaml), not the alias.
    assert attributes["gen_ai.response.model"] == "openai/mock-standard"
    assert attributes["model.served_known"] is True
