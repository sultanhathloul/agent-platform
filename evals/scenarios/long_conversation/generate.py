"""Writes scenario.yaml (run from the repository root: python3 evals/scenarios/long_conversation/generate.py)."""
import json

import yaml

TABLE = "احفظ هذا الجدول عن مبيعات فروعنا في الربع الأول:\nالفرع\tالمبيعات (ألف ريال)\tعدد العملاء\nالرياض\t1250\t340\nجدة\t980\t275\nالدمام\t760\t190\nأبها\t430\t120\nتبوك\t310\t95"
K = ["stats-gov"]
steps = []
# A question that is not about a report never mentions the reports (the first run's defect, ADR-0439).
NOT_ABOUT_REPORTS = ["التقارير المذكوره", "لا تحتوي التقارير", "في التقارير المتاحه", "من التقرير"]


def t(i, msg, expect=None, **kw):
    d = {"id": i, "conversation": "A", "message": msg}
    d.update(kw)
    d["expect"] = expect or {}
    steps.append(d)


t("a01", "ما عاصمة اليابان؟", {"form": "text", "answer_any": ["طوكيو"]})
t("a02", TABLE, {"form": "text", "answer_none": NOT_ABOUT_REPORTS})
t("a03", "كم معدل البطالة للسعوديين في آخر نشرة لسوق العمل؟", {"form": "text", "answer_any": ["%", "٪", "بالمئة", "في المئة"]}, selected_kbs=K)
t("a04", "اشرح باختصار ما هو التضخم.", {"form": "text"})
t("a05", "سوّ لي داشبورد عن إحصاءات سوق العمل في السعودية", {"form": "report", "version": 1, "report": "labor"}, selected_kbs=K)
t("a06", "أضف للوحة رسمًا لمعدل البطالة حسب الجنس", {"form": "report", "version": 2, "same_report": "labor"}, selected_kbs=K)
t("a07", "عدّل اللوحة: أضف جدولًا للمشاركة في قوة العمل حسب الجنسية", {"form": "report", "version": 3, "same_report": "labor"}, selected_kbs=K)
t("a08", "ابحث في اللوحة عن معدل البطالة للإناث", {"form": "text", "tools_any": ["report_read"], "cites_report": True})
t("a09", "سوّ لي تقرير تفاعلي عن تاريخ بطولة كأس العالم لكرة القدم وأبرز الفائزين بها", {"form": "report", "version": 1, "report": "worldcup", "new_report": True})
t("a10", "سوّ لي داشبورد عن أسعار النفط العالمية خلال العقد الأخير", {"nothing_found": True, "no_artifact": True}, selected_kbs=K)
t("a11", "وش التقارير اللي اشتغلنا عليها؟", {"form": "text", "mentions_reports": ["labor", "worldcup"]})
t("a12", "منهم أبطال كأس العالم المذكورين في التقرير السابق؟", {"form": "text", "no_artifact": True, "answer_any": ["البرازيل", "ألمانيا", "الأرجنتين", "إيطاليا"]})
t("a13", "للعلم أنا أعمل في إدارة الإحصاءات الاجتماعية. ما هو مؤشر جيني؟", {"form": "text"})
fill = [
    ("ما الفرق بين الناتج المحلي الإجمالي والناتج القومي؟", None), ("ما عدد سكان المملكة حسب آخر تعداد؟", K),
    ("اشرح مفهوم الاقتصاد الدائري بأمثلة.", None), ("كم نسبة السعوديين في القوى العاملة؟", K),
    ("ما أهم أهداف رؤية المملكة 2030 في قطاع السياحة؟", None), ("ما معدل المشاركة في قوة العمل للنساء السعوديات؟", K),
    ("اشرح الفرق بين البطالة الهيكلية والبطالة الدورية.", None), ("ما أبرز مؤشرات الصحة في المملكة؟", K),
    ("كيف يُحسب معدل التضخم؟", None), ("ما متوسط حجم الأسرة السعودية؟", K),
    ("ما هي أكبر خمس دول من حيث المساحة؟", None), ("ما نسبة الأمية في المملكة؟", K),
    ("اشرح ما هي السياسة النقدية.", None), ("كم عدد الطلاب في التعليم العام؟", K),
    ("ما الفرق بين الإحصاء الوصفي والاستدلالي؟", None), ("ما نسبة ذوي الإعاقة في المملكة؟", K),
    ("اشرح معنى الانحراف المعياري بمثال بسيط.", None), ("ما توزيع السكان حسب الفئات العمرية؟", K),
    ("ما هي أهم مصادر الطاقة المتجددة؟", None), ("ما معدل البطالة لغير السعوديين؟", K),
    ("اشرح مفهوم العينة العشوائية الطبقية.", None), ("كم عدد العاملين في القطاع الخاص؟", K),
    ("ما الفرق بين المتوسط والوسيط؟", None), ("ما نسبة المدخنين في المملكة؟", K),
    ("اشرح ما هو مؤشر أسعار المستهلك.", None), ("ما نسبة المتعطلين الحاصلين على شهادة جامعية؟", K),
    ("ما هي عاصمة البرازيل وما أكبر مدنها؟", None), ("كم عدد الزوار القادمين للسياحة؟", K),
    ("اشرح الفرق بين الارتباط والسببية.", None), ("ما متوسط ساعات العمل الأسبوعية؟", K),
    ("ما أهم مراحل إعداد الميزانية العامة؟", None), ("ما نسبة الأسر التي تملك مسكنًا؟", K),
    ("اشرح ما المقصود بالتضخم الركودي.", None), ("ما توزيع المشتغلين حسب المهنة؟", K),
    ("ما هي فوائد التحول الرقمي في الجهات الحكومية؟", None), ("ما الفرق بين النمو الاقتصادي والتنمية الاقتصادية؟", None),
    ("ما معدل البطالة حسب المنطقة الإدارية؟", K), ("اشرح باختصار ما هي سلاسل الإمداد.", None),
    ("ما أبرز التحديات أمام سوق العمل عالميًا؟", None), ("كم نسبة الشباب من إجمالي السكان؟", K),
]
n = 14
for msg, kb in fill:
    if kb:
        t(f"a{n:02d}", msg, {"form": "text", "answer_none": NOT_ABOUT_REPORTS}, selected_kbs=kb)
    else:
        # Asked at length, so the history passes the summary threshold (8000 tokens) well before the recall steps.
        t(f"a{n:02d}", msg.rstrip(".؟") + "؟ اشرح بالتفصيل في ثلاث فقرات.", {"form": "text", "answer_none": NOT_ABOUT_REPORTS})
    n += 1
