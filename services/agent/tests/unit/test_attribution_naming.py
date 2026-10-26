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


def test_a_boilerplate_clause_does_not_cite_a_neighbouring_page_before_the_clause_with_the_figure(config_dir):
    # The make-eval failure of 2026-10-26 (cross-probation-fertilizer-gulf, ADR-0357): «في عام 2030» ties
    # pages 2 and 3 of the trade report; the clause holding 455 decides the report's page.
    probation = "التجربة\nلا تزيد فترة التجربة على تسعين يوما، ويجوز تمديدها مرة واحدة باتفاق مكتوب."
    partners = "الشركاء التجاريون\nكانت جمهورية السهل أكبر شريك تجاري للواحة في عام 2030 بحصة 18 بالمئة من التجارة."
    sources = [{"id": "p", "ref": "p", "document_id": "dl", "title": "نظام العمل في الواحة", "kind": "kb", "page": 3},
               {"id": "t3", "ref": "t3", "document_id": "d1", "title": SOURCE["title"], "kind": "kb", "page": 3},
               {**SOURCE, "id": "t2", "ref": "t2"}]
    answer = ("تحدد وزارة الموارد البشرية في الواحة في نظام العمل أن فترة التجربة لا تزيد على تسعين يوماً، ويجوز تمديدها "
              "مرة واحدة باتفاق مكتوب. أما صادرات الأسمدة من الواحة في عام 2030، فقد بلغت 455 مليون ريال وفقاً لتقرير "
              "التجارة الخارجية للواحة الصادر عن هيئة إحصاء الواحة في 2031-02-10.")
    cited = attribute(answer, sources, {"p": probation, "t3": partners, "t2": CHUNK}, _rule(config_dir),
                      question="كم مدة التجربة للموظف الجديد، وكم كانت صادرات الأسمدة من الواحة سنة 2030؟")
    assert {c["document_id"]: c["pages"] for c in cited} == {"dl": [3], "d1": [2]}


def test_the_words_introducing_a_source_do_not_dilute_the_claim(config_dir):
    # make-eval-20261026T140012Z, cross-remote-unemployment-ar (ADR-0358): «وفقاً لـ … الصادر عن … بتاريخ»
    # left «يومين» a quarter of the claim, below min_new_word_coverage.
    remote = {**R_SOURCE, "publisher": "وزارة الموارد البشرية في الواحة", "published_on": "2031-03-05",
              "title": "دليل العمل عن بعد"}
    answer = ("يسمح بالعمل عن بعد يومين في الأسبوع وفقاً لدليل العمل عن بعد الصادر عن وزارة الموارد البشرية في الواحة "
              "بتاريخ 2031-03-05.")
    [citation] = attribute(answer, [remote], {"r1": REMOTE}, _rule(config_dir),
                           question="كم يوما في الأسبوع يسمح بالعمل عن بعد؟")
    assert citation["document_id"] == "dr" and citation["pages"] == [2]
    without = _rule(config_dir).model_copy(update={"source_phrasing": []})
    assert attribute(answer, [remote], {"r1": REMOTE}, without, question="كم يوما في الأسبوع يسمح بالعمل عن بعد؟") == []
