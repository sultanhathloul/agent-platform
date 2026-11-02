Standalone PR (owner decision): report awareness, a personal report library, long-conversation recall, and a memory fix. Branch: feat/report-library, from the latest main. Commit, push, open the MR; never merge. Read CLAUDE.md, docs/DECISIONS.md, docs/PROGRESS.md first. Threat model: ADR-0054. First commit: this prompt as docs/REPORT-LIBRARY-PROMPT.md and a PROGRESS section with baseline, done-check and checklist. New ADRs continue from the last number. If this message arrives cut off, say so and wait for the rest. Evidence: conversation 14d0ebf8-1ad3-4f85-a31c-15992ceb5f76.

Problems seen:
- The assistant could not answer about a report it built in the same conversation ("ابحث في اللوحة", "وش التقارير اللي اشتغلنا عليها"), because history shows a report only as «[تقرير: <title>]».
- «منهم أساطير كرة القدم المذكورين في التقرير السابق» ended in validation_failed.
- A table the user pasted early in the conversation was lost later.
- Memory stored «يعمل في إدارة التخطيط», a detail the model invented in an earlier memo, as a fact about the user.

OWNER DECISIONS (ADRs, decided):
1. In-conversation awareness: every turn's context carries a compact index of this conversation's reports (title, latest version, section titles, KPI labels and values, table headers), within a token budget, never folded into the summary. The history marker stays (the model must not imitate report pages) but now carries this digest.
2. A tool report_read(report_id, version?) returns a report's spec as readable text (sections, values, table rows, sources). The assistant answers questions about a report's content from it and cites «من التقرير: <title> (الإصدار n)». A question about a report is a text answer, never an edit, unless the user asks to change the report.
3. Personal report library: every report version is indexed (title, subject, sections, KPI labels and values, sources, date, owner) in the platform DB. A tool report_search(query) searches only the caller's own reports, checked at the gateway AND the agent. In any conversation the user can ask about an earlier report, have it read, or ask for a new version based on it in the current conversation (its spec copied as the base, the original untouched). Existing reports are indexed by a backfill.
4. A web page «تقاريري» / "My reports": list, search, open in the side panel, download, filter by date and source; Arabic first, both themes.
5. Long conversations: older turns stay summarized for context, but a tool conversation_recall(query) searches this conversation's full stored history (owner only) and returns matching earlier messages verbatim, so details lost to summarization can be recovered. Summaries must keep named entities and the existence of pasted tables or lists.
6. Memory fix: long-term memory stores only facts the user stated in their own messages, never content the assistant generated (memos, reports, answers). Remove the stored «إدارة التخطيط» fact. Tests: a memo naming a department creates no memory; a user's own statement still does.
7. Conversation limits: find and report every limit on conversation length in the code and config (message count, checkpoint size, summary size, history query size, UI rendering), measure a 60-turn conversation (context tokens per turn, turn latency at turns 10, 30 and 60, checkpoint size), and fix anything that grows without bound or slows turns as a conversation lengthens.

Rule 12 for the new tools (bounded, owner-scoped, size-capped outputs). Negative tests: no tool returns another user's reports or conversation.

HEAVY SCENARIO (an end-to-end script run through the API as analyst, kept as a repeatable eval `long_conversation`, transcript saved):
- Conversation A, at least 60 turns, mixing: general questions; KB questions with stats-gov toggled; early on, the user pastes a 5-row table; a dashboard over stats-gov, then two edits (v2, v3); a general-knowledge report with nothing toggled; a KB report on an unrelated subject (nothing-found reply); questions about the reports' content («ابحث في اللوحة عن …», «وش التقارير اللي اشتغلنا عليها؟»); after the summarization threshold, questions about the pasted table and about a figure from turn 3.
- Conversation B, new: «التقرير اللي سويته عن سوق العمل، وش نسبة البطالة فيه؟», then «سوّ لي نسخة جديدة منه وأضف رسمًا للمشاركة في قوة العمل».
- As viewer: report_search and conversation_recall find nothing of analyst's.
- Memory: after the scenario, long-term memory holds no fact the user did not state.
Assert each step; report a table of steps with pass or fail and the evidence.

FINISH: make test; make eval for routing, reports, memory and long_conversation once; make smoke; record per CLAUDE.md rule 11. List pages for the owner's visual check. Push; open the MR. Do not touch docs/HANDOFF.md.
