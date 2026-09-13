import pytest
from jinja2 import UndefinedError

from app.models.prompts import PromptError, PromptLibrary


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_repo_prompts_load_in_both_locales(prompts):
    for prompt_id in ("answer.system", "context.state_digest", "context.findings"):
        prompts.require(prompt_id)


def test_answer_prompt_renders_arabic_without_optional_sections(prompts):
    rendered = prompts.render("answer.system", "ar", tools=[], has_evidence=False, policy_hints=[])
    assert rendered.ref == "answer.system@1.2.0"
    assert "أجب بلغة رسالة المستخدم" in rendered.text
    assert "بالحروف العربية" in rendered.text  # script rule (ADR-0027)
    assert "الأدلة" not in rendered.text and "الأدوات" not in rendered.text
    assert "{{" not in rendered.text and "{%" not in rendered.text


def test_evidence_tools_and_hints_render_only_when_present(prompts):
    rendered = prompts.render(
        "answer.system", "en", tools=["web_search"], has_evidence=True, policy_hints=["x"]
    )
    assert "evidence below" in rendered.text
    assert "Available tools: web_search." in rendered.text
    assert "- x" in rendered.text


def test_missing_front_matter_fails(tmp_path):
    _write(tmp_path / "p.md.j2", "no front matter")
    with pytest.raises(PromptError, match="front-matter"):
        PromptLibrary.load(tmp_path)


def test_missing_version_fails(tmp_path):
    _write(tmp_path / "p.md.j2", "---\nid: a\nrole: r\nlocale: ar\n---\nنص")
    with pytest.raises(PromptError, match="version"):
        PromptLibrary.load(tmp_path)


def test_duplicate_id_and_locale_fails(tmp_path):
    body = "---\nid: a\nversion: 1.0.0\nrole: r\nlocale: ar\n---\nنص"
    _write(tmp_path / "one.md.j2", body)
    _write(tmp_path / "two.md.j2", body)
    with pytest.raises(PromptError, match="duplicate"):
        PromptLibrary.load(tmp_path)


def test_require_reports_missing_locale(tmp_path):
    _write(tmp_path / "a.ar.md.j2", "---\nid: a\nversion: 1.0.0\nrole: r\nlocale: ar\n---\nنص")
    library = PromptLibrary.load(tmp_path)
    with pytest.raises(PromptError, match="en"):
        library.require("a")


def test_undefined_variable_is_an_error(tmp_path):
    _write(tmp_path / "a.md.j2", "---\nid: a\nversion: 1.0.0\nrole: r\nlocale: en\n---\n{{ missing }}")
    with pytest.raises(UndefinedError):
        PromptLibrary.load(tmp_path).render("a", "en")
