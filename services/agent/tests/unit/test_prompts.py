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
    assert rendered.ref == "answer.system@1.6.0"
    assert "أجب بلغة رسالة المستخدم" in rendered.text
    assert "بالحروف العربية" in rendered.text  # script rule (ADR-0027)
    assert "لا تختلق روابط" in rendered.text  # on every turn, evidence or not (F3, ADR-0342)
    assert "الأدلة" not in rendered.text and "الأدوات" not in rendered.text
    assert "{{" not in rendered.text and "{%" not in rendered.text


def test_evidence_tools_and_hints_render_only_when_present(prompts):
    rendered = prompts.render(
        "answer.system", "en", tools=["web_search"], has_evidence=True, policy_hints=["x"]
    )
    assert "evidence below" in rendered.text
    assert "Never invent a URL" in rendered.text
    assert "Available tools: web_search." in rendered.text
    assert "- x" in rendered.text


@pytest.mark.parametrize("locale", ["ar", "en"])
@pytest.mark.parametrize("has_evidence", [False, True])
@pytest.mark.parametrize("has_skill", [False, True])
def test_the_url_rule_and_the_skill_line_render_on_every_turn(prompts, locale, has_evidence, has_skill):
    """F3 (ADR-0342): the never-invent-a-URL rule sits outside the evidence block; the skill
    precedence line stays (ADR-0300)."""
    text = prompts.render(
        "answer.system", locale, tools=[], has_evidence=has_evidence, policy_hints=[], has_skill=has_skill
    ).text
    assert ("لا تختلق روابط" if locale == "ar" else "Never invent a URL") in text
    precedence = "تتقدم سياسات الحماية" if locale == "ar" else "the guard policies and the citation rules come before"
    assert (precedence in text) == has_skill


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
