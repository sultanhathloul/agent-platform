# Owner decisions on the report branch (2026-10-29), verbatim

Owner decisions on branch feat/reports, in their own commits (ADRs):

A. Report figure labels: replace the «غير موثق» / "Unverified" marker everywhere (kit, prompts, figure check, evals, messages) with a source label: figures from general model knowledge read «من المعرفة العامة» / "General knowledge"; figures from a knowledge base read «من: <KB name>» / "From: <KB name>"; figures from an attached file read «من: <file title>». The marker stays visible and every figure keeps exactly one label. New ADR superseding the wording of the earlier decision.

B. Persistent seeded knowledge bases:
1. Folder kb-seed/<slug>/ per KB: kb.yaml (Arabic and English name, description, realm role kb:<slug>:read, which dev users get it), the documents, one metadata sidecar per document (title, publisher, publication date, source URL, optional supersedes) and a manifest (URL, retrieved date, sha256). Everything is committed to git, documents included.
2. A seed step that runs automatically after make up (and on demand with make kb-seed): creates each KB if missing, ingests every document not already indexed (by sha256), skips the rest, idempotent, works offline. A document dropped into the folder by hand, without a sidecar, is ingested with its file name as title and flagged "metadata missing" on its admin page.
3. Dev users: add kb:stats-gov:read for analyst and evaluator to infra/keycloak/realm-export.json, so a fresh stack needs no kcadm.
4. KB stats-gov «إحصاءات الهيئة العامة للإحصاء» / "GASTAT statistics": `make kb-fetch` downloads, in development only, from https://www.stats.gov.sa/ar/ (section الإحصاءات → الإحصاءات الاجتماعية) these categories only: إحصاءات سوق العمل، إحصاءات الصحة، إحصاءات النوع الاجتماعي والتنوع، إحصاءات حيوية وسكانية، إحصاءات أساليب الحياة، إحصاءات الأحوال المعيشية، إحصاءات الرياضة، إحصاءات المساكن، إحصاءات التعليم، إحصاءات الخدمات الاجتماعية. In each category take only the most recent year available (for example 2025 when 2022 to 2025 exist), and within that year the latest edition of each publication; prefer PDF and Excel. Record each document's category in its sidecar. Report per category: the year taken, the files fetched, and what failed and why. List failures in PROGRESS as "download by hand into kb-seed/stats-gov/".
5. Move the phase 08 POC KB «الاقتصاد الدولي» to the same mechanism (kb-seed/intl-economy/) and retire make kb-poc.
6. Tests: seeding is idempotent; a new file is picked up; a removed file is reported, not deleted from the index silently; a fresh `make clean && make up` shows both KBs ready to analyst with no manual step.

C. Development model info on reports: a small footer strip at the end of every report shows the model id, provider, tier alias, generation time and input/output tokens of the call that wrote it, and of any call that produced its findings. Controlled by one setting (config/artifacts.yaml show_model_info: true in development, false in the production profile); never shown when false. Included in the downloaded file when shown. Test both settings.

D. Script check on reports: report_b276fb00 v2 contained Chinese characters inside Arabic text («أندريه في拉斯»). Apply the same allowed-script validation used for text answers to the report's visible text and report-data; on failure, regenerate once with the failure named in the prompt, then fail with the existing validation message. Test with a report containing CJK in Arabic.

Then make test, make smoke, and the reports eval suite (rule 13: report prompts and checks changed); record per CLAUDE.md rule 11. Push. Do not touch docs/HANDOFF.md.

# Owner decisions on PR #17 (2026-10-29, second message), verbatim

Owner decisions, on PR #17 in their own commits (ADRs):
1. A capability floor only raises a tier, never lowers it: the tier for any call is max(the role's default tier, the floors of the capabilities it runs for). Fix the resolver (core change approved) and README §19.3 if its wording says otherwise. Tests: report_writer over a kb turn resolves to heavy; a role whose default is below a floor is still raised to the floor; the tier event's reason names both. Remove the BACKLOG line for this finding.
2. «من: طلبك» / "From: your request" is accepted.
3. kb-fetch takes the latest edition of each publication series, not the latest year of the whole category: every series in the ten categories keeps its own newest edition (for example health status 2025 beside the 2026 series). Re-run make kb-fetch, commit the added documents with sidecars, and report per category the series and the edition taken.
4. A report's title, headings and labels follow the language of the request (English request → English title), whatever the sources' language. Add the two eval cases that failed this as gated.
Then from a clean stack: make test, make smoke, and the reports and routing eval suites (rule 13: routing and report prompts changed); record per CLAUDE.md rule 11. Push. Do not touch docs/HANDOFF.md.
