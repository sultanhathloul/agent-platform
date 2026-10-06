"""Phase 03 contracts (rule G2): policy schema, scopes and precedence, CheckResult,
the DATA payload target and the checkpoint call context, tested before any checker."""

import pytest
from pydantic import ValidationError

from app.guardrails.checkpoints import Checkpoint
from app.guardrails.schema import (
    ACTION_RANK,
    DATA_TARGETS,
    SCOPE_PRECEDENCE,
    CheckResult,
    DataPayload,
    GuardContext,
    GuardEventRecord,
    Policy,
    Scope,
    ScopeError,
    Span,
)
from app.identity import Principal


def policy(**overrides) -> Policy:
    fields = {
        "id": "pii-test",
        "name": "PII test",
        "scope": "global",
        "checkpoints": ["DATA"],
        "checker": "regex",
        "config": {"patterns": [{"name": "SA_MOBILE", "pattern": r"05\d{8}"}]},
        "action": "redact",
    }
    return Policy.model_validate({**fields, **overrides})


# ------------------------------------------------------------------ scopes


@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [("global", "global", None), ("tenant:moe", "tenant", "moe"), ("kb:moe", "kb", "moe"), ("tool:web", "tool", "web")],
)
def test_scopes_parse_and_print_back(text, kind, value):
    scope = Scope.parse(text)
    assert (scope.kind, scope.value, str(scope)) == (kind, value, text)


def test_precedence_is_narrow_over_wide_with_the_role_slot_reserved():
    assert SCOPE_PRECEDENCE == {"global": 0, "tenant": 1, "role": 2, "kb": 3, "tool": 4}
    ordered = sorted(["global", "tool:web", "tenant:a", "kb:moe"], key=lambda s: Scope.parse(s).precedence)
    assert ordered == ["global", "tenant:a", "kb:moe", "tool:web"]


def test_role_scope_is_rejected_with_a_clear_error():
    with pytest.raises(ScopeError, match="role scopes are reserved"):
        Scope.parse("role:viewer")
    with pytest.raises(ValidationError, match="role scopes are reserved"):
        policy(scope="role:viewer")


@pytest.mark.parametrize("text", ["", "tenant", "tenant:", "user:x", "global:x", "kb:مدرسة", "tool:a b"])
def test_malformed_scopes_are_rejected(text):
    with pytest.raises(ScopeError):
        Scope.parse(text)


def test_context_scopes_name_the_tenant_kbs_and_tools():
    principal = Principal(user_id="u", tenant_id="moe")
    scopes = GuardContext(kbs=("moe",), tools=("web", "web_fetch")).scopes(principal)
    assert scopes == {"global", "tenant:moe", "kb:moe", "tool:web", "tool:web_fetch"}


# ------------------------------------------------------------------ policy


def test_a_valid_policy_has_a_ref_and_defaults():
    p = policy()
    assert p.ref == "pii-test@1" and p.enabled and p.severity == "medium"
    assert p.effective_on_error == "closed" and p.effective_group == "pii-test"
    assert policy(checker="llm_judge", config={"topic_description": "سياسة"}, action="warn").effective_on_error == "open"


def test_a_block_needs_both_messages():
    with pytest.raises(ValidationError, match="message_ar and message_en"):
        policy(action="block", checkpoints=["INPUT"], message_ar="محجوب")
    assert policy(action="block", checkpoints=["INPUT"], message_ar="محجوب", message_en="Blocked").message_ar


@pytest.mark.parametrize(
    ("action", "checkpoints", "error"),
    [
        ("rewrite", ["DATA"], "rewrite exists only at OUTPUT"),
        ("rewrite", ["OUTPUT", "INPUT"], "rewrite exists only at OUTPUT"),
        ("require_approval", ["POST_TOOL"], "require_approval exists only at PRE_TOOL"),
        ("redact", ["PRE_TOOL"], "redact needs a text checkpoint"),
        ("redact", ["TOOL_LIST"], "redact needs a text checkpoint"),
    ],
)
def test_actions_are_valid_only_where_they_can_act(action, checkpoints, error):
    with pytest.raises(ValidationError, match=error):
        policy(action=action, checkpoints=checkpoints)


