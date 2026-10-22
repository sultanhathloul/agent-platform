"""Platform settings (owner decision 2026-10-22, ADR-0276 to ADR-0282) without a database: the
shared validator's ranges and rules on overrides, the snapshot a request pins, and the plain-words
messages of every rule."""

import copy
import re

import pytest

from app.attachments.limits import load_upload_limits
from app.models.config import load_models_config
from app.platform_settings import effective
from app.platform_settings.effective import Defaults, build
from app.platform_settings.messages import SettingsMessages, family, params
from app.resilience import budget, budget_values
from app.resilience.limits import Caps, ConcurrencyLimiter
from app.resilience.turn import load_turn_config
from app.rag.config import load_docs_config

APP_DIR = budget_values.Path(budget_values.__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def values(config_dir):
    loaded = budget_values.load_values(config_dir, APP_DIR)
    loaded["platform"] = {"lock_pool_size": 20}
    return loaded


@pytest.fixture(scope="module")
def defaults(config_dir, values) -> Defaults:
    return Defaults(
        values=values,
        turn=load_turn_config(config_dir),
        timeouts=load_models_config(config_dir).timeouts,
        upload=load_upload_limits(config_dir),
        register_response_s=load_docs_config(config_dir).register_response_s,
        uploads_per_minute=int(values["docs"]["limits"]["uploads_per_minute"]),
        mcp_max_concurrency={s["id"]: int(s["max_concurrency"]) for s in values["mcp"]["servers"]},
        lock_pool_size=20,
    )


@pytest.fixture(scope="module")
def messages(config_dir) -> SettingsMessages:
    return SettingsMessages.load(config_dir)


def _names(problems) -> list[str]:
    return [p.name for p in problems]


def test_the_files_values_pass_and_every_editable_setting_has_its_default(values):
    _, problems = budget.evaluate(values, {})
    assert problems == []
    found = budget.defaults(values)
    assert found["turn.per_user_max"] == 2
    assert found["turn.per_tenant_max"] is None
    assert found["models.helper_s"] == 20
    assert found["models.reasoning_s"] == 90
    assert found["models.embed_batch_s"] == 60
    servers = [s["id"] for s in values["mcp"]["servers"]]
    assert {f"mcp.{sid}.max_concurrency" for sid in servers} <= set(found)


def test_an_invalid_pair_is_refused_by_name(values):
    _, problems = budget.evaluate(values, {"turn.answer_reserve_s": 130})
    assert "answer-reserve-in-turn" in _names(problems)
    _, problems = budget.evaluate(values, {"models.helper_s": 30})  # the shortest capability: 30 s
    assert "helper-in-shortest-capability" in _names(problems)


def test_a_rule_generated_per_manifest_refuses_a_save(values):
    """deadline 80 − reserve 45 leaves 35 s before the answer: a 60 s capability no longer fits."""
    _, problems = budget.evaluate(values, {"turn.deadline_s": 80})
    assert [n for n in _names(problems) if n.startswith("capability-in-room:")]


def test_an_mcp_max_concurrency_above_the_files_is_refused_and_below_it_is_not(values):
    server = values["mcp"]["servers"][0]
    key = f"mcp.{server['id']}.max_concurrency"
    _, problems = budget.evaluate(values, {key: int(server["max_concurrency"]) + 1})
    assert _names(problems) == [f"range:{key}"]
    assert problems[0].row == "range"
    _, problems = budget.evaluate(values, {key: 1})
    assert problems == []


@pytest.mark.parametrize(
    ("value", "reason"),
    [(0, "range"), (1.5, "integer"), (True, "number"), ("2", "number"), (None, "required"), (21, "range")],
)
def test_a_count_out_of_its_range_is_refused(values, value, reason):
    _, problems = budget.evaluate(values, {"turn.per_user_max": value})
    assert [(p.name, p.row) for p in problems] == [("range:turn.per_user_max", reason)]


def test_null_is_a_value_only_where_it_means_no_limit(values):
    assert budget.evaluate(values, {"turn.per_tenant_max": None})[1] == []
    assert budget.evaluate(values, {"turn.per_tenant_max": 5})[1] == []


def test_an_unknown_key_and_a_bad_tenant_id_are_refused(values):
    _, problems = budget.evaluate(values, {"turn.lock_pool_size": 40})
    assert _names(problems) == ["unknown:turn.lock_pool_size"]
    _, problems = budget.evaluate(values, {"turn.per_tenant_overrides": {"وزارة": 2, "moe": 0}})
    assert sorted((p.name, p.row) for p in problems) == [
        ("range:turn.per_tenant_overrides", "range"),
        ("range:turn.per_tenant_overrides", "tenant"),
    ]
    assert budget.evaluate(values, {"turn.per_tenant_overrides": {"moe": 3}})[1] == []


def test_evaluate_never_changes_the_files_values(values):
    before = copy.deepcopy(values)
    budget.evaluate(values, {"turn.deadline_s": 150, "models.helper_s": 15})
    assert values == before


def test_a_snapshot_writes_the_overrides_over_the_default_a_caller_holds(defaults):
    snapshot = build(defaults, 3, {"turn.per_user_max": 1, "models.helper_s": 15, "docs.register_response_s": 12})
    assert snapshot.turn.per_user_max == 1
    assert snapshot.turn_caps().per_user == 1
    with effective.pin(snapshot):
        assert effective.turn(defaults.turn).per_user_max == 1
        assert effective.turn(defaults.turn).deadline_s == defaults.turn.deadline_s
        roles = effective.timeouts(defaults.timeouts).roles
        assert [roles[r] for r in budget.HELPER_ROLES] == [15, 15, 15, 15]
        assert roles["reasoning"] == defaults.timeouts.roles["reasoning"]
        assert effective.register_response_s(20.0) == 12
        # A caller's own default (a test's short configuration) gets the same overrides.
        own = defaults.turn.model_copy(update={"deadline_s": 60.0})
        assert effective.turn(own).per_user_max == 1
        assert effective.turn(own).deadline_s == 60.0
    assert effective.turn(defaults.turn) is defaults.turn  # nothing pinned: the default itself


def test_a_lowered_limit_applies_to_what_starts_after_it():
    limiter = ConcurrencyLimiter(Caps(per_user=2, platform=20))
    first = limiter.try_acquire("default", "u1")
    limiter.caps = Caps(per_user=1, platform=20)
    assert limiter.try_acquire("default", "u1") == "user"  # the one in flight keeps its slot
    first.release()
    assert not isinstance(limiter.try_acquire("default", "u1"), str)


def test_every_rule_and_range_has_its_words_in_both_languages(values, messages):
    rules = [*budget.EXPECTED, *budget.generated(values)]
    for name in rules:
        template = messages.rules.get(name) or messages.families.get(family(name))
        assert template, f"{name}: no message in config/messages/settings.yaml"
    for setting in budget.editable(values):
        assert messages.field(setting.key)["ar"] != "إعداد", setting.key
    for reason in ("required", "number", "integer", "range", "tenant", "map", "unknown"):
        assert reason in messages.ranges


def test_no_latin_token_inside_an_arabic_sentence(messages):
    for group in (messages.rules, messages.families, messages.ranges, messages.fields):
        for key, text in group.items():
            plain = re.sub(r"\{\w+\}", "", text["ar"])
            assert not re.search(r"[A-Za-z]", plain), f"{key}: {text['ar']}"


def test_a_refusal_names_the_conflict_in_plain_words(values, messages):
    _, problems = budget.evaluate(values, {"turn.answer_reserve_s": 130})
    check = next(p for p in problems if p.name == "answer-reserve-in-turn")
    ar, en = messages.describe(check, lambda kind, ident: (ident, ident))
    assert ar.startswith("مهلة الإجابة (١٣٠ ثانية) أطول من مهلة السؤال كله (١٢٠ ثانية)")
    assert "130 s" in en and "120 s" in en
    _, problems = budget.evaluate(values, {"turn.deadline_s": 80})
    room = next(p for p in problems if p.name.startswith("capability-in-room:"))
    assert params(room.name)["capability"] in values["manifests"]
    ar, _ = messages.describe(room, lambda kind, ident: ("الملفات المرفقة", "Attached files"))
    assert "«الملفات المرفقة»" in ar
    _, problems = budget.evaluate(values, {"turn.per_user_max": 0})
    ar, en = messages.describe(problems[0], lambda kind, ident: ("", ""), minimum=1)
    assert ar == "الأسئلة الجارية للمستخدم الواحد: القيمة ٠ خارج المدى المسموح، من ١ إلى ٢٠."
