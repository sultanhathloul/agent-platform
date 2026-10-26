"""Skills in real turns (phase 06): permission first (G6), selection (I5, ADR-0299), the `skill`
block (ADR-0300), the `skill` event, state, history and audit (ADR-0301, G8). The test-only skills
of tests/fixtures/skills stand in for the reference ones, so no core or test path depends on a
shipped skill. Model text is replaced after a real LiteLLM call where a test needs the router to
name a skill (ADR-0075)."""

import dataclasses
import json
import uuid

import httpx
import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.identity import Principal
from tests.fixtures.replace import ReplacingGateway

pytestmark = pytest.mark.integration

FORM = "fixture_form"
DOCS_FORM = "fixture_docs_form"
INSTRUCTIONS = "FIXTURE-SKILL-INSTRUCTIONS"
KEYWORD_MESSAGE = "اكتب لي نموذج الاختبار عن الإجازة السنوية"
ROUTER_FORM = json.dumps({"capabilities": ["general"], "skill": FORM, "complexity": "low", "risk": "low"})


class Recording(ReplacingGateway):
    """Replaces chosen roles' text and keeps every call's context package."""

    def __init__(self, inner, replies):
        super().__init__(inner, replies)
        self.packages: list[tuple[str, object]] = []

    async def call(self, decision, package, **kwargs):
        self.packages.append((decision.role, package))
        return await super().call(decision, package, **kwargs)

    def answer_system(self) -> str:
        [package] = [p for role, p in self.packages if role == "reasoning"]
        return package.to_messages()[0]["content"]


def who(*permissions: str) -> Principal:
    return Principal(user_id=f"u-skill-{uuid.uuid4().hex[:8]}", permissions=("cap:general", *permissions))


async def turn(services, principal: Principal, message: str, *, replies=None, skill: str | None = None,
               conversation: str | None = None):
    gateway = Recording(services.gateway, replies or {})
    swapped = dataclasses.replace(services, gateway=gateway)
    request = TurnRequest(conversation_id=conversation or str(uuid.uuid4()), principal=principal,
                          message=message, locale="ar", skill=skill)
    events = [e async for e in TurnRunner(swapped).stream(request, "req-skill")]
    return events, gateway, request.conversation_id


# ------------------------------------------------------------------ permission first (G6)