def test_model_tier_policies_are_ceilings_with_the_tier_ceiling_checker():
    ceiling = policy(
        checkpoints=["MODEL_TIER"], checker="tier_ceiling", config={"max_tier": "standard"}, action="log_only"
    )
    assert ceiling.typed_config().max_tier == "standard"
    with pytest.raises(ValidationError, match="tier_ceiling"):
        policy(checkpoints=["MODEL_TIER"], action="log_only")
    with pytest.raises(ValidationError, match="tier_ceiling"):
        policy(checkpoints=["DATA"], checker="tier_ceiling", config={"max_tier": "fast"}, action="log_only")
    with pytest.raises(ValidationError, match="ceiling"):
        policy(checkpoints=["MODEL_TIER"], checker="tier_ceiling", config={"max_tier": "fast"}, action="block",
               message_ar="x", message_en="x")


def test_config_is_validated_against_its_checker():
    with pytest.raises(ValidationError, match="invalid regular expression"):
        policy(config={"patterns": [{"name": "X", "pattern": "("}]})
    with pytest.raises(ValidationError, match="config for checker presidio"):
        policy(checker="presidio", config={"entities": []})
    with pytest.raises(ValidationError, match="config for checker keyword_list"):
        policy(checker="keyword_list", config={"keywords": ["x"], "unknown": 1})


def test_checkpoints_do_not_repeat_and_ids_are_slugs():
    with pytest.raises(ValidationError, match="must not repeat"):
        policy(checkpoints=["DATA", "DATA"])
    with pytest.raises(ValidationError):
        policy(id="Not A Slug")


def test_data_targets_default_to_all_and_can_be_restricted():
    assert DATA_TARGETS == ("answer", "memory_candidate", "summary")
    every = policy()
    memory_only = policy(config={"patterns": [{"name": "X", "pattern": "x"}], "targets": ["memory_candidate"]})
    assert all(every.guards_target(t) for t in DATA_TARGETS)
    assert memory_only.guards_target("memory_candidate") and not memory_only.guards_target("answer")
    with pytest.raises(ValidationError):
        policy(config={"patterns": [{"name": "X", "pattern": "x"}], "targets": ["elsewhere"]})


def test_content_excludes_identity_and_the_switch():
    content = policy().content()
    assert "id" not in content and "version" not in content and "enabled" not in content
    assert content["checker"] == "regex"


# ------------------------------------------------------------------ results and payloads


def test_check_result_bounds_and_spans():
    result = CheckResult(matched=True, spans=[Span(start=3, end=13, entity="SA_MOBILE")], score=0.9)
    assert result.spans[0].entity == "SA_MOBILE"
    with pytest.raises(ValidationError):
        CheckResult(matched=True, score=1.5)
    with pytest.raises(ValidationError):
        Span(start=-1, end=2, entity="X")


def test_the_data_payload_carries_a_known_target():
    assert DataPayload(text="نص", target="summary").target == "summary"
    with pytest.raises(ValidationError):
        DataPayload(text="نص", target="log")


def test_event_records_name_the_policy_version():
    event = GuardEventRecord(checkpoint=Checkpoint.DATA, policy_id="pii-test", policy_version=3, action="redact")
    assert event.policy_ref == "pii-test@3"
    assert GuardEventRecord(checkpoint=Checkpoint.INPUT, action="allow").policy_ref is None


def test_block_is_the_most_restrictive_action():
    assert max(ACTION_RANK, key=ACTION_RANK.get) == "block"
    assert ACTION_RANK["redact"] > ACTION_RANK["warn"] > ACTION_RANK["log_only"] > ACTION_RANK["allow"]
