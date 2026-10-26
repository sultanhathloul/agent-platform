"""Page attribution with a source named in the answer (phase 08, D3, ADR-0354): a KB answer states its
source's title, publisher and date in the sentence; those words are the source's metadata, not its
chunk text, so they are dropped before attribution, as the question's words are."""

from app.rag.citations import AttributionRule, attribute, load_attribution_rule

CHUNK = "الصادرات\nبلغت صادرات التمور من الواحة 812 مليون ريال في عام 2030.\nوبلغت صادرات الأسمدة 455 مليون ريال."
SOURCE = {"id": "c1", "ref": "c1", "document_id": "d1", "title": "تقرير التجارة الخارجية للواحة 2030",
          "kind": "kb", "kb": "eco", "kb_name": {"ar": "اقتصاد الواحة", "en": "Oasis economy"},
          "publisher": "هيئة إحصاء الواحة", "published_on": "2031-02-10", "page": 2}
QUESTION = "كم بلغت صادرات التمور من الواحة في عام 2030؟"


def _rule(config_dir) -> AttributionRule:
    return load_attribution_rule(config_dir)


def test_an_answer_naming_its_source_and_date_is_cited_with_the_sources_metadata(config_dir):
    answer = ("بلغت صادرات التمور من الواحة 812 مليون ريال في عام 2030، وفقًا لتقرير التجارة الخارجية للواحة 2030 "
              "الصادر عن هيئة إحصاء الواحة بتاريخ 2031-02-10.")
    [citation] = attribute(answer, [SOURCE], {"c1": CHUNK}, _rule(config_dir), question=QUESTION)
    assert citation["document_id"] == "d1" and citation["pages"] == [2]
    assert citation["kb"] == "eco" and citation["publisher"] == "هيئة إحصاء الواحة"
    assert citation["published_on"] == "2031-02-10" and citation["kb_name"]["ar"] == "اقتصاد الواحة"


def test_an_answer_naming_only_the_source_and_the_question_cites_nothing(config_dir):
    answer = "لا يذكر تقرير التجارة الخارجية للواحة 2030 الصادر عن هيئة إحصاء الواحة صادرات التمور في عام 2030."
    assert attribute(answer, [SOURCE], {"c1": CHUNK}, _rule(config_dir), question=QUESTION) == []


def test_a_file_source_without_metadata_attributes_as_before(config_dir):
    file = {"id": "c1", "ref": "c1", "document_id": "a1", "title": "تقرير.pdf", "kind": "doc", "page": 2}
    answer = "بلغت صادرات التمور 812 مليون ريال في عام 2030."
    [citation] = attribute(answer, [file], {"c1": CHUNK}, _rule(config_dir), question=QUESTION)
    assert citation["pages"] == [2] and "publisher" not in citation and "kb_name" not in citation


LABOR = "الإجازات\nتستحق العاملة إجازة أمومة مدتها عشرة أسابيع بأجر كامل."
REMOTE = "أيام العمل عن بعد\nيجوز للعامل العمل عن بعد يومين في الأسبوع بموافقة مديره المباشر."
L_SOURCE = {"id": "l1", "ref": "l1", "document_id": "dl", "title": "نظام العمل", "kind": "kb", "page": 2}
R_SOURCE = {"id": "r1", "ref": "r1", "document_id": "dr", "title": "دليل العمل عن بعد", "kind": "kb", "page": 2}


def test_one_sentence_joining_two_facts_with_an_arabic_comma_cites_both(config_dir):
    answer = "يجوز العمل عن بعد يومين في الأسبوع بموافقة المدير المباشر، وتستحق العاملة إجازة أمومة مدتها عشرة أسابيع."
    cited = attribute(answer, [L_SOURCE, R_SOURCE], {"l1": LABOR, "r1": REMOTE}, _rule(config_dir),
                      question="كم يوما يسمح بالعمل عن بعد، وكم مدة إجازة الأمومة؟")
    assert {c["document_id"] for c in cited} == {"dl", "dr"}


def test_an_english_answer_over_arabic_chunks_is_attributed_on_its_numbers(config_dir):
    july = "التوقعات\nيتوقع الصندوق الآن أن ينمو الناتج المحلي بنسبة 3.6 بالمئة في عام 2031."
    april = "التوقعات\nيتوقع الصندوق أن ينمو الناتج المحلي بنسبة 3.1 بالمئة في عام 2031."
    sources = [{"id": "j", "ref": "j", "document_id": "dj", "title": "يوليو", "kind": "kb", "page": 2,
                "published_on": "2031-07-20"},
               {"id": "a", "ref": "a", "document_id": "da", "title": "أبريل", "kind": "kb", "page": 2,
                "published_on": "2031-04-15"}]
    answer = "According to the July 2031 outlook (2031-07-20), growth is forecast at 3.6% in 2031."
    cited = attribute(answer, sources, {"j": july, "a": april}, _rule(config_dir),
                      question="What growth is forecast for 2031?")
    assert [c["document_id"] for c in cited] == ["dj"]
    # The question's and the sources' numbers alone cite nothing.
    assert attribute("The outlook of 2031-07-20 does not say.", sources, {"j": july, "a": april}, _rule(config_dir),
                     question="What growth is forecast for 2031?") == []


def test_two_sentences_from_two_documents_cite_both_whatever_their_weights(config_dir):
    unemployment = "سوق العمل\nيتوقع الصندوق أن ينخفض معدل البطالة في الواحة إلى 5.8 بالمئة بنهاية عام 2031."
    sources = [R_SOURCE, {"id": "u1", "ref": "u1", "document_id": "du", "title": "آفاق يوليو", "kind": "kb", "page": 3}]
    answer = ("يسمح بالعمل عن بعد يومين في الأسبوع وفقاً لدليل العمل عن بعد. "
              "ويتوقع الصندوق أن ينخفض معدل البطالة إلى 5.8 بالمئة بنهاية عام 2031.")
    cited = attribute(answer, sources, {"r1": REMOTE, "u1": unemployment}, _rule(config_dir),
                      question="كم يوما في الأسبوع يسمح بالعمل عن بعد، وما معدل البطالة المتوقع بنهاية 2031؟")
    assert {c["document_id"] for c in cited} == {"dr", "du"}


def test_one_document_keeps_the_single_floor(config_dir):
    """Two pages of one document: the neighbouring page that only repeats a phrase is not cited."""
    page2 = "المادة الثالثة: يستحق الموظف إجازة سنوية مدتها ثلاثون يوما براتب كامل عن كل سنة خدمة."
    page4 = "المادة التاسعة: يستحق الموظف إجازة مرضية مدتها ثلاثون يوما براتب كامل."
    sources = [{"id": "p2", "ref": "p2", "document_id": "d", "title": "لائحة", "kind": "doc", "page": 2},
               {"id": "p4", "ref": "p4", "document_id": "d", "title": "لائحة", "kind": "doc", "page": 4}]
    [citation] = attribute("مدة الإجازة السنوية ثلاثون يوما براتب كامل عن كل سنة خدمة.", sources,
                           {"p2": page2, "p4": page4}, _rule(config_dir), question="كم مدة الإجازة السنوية؟")
    assert citation["pages"] == [2]