steps[-1]["expect"]["summary"] = True
for msg, exp in [
    ("وش كانت مبيعات فرع جدة في الجدول اللي أرسلته لك في بداية المحادثة؟", {"form": "text", "answer_any": ["980", "٩٨٠"], "tools_any": ["conversation_recall"], "summary": True}),
    ("وكم عدد عملاء فرع أبها في ذاك الجدول؟", {"form": "text", "answer_any": ["120", "١٢٠"]}),
    ("كم كان معدل البطالة للسعوديين اللي ذكرته لي في بداية المحادثة؟", {"form": "text", "numbers_of": "a03"}),
    ("ابحث في لوحة سوق العمل عن نسبة المشاركة في قوة العمل", {"form": "text", "tools_any": ["report_read"], "cites_report": True, "no_artifact": True}),
    ("وش التقارير اللي سويناها في هذي المحادثة؟", {"form": "text", "mentions_reports": ["labor", "worldcup"]}),
    ("ما أكبر محيط في العالم؟", {"form": "text", "answer_any": ["الهادئ", "الهادي"], "answer_none": NOT_ABOUT_REPORTS}),
    ("أي فرع في الجدول الذي أرسلته كان الأقل مبيعات؟", {"form": "text", "answer_any": ["تبوك"]}),
    ("شكرًا، لخص لي في جملتين ما تحدثنا عنه اليوم.", {"form": "text"}),
]:
    t(f"a{n:02d}", msg, exp)
    n += 1
assert n - 1 >= 60, n
steps.append({"id": "b01", "conversation": "B", "message": "التقرير اللي سويته عن سوق العمل، وش نسبة البطالة فيه؟",
              "expect": {"form": "text", "tools_any": ["report_read"], "cites_report": True, "answer_any": ["%", "٪"]}})
steps.append({"id": "b02", "conversation": "B", "message": "سوّ لي نسخة جديدة منه وأضف رسمًا للمشاركة في قوة العمل",
              "selected_kbs": K, "expect": {"form": "report", "version": 1, "report": "labor_b", "new_report": True, "based_on": True}})
steps.append({"id": "b03", "check": "report", "report": "labor", "expect": {"status": 200, "latest_version": 3}})
steps.append({"id": "v01", "as": "viewer", "check": "library", "query": "سوق العمل", "negative": True, "expect": {"none_of": ["labor", "worldcup", "labor_b"]}})
steps.append({"id": "v02", "as": "viewer", "check": "report", "report": "labor", "negative": True, "expect": {"status": 404}})
steps.append({"id": "v03", "as": "viewer", "conversation": "V", "negative": True, "message": "وش التقارير اللي سويتها عن سوق العمل؟ وش نسبة البطالة فيها؟", "expect": {"answer_none": ["إحصاءات سوق العمل"], "no_artifact": True}})
steps.append({"id": "v04", "as": "viewer", "conversation": "V", "negative": True, "message": "وش كانت مبيعات فرع جدة في الجدول اللي أرسلته في بداية المحادثة؟", "expect": {"answer_none": ["980", "٩٨٠"]}})
steps.append({"id": "o01", "check": "library", "query": "سوق العمل", "expect": {"all_of": ["labor", "labor_b"]}})
steps.append({"id": "m01", "check": "memory", "negative": True, "expect": {"memory_none": ["التخطيط", "كأس العالم", "النفط", "جده", "1250"]}})
doc = {"user": "analyst", "measure_at": [1, 10, 30, 60], "steps": steps}
head = """# The heavy scenario of the report library PR (owner decisions 1 to 7, ADR-0438), run through the gateway as
# analyst. Conversation A: 61 turns mixing general questions, questions over the stats-gov knowledge base, a
# pasted 5-row table (a02), a dashboard over stats-gov and two edits (a05 to a07), a general-knowledge report
# (a09), a report over stats-gov on an unrelated subject (a10, nothing found), questions about the reports'
# content, and, after the summary threshold, questions about the pasted table and the figure of a03.
# Conversation B: a question about the labour-market report from A, then a new version of it here. The viewer
# then finds nothing of analyst's; analyst's memory holds nothing analyst did not state.
"""
open("evals/scenarios/long_conversation/scenario.yaml", "w").write(head + yaml.safe_dump(json.loads(json.dumps(doc)), allow_unicode=True, sort_keys=False, width=200))
print(n - 1, len(steps))
