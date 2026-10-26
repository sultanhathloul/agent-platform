"""The skill contract and registry (phase 06, G2, G6, ADR-0317, ADR-0318): SKILL.md in the open
Agent Skills format, the platform's namespaced fields and their defaults, what is loaded and what
is skipped, no script or executable file, permissions and availability, keyword candidacy."""

import logging
import os
from pathlib import Path

import pytest
import yaml

from app.identity import Principal
from app.skills.contract import FrontMatter, SkillManifest
from app.skills.registry import MAX_FILE_BYTES, SkillError, SkillRegistry, load_skill

PLATFORM = {
    "version": "1.0.0",
    "name": {"ar": "نموذج تجريبي", "en": "Demo form"},
    "description": {"ar": "يرتب الإجابة في نموذج", "en": "Lays the answer out as a form"},
    "requires": {"capabilities": []},
    "routing_hints": {"keywords": {"ar": ["نموذج", "مذكرة"], "en": ["form"]}, "rules": [{"when": "keyword_candidate"}]},
}
BODY = "Lay the answer out as a form. Follow `reference/layout.md` for the sections."


def _skill(root: Path, slug: str = "demo_form", body: str = BODY, platform: dict | None = None, **front) -> Path:
    """A skill folder: SKILL.md (the open format's fields, `sovereign` when given), a referenced
    Markdown file, an unreferenced one, a licence and the evals folder."""
    directory = root / slug
    (directory / "reference").mkdir(parents=True)
    (directory / "evals").mkdir()
    matter = {"name": slug, "description": "Lays the answer out as a form. Use when asked for a form.", **front}
    if platform is not None:
        matter["sovereign"] = platform
    (directory / "SKILL.md").write_text(
        f"---\n{yaml.safe_dump(matter, allow_unicode=True)}---\n{body}\n", encoding="utf-8"
    )
    (directory / "reference" / "layout.md").write_text("REFERENCED-LAYOUT: title, items, closing.\n", encoding="utf-8")
    (directory / "reference" / "unused.md").write_text("UNREFERENCED-NOTES\n", encoding="utf-8")
    (directory / "LICENSE.txt").write_text("LICENSE-TEXT\n", encoding="utf-8")
    (directory / "evals" / f"{slug}.jsonl").write_text("", encoding="utf-8")
    (directory / "evals" / "metrics.yaml").write_text("metrics: {}\n", encoding="utf-8")
    return directory


# ------------------------------------------------------------------ the open format


def test_a_public_skill_loads_unmodified_with_the_defaults(tmp_path):
    """Only `name` and `description`, plus the open format's optional keys and one it does not
    define: permission skill:<name>, no required capability, no keyword, labels from the open
    fields, a version from the content."""
    directory = _skill(
        tmp_path, "internal-notes", license="Complete terms in LICENSE.txt", **{"allowed-tools": "Read"},
        **{"disable-model-invocation": True},
    )
    skill = load_skill(directory)
    manifest = skill.manifest
    assert manifest.slug == "internal-notes" and manifest.permission == "skill:internal-notes"
    assert manifest.requires.capabilities == () and manifest.routing_hints.rules == ()
    assert manifest.name.ar == manifest.name.en == "internal-notes"
    assert manifest.description.en.startswith("Lays the answer out")
    assert manifest.version.startswith("sha-") and len(manifest.version) == 16
    assert manifest.ref == f"internal-notes@{manifest.version}"
    assert not skill.matches("form")  # no keyword: selectable by the picker only
    front = FrontMatter.model_validate(yaml.safe_load((directory / "SKILL.md").read_text().split("---")[1]))
    assert front.ignored_keys() == ["allowed-tools", "disable-model-invocation", "license"]


def test_the_namespaced_fields_apply(tmp_path):
    skill = load_skill(_skill(tmp_path, platform=PLATFORM))
    manifest = skill.manifest
    assert manifest.ref == "demo_form@1.0.0" and manifest.policy_name == "skill:demo_form"
    assert manifest.name.ar == "نموذج تجريبي" and manifest.name.en == "Demo form"
    assert manifest.description.ar == "يرتب الإجابة في نموذج"
    assert skill.matches("املأ نموذج الطلب") and not skill.matches("ما عاصمة المملكة؟")


def test_a_missing_arabic_label_falls_back_to_the_open_field(tmp_path):
    skill = load_skill(_skill(tmp_path, platform={"name": {"en": "Demo form"}}))
    assert skill.manifest.name.en == "Demo form" and skill.manifest.name.ar == "demo_form"


