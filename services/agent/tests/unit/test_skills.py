"""The skill contract and registry (phase 06, G2, G6): SKILL.md validation, the folder layout,
no executable file, permissions and availability, keyword candidacy."""

import os
from pathlib import Path

import pytest
import yaml

from app.identity import Principal
from app.skills.contract import SkillManifest
from app.skills.registry import SkillError, SkillRegistry, load_skill

VALID = {
    "slug": "demo_form",
    "version": "1.0.0",
    "name": {"ar": "نموذج تجريبي", "en": "Demo form"},
    "description": {"ar": "يرتب الإجابة في نموذج", "en": "Lays the answer out as a form"},
    "when_to_use": {"ar": "عندما يطلب المستخدم نموذجًا", "en": "When the user asks for a form"},
    "permission": "skill:demo_form",
    "requires": {"capabilities": []},
    "routing_hints": {"keywords": {"ar": ["نموذج", "مذكرة"], "en": ["form"]}, "rules": [{"when": "keyword_candidate"}]},
    "max_instruction_tokens": 400,
}
PROMPT = "---\nid: {slug}.instructions\nversion: 1.0.0\nrole: skill\nlocale: {locale}\n---\nاكتب النموذج.\n"


def _skill(root: Path, slug: str = "demo_form", body: str = "Notes for reviewers.", **overrides) -> Path:
    directory = root / slug
    (directory / "prompts").mkdir(parents=True)
    (directory / "evals").mkdir()
    front = {**VALID, "slug": slug, "permission": f"skill:{slug}", **overrides}
    (directory / "SKILL.md").write_text(
        f"---\n{yaml.safe_dump(front, allow_unicode=True)}---\n{body}\n", encoding="utf-8"
    )
    for locale in ("ar", "en"):
        (directory / "prompts" / f"instructions.{locale}.md.j2").write_text(
            PROMPT.format(slug=slug, locale=locale), encoding="utf-8"
        )
    (directory / "evals" / f"{slug}.jsonl").write_text("", encoding="utf-8")
    (directory / "evals" / "metrics.yaml").write_text("metrics: {}\n", encoding="utf-8")
    return directory


def test_a_valid_skill_loads(tmp_path):
    skill = load_skill(_skill(tmp_path))
    assert skill.slug == "demo_form" and skill.manifest.ref == "demo_form@1.0.0"
    assert skill.manifest.instructions_prompt == "demo_form.instructions"
    assert skill.manifest.policy_name == "skill:demo_form"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"permission": "cap:demo_form"}, "skill:demo_form"),
        ({"requires": {"capabilities": [], "mcp_tools": ["routing.route"]}}, "mcp_tools"),  # ADR-0307
        ({"requires": {"capabilities": ["docs", "docs"]}}, "twice"),
        ({"routing_hints": {"rules": [{"when": "keyword"}]}}, "keyword_candidate"),
        ({"routing_hints": {"rules": [{"when": "keyword_candidate"}]}}, "needs keywords"),
        ({"max_instruction_tokens": 0}, "max_instruction_tokens"),
        ({"version": "1.0"}, "version"),
        ({"script": "run.sh"}, "script"),
        ({"name": {"ar": "نموذج"}}, "name.en"),
    ],
)
def test_an_invalid_skill_md_fails_fast_naming_the_file(tmp_path, overrides, message):
    directory = _skill(tmp_path, **overrides)
    with pytest.raises(SkillError, match=message) as info:
        load_skill(directory)
    assert str(directory / "SKILL.md") in str(info.value)


def test_the_slug_must_match_its_folder(tmp_path):
    directory = _skill(tmp_path)
    directory.rename(tmp_path / "other_name")
    (tmp_path / "other_name" / "evals" / "demo_form.jsonl").rename(tmp_path / "other_name" / "evals" / "other_name.jsonl")
    with pytest.raises(SkillError, match="must match directory"):
        load_skill(tmp_path / "other_name")


def test_skill_md_without_front_matter_fails(tmp_path):
    directory = _skill(tmp_path)
    (directory / "SKILL.md").write_text("# just notes\n", encoding="utf-8")
    with pytest.raises(SkillError, match="front-matter"):
        load_skill(directory)


