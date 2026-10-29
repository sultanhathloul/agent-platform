# «الاقتصاد الدولي» / International economy

The phase 08 proof-of-concept KB (D4, ADR-0331), moved here from `make kb-poc` (retired) by owner decision B
on the report branch (ADR-0392). `make kb-seed` (run by `make up`) uploads every file of this folder that the
KB does not already hold, by SHA-256. This README, `kb.yaml`, `manifest.json` and the `*.meta.yaml` sidecars
are not documents.

To add a source: save it here (PDF, DOCX, XLSX, TXT or MD), add `<file>.meta.yaml` beside it (title,
publisher, published_on, source_url, optionally supersedes: <the earlier file's name>) and its line in
`manifest.json` (url, retrieved_at, sha256), then commit and run `make kb-seed`.

Not here yet (listed by the owner in phase 08; `make kb-poc` could not fetch them):
- imf.org refuses scripts (403): "IMF Executive Board Concludes 2025 Article IV Consultation with the People's
  Republic of China" (2026-02-18), the China 2025 Article IV Staff Report (Country Report No. 26/44,
  2026-02-17), World Economic Outlook April 2026 (2026-04-14) and its July 2026 Update (2026-07-08,
  supersedes April). Save each from a browser into this folder with its sidecar.
- Sites named without an exact page: stats.gov.cn, english.gov.cn, english.scio.gov.cn, fmprc.gov.cn,
  mfa.gov.cn, SPA, SAMA, PIF, Aramco, qna.org.qa, bricspolicycenter.org, sab.com, the Permanent Mission of
  China to the UN.
- By subscription only, never fetched: Moody's, Fitch Ratings, S&P Global Ratings, Bloomberg.