@pytest.mark.parametrize(
    ("platform", "message"),
    [
        ({"permission": "cap:demo_form"}, "skill:demo_form"),
        ({"requires": {"capabilities": [], "mcp_tools": ["routing.route"]}}, "mcp_tools"),  # ADR-0307
        ({"requires": {"capabilities": ["docs", "docs"]}}, "twice"),
        ({"routing_hints": {"rules": [{"when": "keyword"}]}}, "keyword_candidate"),
        ({"routing_hints": {"rules": [{"when": "keyword_candidate"}]}}, "needs keywords"),
        ({"version": "1.0"}, "version"),
        ({"script": "run.sh"}, "script"),
    ],
)
def test_an_invalid_namespaced_field_fails_fast_naming_the_file(tmp_path, platform, message):
    directory = _skill(tmp_path, platform=platform)
    with pytest.raises(SkillError, match=message) as info:
        load_skill(directory)
    assert str(directory / "SKILL.md") in str(info.value)


# ------------------------------------------------------------------ the sidecar (ADR-0325)


def _sidecar(directory: Path, fields: dict) -> Path:
    path = directory / "skill.platform.yaml"
    path.write_text(yaml.safe_dump(fields, allow_unicode=True), encoding="utf-8")
    return path


def test_the_sidecar_gives_a_public_skill_its_fields_and_leaves_skill_md_untouched(tmp_path, caplog):
    directory = _skill(tmp_path, "internal-notes")
    before = (directory / "SKILL.md").read_bytes()
    _sidecar(directory, PLATFORM)
    with caplog.at_level(logging.INFO, logger="app.skills.registry"):
        skill = load_skill(directory)
    manifest = skill.manifest
    assert manifest.ref == "internal-notes@1.0.0" and manifest.permission == "skill:internal-notes"
    assert manifest.name.ar == "نموذج تجريبي" and manifest.description.ar == "يرتب الإجابة في نموذج"
    assert skill.matches("املأ نموذج الطلب") and not skill.matches("ما عاصمة المملكة؟")
    assert (directory / "SKILL.md").read_bytes() == before
    # Read by the loader, never loaded into the model, and not among the skipped files.
    assert skill.platform_file == "skill.platform.yaml"
    assert "skill.platform.yaml" not in skill.loaded and "skill.platform.yaml" not in skill.skipped
    assert "keyword_candidate" not in skill.instructions and "نموذج تجريبي" not in skill.instructions
    line = next(r.getMessage() for r in caplog.records if "internal-notes@1.0.0" in r.getMessage())
    assert "platform_file=skill.platform.yaml" in line


def test_without_a_sidecar_version_the_content_version_covers_the_sidecar(tmp_path):
    directory = _skill(tmp_path)
    plain = load_skill(directory).manifest.version
    _sidecar(directory, {"name": {"ar": "نموذج"}})
    first = load_skill(directory).manifest.version
    assert first.startswith("sha-") and first != plain
    _sidecar(directory, {"name": {"ar": "نموذج آخر"}})
    assert load_skill(directory).manifest.version != first


def test_a_sidecar_and_the_namespaced_block_together_fail_startup(tmp_path):
    directory = _skill(tmp_path, platform={"name": {"en": "Demo form"}})
    _sidecar(directory, PLATFORM)
    with pytest.raises(SkillError, match="keep one"):
        load_skill(directory)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"permission": "skill:other"}, "skill:demo_form"),
        ({"requires": {"mcp_tools": ["routing.route"]}}, "mcp_tools"),
        ({"routing_hints": {"rules": [{"when": "keyword_candidate"}]}}, "needs keywords"),
        ({"unknown": 1}, "unknown"),
    ],
)
def test_an_invalid_sidecar_fails_fast_naming_it(tmp_path, fields, message):
    directory = _skill(tmp_path)
    path = _sidecar(directory, fields)
    with pytest.raises(SkillError, match=message) as info:
        load_skill(directory)
    assert str(path) in str(info.value)


def test_a_sidecar_that_is_not_yaml_fails_fast(tmp_path):
    directory = _skill(tmp_path)
    (directory / "skill.platform.yaml").write_text("name: [unclosed\n", encoding="utf-8")
    with pytest.raises(SkillError, match="invalid skill.platform.yaml"):
        load_skill(directory)


@pytest.mark.parametrize(
    ("front", "message"),
    [({"description": ""}, "description"), ({"description": "x" * 1025}, "description")],
)
def test_the_open_fields_are_required(tmp_path, front, message):
    directory = _skill(tmp_path, **front)
    with pytest.raises(SkillError, match=message):
        load_skill(directory)


@pytest.mark.parametrize("name", ["Demo", "-demo", "demo form", "x" * 65])
def test_a_name_outside_the_format_fails(tmp_path, name):
    directory = tmp_path / "demo"
    directory.mkdir()
    (directory / "SKILL.md").write_text(f"---\nname: {name!r}\ndescription: d\n---\nbody\n", encoding="utf-8")
    with pytest.raises(SkillError, match="name"):
        load_skill(directory)


