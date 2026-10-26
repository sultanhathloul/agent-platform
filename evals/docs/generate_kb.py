"""Generates the fictional Arabic documents of the kb suite's two knowledge bases (phase 08, I6).
Every entity, figure and date here is invented: «الواحة» is no real country and no document is a
real report (ADR-0065). Two versions of one outlook (April, then July, which supersedes it) differ
on the growth and inflation forecasts, so the suite can check that the latest figure is the answer.

Run in the agent test image, which has the Arabic font and the PDF generator:
  docker compose --profile test run --rm -v "$PWD/evals:/evals" -v "$PWD/services/agent/tests:/app/tests" \
    agent-tests python /evals/docs/generate_kb.py /evals/docs/kb

Writes only under evals/docs/kb/; the documents of the other suites are left as they are."""

import sys
from pathlib import Path

sys.path.insert(0, "/app")

from tests.fixtures.documents import pdf_bytes  # noqa: E402

FUND = "صندوق الواحة للنقد"
STATS = "هيئة إحصاء الواحة"
MINISTRY = "وزارة الموارد البشرية في الواحة"

OUTLOOK_APRIL = [
    [
        "آفاق اقتصاد الواحة - إصدار أبريل 2031",
        "يصدر " + FUND + " هذا التقرير مرتين في السنة.",
        "يعرض التقرير توقعات النمو والتضخم والافتراضات التي بنيت عليها.",
    ],
    [
        "توقعات النمو والتضخم",
        "يتوقع الصندوق أن ينمو الناتج المحلي لاقتصاد الواحة بنسبة 3.1 بالمئة في عام 2031.",
        "ويتوقع أن يبلغ معدل التضخم 2.4 بالمئة في عام 2031.",
    ],
    [
        "الافتراضات",
        "يفترض التقرير أن متوسط سعر برميل النفط سيبلغ 71 دولارا في عام 2031.",
        "ويفترض استقرار سعر صرف ريال الواحة مقابل سلة العملات.",
    ],
]

OUTLOOK_JULY = [
    [
        "آفاق اقتصاد الواحة - تحديث يوليو 2031",
        "يحدّث " + FUND + " في هذا الإصدار توقعات إصدار أبريل 2031.",
        "يعرض التقرير توقعات النمو والتضخم وسوق العمل.",
    ],
    [
        "توقعات النمو والتضخم بعد التحديث",
        "يتوقع الصندوق الآن أن ينمو الناتج المحلي لاقتصاد الواحة بنسبة 3.6 بالمئة في عام 2031.",
        "ويتوقع أن يبلغ معدل التضخم 2.2 بالمئة في عام 2031.",
    ],
    [
        "سوق العمل",
        "يتوقع الصندوق أن ينخفض معدل البطالة في الواحة إلى 5.8 بالمئة بنهاية عام 2031.",
    ],
]

TRADE_2030 = [
    [
        "تقرير التجارة الخارجية للواحة لعام 2030",
        "تصدر " + STATS + " هذا التقرير سنويا.",
    ],
    [
        "الصادرات",
        "بلغت صادرات التمور من الواحة 812 مليون ريال في عام 2030.",
        "وبلغت صادرات الأسمدة 455 مليون ريال في العام نفسه.",
    ],
    [
        "الشركاء التجاريون",
        "كانت جمهورية السهل أكبر شريك تجاري للواحة في عام 2030 بحصة 18 بالمئة من التجارة.",
    ],
]

LABOR_LAW = [
    [
        "نظام العمل في الواحة",
        "تصدر " + MINISTRY + " هذا النظام ويسري على العاملين في القطاع الخاص.",
    ],
    [
        "الإجازات",
        "تستحق العاملة إجازة أمومة مدتها عشرة أسابيع بأجر كامل.",
        "يستحق العامل إجازة سنوية مدتها واحد وعشرون يوما، تزيد إلى ثلاثين يوما بعد خمس سنوات خدمة.",
    ],
    [
        "التجربة",
        "لا تزيد فترة التجربة على تسعين يوما، ويجوز تمديدها مرة واحدة باتفاق مكتوب.",
    ],
]

REMOTE_GUIDE = [
    [
        "دليل العمل عن بعد",
        "يصدر هذا الدليل عن " + MINISTRY + " لتنظيم العمل عن بعد في القطاع الخاص.",
    ],
    [
        "أيام العمل عن بعد",
        "يجوز للعامل العمل عن بعد يومين في الأسبوع بموافقة مديره المباشر.",
        "ويلتزم العامل بساعات العمل الأساسية من العاشرة صباحا حتى الثانية ظهرا.",
    ],
]

DOCUMENTS = {
    "outlook-april-2031.pdf": OUTLOOK_APRIL,
    "outlook-july-2031.pdf": OUTLOOK_JULY,
    "trade-report-2030.pdf": TRADE_2030,
    "labor-law.pdf": LABOR_LAW,
    "remote-work-guide.pdf": REMOTE_GUIDE,
}


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, pages in DOCUMENTS.items():
        # Laid out as a bidi-aware producer lays Arabic out (owner decision A2), as the rag documents are.
        (out / name).write_bytes(pdf_bytes(pages, visual=True))
    print("written:", sorted(p.name for p in out.iterdir()))


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/evals/docs/kb"))
