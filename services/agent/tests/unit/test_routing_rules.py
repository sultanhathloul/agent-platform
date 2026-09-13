from pathlib import Path

from app.capabilities.contract import Manifest
from app.capabilities.registry import LoadedCapability
from app.graph.nodes.route import MAX_CAPABILITIES, select_capabilities
from app.text.arabic import contains_keyword, normalize


def _cap(slug, rules, keywords_ar=(), keywords_en=(), order=10):
    manifest = Manifest.model_validate(
        {
            "slug": slug,
            "version": "1.0.0",
            "name": {"ar": slug, "en": slug},
            "description": {"ar": slug, "en": slug},
            "icon": "x",
            "permission": f"cap:{slug}",
            "ui": {"mode": "auto", "order": order},
            "routing_hints": {
                "description": {"ar": slug, "en": slug},
                "keywords": {"ar": list(keywords_ar), "en": list(keywords_en)},
                "rules": [{"when": r} for r in rules],
            },
            "model": {"min_tier": "fast"},
            "state_namespace": f"cap.{slug}",
        }
    )
    return LoadedCapability(manifest=manifest, graph=None, path=Path(slug))


GENERAL = _cap("general", ["fallback"], order=100)
PROOF = _cap("proof", ["toggled", "keyword"], keywords_ar=["دقق", "إملاء"], keywords_en=["proofread"])
DOCS = _cap("docs", ["attachments"])


def test_fallback_only_when_nothing_else_matches():
    assert select_capabilities(
        [PROOF, DOCS, GENERAL], message="ما عاصمة المملكة؟", has_attachments=False
    ) == (["general"], ["general:fallback"])


def test_arabic_keyword_matches_despite_diacritics_and_hamza_forms():
    slugs, reasons = select_capabilities(
        [PROOF, GENERAL], message="دقّق هذا الخطاب من فضلك", has_attachments=False
    )
    assert slugs == ["proof"] and reasons == ["proof:keyword"]
    assert select_capabilities([PROOF, GENERAL], message="راجع الاملاء", has_attachments=False)[0] == [
        "proof"
    ]


def test_attachments_and_toggles():
    assert select_capabilities([DOCS, GENERAL], message="لخص الملف", has_attachments=True)[0] == ["docs"]
    assert select_capabilities([PROOF, GENERAL], message="hello", has_attachments=False, toggled=["proof"])[
        0
    ] == ["proof"]


def test_at_most_three_capabilities():
    many = [_cap(f"c{i}", ["keyword"], keywords_en=["report"]) for i in range(5)]
    assert len(select_capabilities(many, message="report", has_attachments=False)[0]) == MAX_CAPABILITIES


def test_normalize_folds_arabic_variants():
    assert normalize("أإآٱ") == "اااا"
    assert normalize("مدرسة") == normalize("مدرسه")
    assert normalize("على") == normalize("علي")
    assert normalize("كـــتاب") == "كتاب"
    assert normalize("مُحَمَّد") == "محمد"
    assert normalize("٢٠٢٦") == "2026"
    assert normalize("  Hello World ") == "hello world"
    assert contains_keyword("وبالمدرسة", "مدرسة")
    assert not contains_keyword("أي نص", "")