def test_the_name_must_match_its_folder(tmp_path):
    directory = _skill(tmp_path)
    directory.rename(tmp_path / "other_name")
    with pytest.raises(SkillError, match="must match directory"):
        load_skill(tmp_path / "other_name")


def test_skill_md_without_front_matter_fails(tmp_path):
    directory = _skill(tmp_path)
    (directory / "SKILL.md").write_text("# just notes\n", encoding="utf-8")
    with pytest.raises(SkillError, match="front-matter"):
        load_skill(directory)


def test_a_folder_without_skill_md_fails(tmp_path):
    directory = _skill(tmp_path)
    (directory / "SKILL.md").unlink()
    with pytest.raises(SkillError, match="no SKILL.md"):
        load_skill(directory)


# ------------------------------------------------------------------ what is loaded (ADR-0318)


def test_the_instructions_are_the_body_and_the_markdown_files_it_names(tmp_path):
    skill = load_skill(_skill(tmp_path, platform=PLATFORM))
    assert skill.loaded == ("SKILL.md", "reference/layout.md")
    assert skill.instructions.startswith(BODY)
    assert '<file path="reference/layout.md">\nREFERENCED-LAYOUT' in skill.instructions
    # The front-matter, the licence, an unreferenced Markdown file and the evals never reach the model.
    for absent in ("LICENSE-TEXT", "UNREFERENCED-NOTES", "sovereign", "metrics"):
        assert absent not in skill.instructions
    assert skill.skipped == ("LICENSE.txt", "evals/demo_form.jsonl", "evals/metrics.yaml", "reference/unused.md")


def test_named_files_load_in_the_order_the_body_names_them(tmp_path):
    directory = _skill(tmp_path, body="First `b.md`, then `reference/layout.md`, then `b.md` again.")
    (directory / "b.md").write_text("FILE-B\n", encoding="utf-8")
    skill = load_skill(directory)
    assert skill.loaded == ("SKILL.md", "b.md", "reference/layout.md")
    assert skill.instructions.index("FILE-B") < skill.instructions.index("REFERENCED-LAYOUT")


def test_a_path_inside_a_longer_one_is_not_named(tmp_path):
    directory = _skill(tmp_path, body="See `reference/layout.md`.")
    (directory / "layout.md").write_text("ROOT-LAYOUT\n", encoding="utf-8")
    skill = load_skill(directory)
    assert "layout.md" not in skill.loaded and "ROOT-LAYOUT" not in skill.instructions


def test_the_startup_log_lists_the_skipped_files(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="app.skills.registry"):
        load_skill(_skill(tmp_path, platform=PLATFORM))
    line = next(r.getMessage() for r in caplog.records if "demo_form@1.0.0" in r.getMessage())
    assert "'LICENSE.txt'" in line and "'reference/unused.md'" in line and "skipped=" in line


def test_the_content_version_follows_the_loaded_files_only(tmp_path):
    directory = _skill(tmp_path)
    first = load_skill(directory).manifest.version
    (directory / "LICENSE.txt").write_text("changed\n", encoding="utf-8")
    (directory / "reference" / "unused.md").write_text("changed\n", encoding="utf-8")
    assert load_skill(directory).manifest.version == first
    (directory / "reference" / "layout.md").write_text("changed\n", encoding="utf-8")
    assert load_skill(directory).manifest.version != first


# ------------------------------------------------------------------ no code (D3, ADR-0291)


@pytest.mark.parametrize(
    "extra", ["run.sh", "helper.py", "scripts/build.py", "reference/tool.js", "bin/start.bat", "evals/score.py"]
)
def test_a_script_fails_startup_with_its_path(tmp_path, extra):
    directory = _skill(tmp_path)
    target = directory / extra
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", encoding="utf-8")
    with pytest.raises(SkillError, match="no script") as info:
        load_skill(directory)
    assert extra in str(info.value)


def test_a_file_with_a_shebang_is_a_script(tmp_path):
    directory = _skill(tmp_path)
    (directory / "tool").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    with pytest.raises(SkillError, match="no script"):
        load_skill(directory)


@pytest.mark.parametrize("name", ["SKILL.md", "LICENSE.txt", "reference/layout.md", "evals/metrics.yaml"])
def test_an_executable_bit_fails_startup(tmp_path, name):
    directory = _skill(tmp_path)
    os.chmod(directory / name, 0o755)
    with pytest.raises(SkillError, match="executable"):
        load_skill(directory)


