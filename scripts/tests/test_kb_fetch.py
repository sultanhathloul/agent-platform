"""`make kb-fetch`'s choice of documents (owner decision 3 on PR #17, ADR-0395): the newest edition of every
publication series, each series keeping its own (health status 2025 beside the 2026 series), a quarterly
series apart from an annual one, editions matched across spelling (alef forms, harakat, tatweel)."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import kb_fetch  # noqa: E402
from kb_fetch import Item  # noqa: E402


def item(title: str, year: int | None, listing: str = "l") -> Item:
    return Item(title, year, "", [], listing)


def test_every_series_keeps_its_own_newest_edition_whatever_the_categorys_latest_year():
    chosen = kb_fetch.select([
        item("إحصاءات تنمية الطفولة المبكرة 2026", 2026, "ecd"),
        item("إحصاءات الحالة الصحية 2025", 2025, "status"),
        item("إحصاءات الحالة الصحية 2024", 2024, "status"),
    ])
    assert [(i.title, i.year) for i in chosen] == [("إحصاءات تنمية الطفولة المبكرة 2026", 2026), ("إحصاءات الحالة الصحية 2025", 2025)]


def test_the_latest_quarter_wins_and_an_annual_index_is_its_own_series():
    chosen = kb_fetch.select([
        item("مؤشر ثقة المستهلك للربع الثاني 2026", 2026),
        item("مؤشر ثقة المستهلك للربع الأول 2026", 2026),
        item("مؤشر ثقة المستهلك لعام 2025", 2025),
        item("مؤشر ثقة المستهلك لعام 2024", 2024),
    ])
    assert [i.title for i in chosen] == ["مؤشر ثقة المستهلك للربع الثاني 2026", "مؤشر ثقة المستهلك لعام 2025"]


def test_editions_match_across_harakat_tatweel_alef_and_indic_years():
    assert kb_fetch.series("الإحصاءات السجليَّة لسوق العمل للربع الثاني لعام 2026") == kb_fetch.series(
        "الاحصاءات السجلية لسوق العمل للربع الأول 2024")
    assert kb_fetch.series("تـقريـر الشباب ٢٠١٩م") == kb_fetch.series("الشباب 2020")


def test_an_undated_item_stands_for_its_series_only_without_a_dated_edition():
    assert [i.year for i in kb_fetch.select([item("الأطلس", None), item("الأطلس 2010", 2010)])] == [2010]
    assert [i.year for i in kb_fetch.select([item("التعداد", None)])] == [None]