@pytest.mark.parametrize(
    "extra",
    ["run.sh", "helper.py", "prompts/build.py", "prompts/nested/instructions.ar.md.j2", "evals/other.jsonl",
     "evals/fixtures.json", ".hidden", "notes/readme.md"],
)
def test_any_other_file_fails_startup_with_its_path(tmp_path, extra):
    directory = _skill(tmp_path)
    target = directory / extra
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", encoding="utf-8")
    with pytest.raises(SkillError, match="not allowed") as info:
        load_skill(directory)
    assert extra.split("/")[0] in str(info.value)


@pytest.mark.parametrize("name", ["SKILL.md", "prompts/instructions.ar.md.j2", "evals/metrics.yaml"])
def test_an_executable_bit_fails_startup(tmp_path, name):
    directory = _skill(tmp_path)
    os.chmod(directory / name, 0o755)
    with pytest.raises(SkillError, match="executable"):
        load_skill(directory)


def test_a_symbolic_link_fails_startup(tmp_path):
    directory = _skill(tmp_path)
    (directory / "prompts" / "linked.md.j2").symlink_to(directory / "prompts" / "instructions.ar.md.j2")
    with pytest.raises(SkillError, match="symbolic link"):
        load_skill(directory)


def test_the_root_holds_only_skill_folders(tmp_path):
    _skill(tmp_path)
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    with pytest.raises(SkillError, match="only skill folders"):
        SkillRegistry.discover(tmp_path)


def test_a_missing_root_is_no_skill(tmp_path):
    assert SkillRegistry.discover(tmp_path / "absent").all() == []


def test_requires_names_only_capabilities_that_exist(tmp_path):
    _skill(tmp_path, requires={"capabilities": ["docs"]})
    registry = SkillRegistry.discover(tmp_path)
    registry.check_requires(["general", "docs"])
    with pytest.raises(SkillError, match=r"\['docs'\] are not capabilities"):
        registry.check_requires(["general"])


# ------------------------------------------------------------------ permission first (G6)


def test_a_principal_without_the_permission_never_has_the_skill(tmp_path):
    _skill(tmp_path)
    registry = SkillRegistry.discover(tmp_path)
    viewer = Principal(user_id="v", permissions=("cap:general", "cap:docs"))
    analyst = Principal(user_id="a", permissions=("cap:general", "skill:demo_form"))
    assert registry.available_for(viewer, ["general", "docs"]) == []
    assert [s.slug for s in registry.available_for(analyst, ["general"])] == ["demo_form"]
    # A permission of another kind with the same name grants nothing.
    lookalike = Principal(user_id="x", permissions=("cap:demo_form", "kb:demo_form:read"))
    assert registry.available_for(lookalike, ["general"]) == []


def test_a_skill_is_available_only_when_every_capability_it_requires_is(tmp_path):
    _skill(tmp_path, requires={"capabilities": ["docs"]})
    registry = SkillRegistry.discover(tmp_path)
    principal = Principal(user_id="a", permissions=("skill:demo_form",))
    assert registry.available_for(principal, ["general"]) == []
    assert [s.slug for s in registry.available_for(principal, ["general", "docs"])] == ["demo_form"]


def test_keywords_make_a_candidate_matched_arabic_folded(tmp_path):
    _skill(tmp_path)
    registry = SkillRegistry.discover(tmp_path)
    available = registry.all()
    assert registry.candidates(available, "اكتب لي مذكره عن الإجازات")  # «مذكره» folds to «مذكرة»
    assert registry.candidates(available, "Please fill the FORM")
    assert registry.candidates(available, "ما عاصمة المملكة؟") == []


def test_a_skill_without_the_rule_is_never_a_candidate(tmp_path):
    _skill(tmp_path, routing_hints={"keywords": {"ar": ["نموذج"], "en": []}, "rules": []})
    registry = SkillRegistry.discover(tmp_path)
    assert registry.candidates(registry.all(), "نموذج") == []


def test_the_manifest_is_immutable():
    manifest = SkillManifest.model_validate(VALID)
    with pytest.raises(Exception):
        manifest.slug = "other"  # type: ignore[misc]