def test_a_symbolic_link_fails_startup(tmp_path):
    directory = _skill(tmp_path)
    (directory / "reference" / "linked.md").symlink_to(directory / "reference" / "layout.md")
    with pytest.raises(SkillError, match="symbolic link"):
        load_skill(directory)


def test_an_oversize_file_fails_before_it_is_read(tmp_path):
    directory = _skill(tmp_path)
    (directory / "reference" / "layout.md").write_text("x" * (MAX_FILE_BYTES + 1), encoding="utf-8")
    with pytest.raises(SkillError, match="over"):
        load_skill(directory)


def test_the_root_holds_only_skill_folders(tmp_path):
    _skill(tmp_path)
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    with pytest.raises(SkillError, match="only skill folders"):
        SkillRegistry.discover(tmp_path)


def test_a_missing_root_is_no_skill(tmp_path):
    assert SkillRegistry.discover(tmp_path / "absent").all() == []


def test_requires_names_only_capabilities_that_exist(tmp_path):
    _skill(tmp_path, platform={"requires": {"capabilities": ["docs"]}})
    registry = SkillRegistry.discover(tmp_path)
    registry.check_requires(["general", "docs"])
    with pytest.raises(SkillError, match=r"\['docs'\] are not capabilities"):
        registry.check_requires(["general"])


# ------------------------------------------------------------------ permission first (G6)


def test_a_principal_without_the_permission_never_has_the_skill(tmp_path):
    _skill(tmp_path, platform=PLATFORM)
    registry = SkillRegistry.discover(tmp_path)
    viewer = Principal(user_id="v", permissions=("cap:general", "cap:docs"))
    analyst = Principal(user_id="a", permissions=("cap:general", "skill:demo_form"))
    assert registry.available_for(viewer, ["general", "docs"]) == []
    assert [s.slug for s in registry.available_for(analyst, ["general"])] == ["demo_form"]
    # A permission of another kind with the same name grants nothing.
    lookalike = Principal(user_id="x", permissions=("cap:demo_form", "kb:demo_form:read"))
    assert registry.available_for(lookalike, ["general"]) == []


def test_a_public_skill_needs_its_default_permission(tmp_path):
    _skill(tmp_path, "internal-notes")
    registry = SkillRegistry.discover(tmp_path)
    assert registry.available_for(Principal(user_id="v", permissions=("cap:general",)), ["general"]) == []
    holder = Principal(user_id="a", permissions=("cap:general", "skill:internal-notes"))
    assert [s.slug for s in registry.available_for(holder, ["general"])] == ["internal-notes"]


def test_a_skill_is_available_only_when_every_capability_it_requires_is(tmp_path):
    _skill(tmp_path, platform={"requires": {"capabilities": ["docs"]}})
    registry = SkillRegistry.discover(tmp_path)
    principal = Principal(user_id="a", permissions=("skill:demo_form",))
    assert registry.available_for(principal, ["general"]) == []
    assert [s.slug for s in registry.available_for(principal, ["general", "docs"])] == ["demo_form"]


def test_keywords_make_a_candidate_matched_arabic_folded(tmp_path):
    _skill(tmp_path, platform=PLATFORM)
    registry = SkillRegistry.discover(tmp_path)
    available = registry.all()
    assert registry.candidates(available, "اكتب لي مذكره عن الإجازات")  # «مذكره» folds to «مذكرة»
    assert registry.candidates(available, "Please fill the FORM")
    assert registry.candidates(available, "ما عاصمة المملكة؟") == []


def test_a_skill_without_the_rule_is_never_a_candidate(tmp_path):
    _skill(tmp_path, platform={"routing_hints": {"keywords": {"ar": ["نموذج"], "en": []}, "rules": []}})
    registry = SkillRegistry.discover(tmp_path)
    assert registry.candidates(registry.all(), "نموذج") == []


def test_the_manifest_is_immutable(tmp_path):
    manifest = load_skill(_skill(tmp_path, platform=PLATFORM)).manifest
    assert isinstance(manifest, SkillManifest)
    with pytest.raises(Exception):
        manifest.slug = "other"  # type: ignore[misc]


@pytest.mark.parametrize("code", ["tool.py", "run.sh", "executable"])
def test_a_skill_holding_code_fails_the_service_startup(settings, tmp_path, code):
    """Startup itself refuses a skills root with a script or an executable file (D3)."""
    from app.main import build_services

    directory = _skill(tmp_path / "skills", platform=PLATFORM)
    if code == "executable":
        os.chmod(directory / "reference" / "layout.md", 0o755)
    else:
        (directory / code).write_text("print('x')\n", encoding="utf-8")
    with pytest.raises(SkillError, match=code if code != "executable" else "executable"):
        build_services(settings.model_copy(update={"skills_dir": tmp_path / "skills"}))