async def test_a_picker_skill_the_principal_lacks_is_refused_before_any_stream(skill_services, litellm):
    from app.main import create_app

    app = create_app(start_background=False, services=skill_services)
    body = TurnRequest(conversation_id=str(uuid.uuid4()), principal=who(), message="مرحبا", skill=FORM)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as http:
        response = await http.post(
            "/v1/turns",
            content=body.model_dump_json(),
            headers={"X-Internal-Token": skill_services.settings.agent_internal_token,
                     "Content-Type": "application/json"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "skill_forbidden"


async def test_without_the_permission_no_path_loads_the_instructions(skill_services, litellm):
    viewer = who("cap:docs")
    # The request field: refused in the stream path too, nothing assembled.
    events, gateway, _ = await turn(skill_services, viewer, "مرحبا", skill=FORM)
    assert events[-1].type == "error" and events[-1].data.code == "skill_forbidden"
    assert gateway.packages == []
    # The keyword: not a candidate, so no router call and no skill block.
    events, gateway, _ = await turn(skill_services, viewer, KEYWORD_MESSAGE, replies={"router": ROUTER_FORM})
    assert events[-1].type == "done" and events[-1].data.state_digest.skill is None
    assert "router" not in gateway.calls
    assert INSTRUCTIONS not in gateway.answer_system()
    assert not any(e.type == "skill" for e in events)


async def test_a_router_answer_naming_a_skill_the_principal_lacks_is_dropped(skill_services, spans, litellm):
    # The docs-form skill needs cap:docs and its own permission; this principal has the form only,
    # so the candidates are [fixture_form]; a router naming fixture_docs_form is dropped.
    principal = who(f"skill:{FORM}")
    reply = json.dumps({"capabilities": ["general"], "skill": DOCS_FORM})
    events, gateway, _ = await turn(skill_services, principal, KEYWORD_MESSAGE, replies={"router": reply})
    assert events[-1].type == "done" and events[-1].data.state_digest.skill is None
    assert INSTRUCTIONS not in gateway.answer_system()
    route = next(s for s in spans.get_finished_spans() if s.name == "node.route")
    dropped = [e for e in route.events if e.name == "skill_dropped"]
    assert dropped and dropped[0].attributes["skill"] == DOCS_FORM
    assert dropped[0].attributes["reason"] == "not_a_candidate"


# ------------------------------------------------------------------ selection (I5)


async def test_a_keyword_candidate_chosen_by_the_router_shapes_the_answer(skill_services, spans, litellm):
    principal = who(f"skill:{FORM}")
    events, gateway, conversation = await turn(
        skill_services, principal, KEYWORD_MESSAGE, replies={"router": ROUTER_FORM}
    )
    done = events[-1]
    assert done.type == "done" and done.data.state_digest.skill == f"{FORM}@1.0.0"
    assert done.data.state_digest.capabilities == ["general"]
    kinds = [e.type for e in events]
    assert kinds.index("skill") < kinds.index("token")
    skill_event = next(e for e in events if e.type == "skill")
    assert skill_event.data.slug == FORM and skill_event.data.label.ar == "نموذج الاختبار"
    system = gateway.answer_system()
    assert f'<skill id="{FORM}" version="1.0.0">' in system and f"{INSTRUCTIONS} {FORM}" in system
    assert system.index("<skill") > system.index("سياسات الحماية")  # after the precedence line
    # SKILL.md's body and the file it names, never its licence or an unnamed file (ADR-0318).
    assert '<file path="reference/layout.md">' in system and "FIXTURE-SKILL-REFERENCE" in system
    assert "FIXTURE-SKILL-LICENSE" not in system and "FIXTURE-SKILL-UNREFERENCED" not in system
    # Only the selected skill's instructions: never another's.
    assert DOCS_FORM not in system
    # The router saw the candidate in the user role, never in its system message.
    [router_package] = [p for role, p in gateway.packages if role == "router"]
    router_messages = router_package.to_messages()
    assert FORM in router_messages[-1]["content"] and "<candidate_skills>" in router_messages[-1]["content"]
    assert "<candidate_skills>" not in router_messages[0]["content"]
    # G8: id@version on the route, answer and turn spans; the instructions' text only in the model
    # call's own input, which content capture records for every prompt; never in the audit fields.
    finished = spans.get_finished_spans()
    route = next(s for s in finished if s.name == "node.route")
    assert route.attributes["route.skill"] == f"{FORM}@1.0.0"
    assert next(s for s in finished if s.name == "node.answer").attributes["answer.skill"] == f"{FORM}@1.0.0"
    assert next(s for s in finished if s.name == "turn").attributes["turn.skill"] == f"{FORM}@1.0.0"
    for span in finished:
        if span.name == "model.call":
            continue
        for value in span.attributes.values():
            assert INSTRUCTIONS not in str(value), span.name
    assert INSTRUCTIONS not in done.model_dump_json()  # guard_audit and tool_audit included
    # The reply in the history carries the skill (I7).
    state = await skill_services.graph.aget_state({"configurable": {"thread_id": conversation}})
    assert state.values["messages"][-1].additional_kwargs["skill"] == {"slug": FORM, "version": "1.0.0"}


async def test_the_router_may_choose_no_skill(skill_services, litellm):
    reply = json.dumps({"capabilities": ["general"], "skill": None})
    events, gateway, _ = await turn(skill_services, who(f"skill:{FORM}"), KEYWORD_MESSAGE, replies={"router": reply})
    assert "router" in gateway.calls
    assert events[-1].data.state_digest.skill is None and INSTRUCTIONS not in gateway.answer_system()


async def test_an_unusable_router_answer_means_no_skill(skill_services, litellm):
    events, gateway, _ = await turn(skill_services, who(f"skill:{FORM}"), KEYWORD_MESSAGE, replies={"router": "؟"})
    assert events[-1].type == "done" and events[-1].data.state_digest.skill is None


async def test_no_picker_and_no_keyword_adds_no_router_call(skill_services, litellm):
    events, gateway, _ = await turn(skill_services, who(f"skill:{FORM}"), "ما عاصمة المملكة؟")
    assert events[-1].type == "done" and "router" not in gateway.calls
    assert events[-1].data.state_digest.skill is None


async def test_the_picker_applies_its_skill_with_no_router_call(skill_services, litellm):
    events, gateway, _ = await turn(skill_services, who(f"skill:{FORM}"), "ما عاصمة المملكة؟", skill=FORM)
    assert events[-1].type == "done" and events[-1].data.state_digest.skill == f"{FORM}@1.0.0"
    assert "router" not in gateway.calls
    assert f"{INSTRUCTIONS} {FORM}" in gateway.answer_system()


async def test_each_skill_picked_is_the_one_applied_even_under_another_skill_s_keyword(skill_services, spans, litellm):
    """Owner visual check of 2026-10-26: for every skill the services hold (the shipped ones and the
    fixtures) that composes with any capability, the pick reaches the answer as that skill, never
    another: its state, its event, its trace, its block. The message carries another skill's
    keyword, so a keyword candidate cannot take the pick's place."""
    skills = [s for s in skill_services.skills.all() if not s.manifest.requires.capabilities]
    shipped = {p.name for p in skill_services.settings.skills_dir.iterdir() if p.is_dir()}
    assert shipped and shipped <= {s.slug for s in skills}
    principal = who(*(s.manifest.permission for s in skills))
    for picked in skills:
        others = [k for s in skills if s.slug != picked.slug for k in s.manifest.routing_hints.keywords.all()]
        message = f"اكتب {others[0]} للفريق عن الإجازات" if others else "جهز لي شي للفريق عن الإجازات"
        spans.clear()
        events, gateway, _ = await turn(skill_services, principal, message, skill=picked.slug)
        done = events[-1]
        assert done.type == "done" and done.data.state_digest.skill == picked.manifest.ref, picked.slug
        assert [e.data.slug for e in events if e.type == "skill"] == [picked.slug]
        route = next(s for s in spans.get_finished_spans() if s.name == "node.route")
        assert route.attributes["route.skill"] == picked.manifest.ref
        assert route.attributes["route.skill.decided_by"] == "picker"
        system = gateway.answer_system()
        assert f'id="{picked.slug}"' in system
        for other in skills:
            if other.slug != picked.slug:
                assert f'id="{other.slug}"' not in system


async def test_a_skill_whose_required_capability_is_not_selected_is_dropped(skill_services, spans, litellm):
    principal = who("cap:docs", f"skill:{DOCS_FORM}")
    reply = json.dumps({"capabilities": ["general"], "skill": DOCS_FORM})
    events, gateway, _ = await turn(skill_services, principal, "اكتب ملخص الملف عن الإجازات", replies={"router": reply})
    assert events[-1].type == "done" and events[-1].data.state_digest.capabilities == ["general"]
    assert events[-1].data.state_digest.skill is None
    route = next(s for s in spans.get_finished_spans() if s.name == "node.route")
    [dropped] = [e for e in route.events if e.name == "skill_dropped"]
    assert dropped.attributes["reason"] == "required_capability_not_selected"


async def test_the_cache_key_changes_with_the_skill_candidates(skill_services, litellm):
    """A decision cached with a skill candidate never answers the same user's turn without one, as
    after the role was removed (ADR-0299): the candidates are part of the key."""
    user = f"u-skill-{uuid.uuid4().hex[:8]}"
    with_skill = Principal(user_id=user, permissions=("cap:general", "cap:directions", f"skill:{FORM}"))
    without = Principal(user_id=user, permissions=("cap:general", "cap:directions"))
    message = "كم المسافة إلى المطار؟ واكتب نموذج الاختبار"
    first, gateway, _ = await turn(skill_services, with_skill, message, replies={"router": ROUTER_FORM})
    again, gateway2, _ = await turn(skill_services, with_skill, message, replies={"router": ROUTER_FORM})
    assert gateway.calls.count("router") == 1 and gateway2.calls.count("router") == 0  # cached
    assert again[-1].data.state_digest.skill == first[-1].data.state_digest.skill == f"{FORM}@1.0.0"
    third, gateway3, _ = await turn(skill_services, without, message, replies={"router": ROUTER_FORM})
    assert gateway3.calls.count("router") == 1  # another key: no candidate now
    assert third[-1].data.state_digest.skill is None


# ------------------------------------------------------------------ TOOL_LIST (ADR-0298)


async def test_a_tool_list_policy_removes_the_skill_silently(skill_services, role, litellm):
    name, add = role
    hidden = await add(
        checkpoints=["TOOL_LIST"],
        checker="regex",
        config={"patterns": [{"name": "HIDDEN_SKILL", "pattern": f"^skill:{FORM}$"}]},
        action="block",
        message_ar="غير متاح لك.",
        message_en="Not available to you.",
    )
    principal = Principal(user_id=f"u-{uuid.uuid4().hex[:8]}", permissions=("cap:general", f"skill:{FORM}"),
                          policy_roles=(f"policy:{name}",))
    events, gateway, _ = await turn(skill_services, principal, "ما عاصمة المملكة؟", skill=FORM)
    done = events[-1]
    assert done.type == "done" and done.data.state_digest.skill is None
    assert not any(e.type in ("guard", "skill") for e in events)
    assert INSTRUCTIONS not in gateway.answer_system()
    [record] = [g for g in done.data.guard_audit if g.policy_id == hidden.id and g.action == "block"]
    assert record.checkpoint == "TOOL_LIST" and record.detail.startswith(f"skill:{FORM}")
    assert record.message_ar is None


# ------------------------------------------------------------------ GET /v1/skills and history


async def _get(services, path: str, user: str, permissions: str = ""):
    from app.main import create_app

    app = create_app(start_background=False, services=services)
    headers = {"X-Internal-Token": services.settings.agent_internal_token, "X-User-Id": user,
               "X-Tenant-Id": "default", "X-User-Permissions": permissions}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as http:
        return await http.get(path, headers=headers)


async def test_the_skills_list_is_filtered_by_principal(skill_services):
    viewer = await _get(skill_services, "/v1/skills", "viewer", "cap:general,cap:docs")
    assert viewer.status_code == 200 and viewer.json()["skills"] == []
    both = f"cap:general,skill:{FORM},skill:{DOCS_FORM}"
    listed = await _get(skill_services, "/v1/skills", "analyst", both)
    assert [s["slug"] for s in listed.json()["skills"]] == [FORM]  # the docs form needs cap:docs
    with_docs = await _get(skill_services, "/v1/skills", "analyst", both + ",cap:docs")
    entries = {s["slug"]: s for s in with_docs.json()["skills"]}
    assert set(entries) == {FORM, DOCS_FORM}
    assert entries[FORM]["name"] == {"ar": "نموذج الاختبار", "en": "Test form"}
    assert set(entries[FORM]) == {"slug", "name", "description"}  # never the instructions
    refused = await _get(skill_services, "/v1/skills", "x", "admin")
    assert refused.status_code == 400


async def test_the_history_shows_the_reply_s_skill(skill_services, litellm):
    principal = who(f"skill:{FORM}")
    events, _, conversation = await turn(skill_services, principal, "ما عاصمة المملكة؟", skill=FORM)
    assert events[-1].type == "done"
    plain, _, _ = await turn(skill_services, principal, "وما عاصمة مصر؟", conversation=conversation)
    response = await _get(skill_services, f"/v1/conversations/{conversation}/messages", principal.user_id)
    replies = [m for m in response.json()["messages"] if m["role"] == "assistant"]
    assert replies[0]["skill"] == {"slug": FORM, "label": {"ar": "نموذج الاختبار", "en": "Test form"}}
    assert replies[1]["skill"] is None
