# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "pyyaml>=6"]
# ///
"""`make kb-fetch` (owner decision B4 on the report branch, ADR-0393): fetches the KB stats-gov's documents from
stats.gov.sa into kb-seed/stats-gov/, in development only, as kb-seed/stats-gov/fetch.yaml says: for each
of the owner's ten social-statistics categories, the most recent year its publications listing holds, and
in that year the latest edition of each publication (the listing is newest first; editions of one
publication share a title once its year and quarter words are taken out), its PDF and Excel files.

Each file is written with its sidecar (title, publisher, publication date, source URL, category) and
recorded in manifest.json (URL, retrieval time, SHA-256, size); the manifest also holds, per category, the
year taken, the files fetched and what failed and why. A file of an earlier run that is no longer selected
(a newer edition replaced it) is removed from the folder; `make kb-seed` then reports it, and the index
keeps it. Files dropped here by hand are never touched. The listing shows no publication date: the file's
Last-Modified date stands for it.

Refused unless PLATFORM_PROFILE is development, and under the airgap profile. Every request is bounded
(60 s; 20 MiB per file, the KB upload limit) and paced; the site's pages are read as robots.txt allows.

    uv run scripts/kb_fetch.py
"""

import hashlib
import html
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
import yaml

REPO = Path(__file__).resolve().parents[1]
FOLDER = REPO / "kb-seed" / "stats-gov"
MAX_BYTES = 20 * 1024 * 1024
TIMEOUT_S = 60.0
PAUSE_S = 1.0
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0 Safari/537.36 agent-platform-kb-fetch/1.0 (development)",
    "Accept-Language": "ar",
}
KINDS = {"pdf": ".pdf", "xlsx": ".xlsx"}
_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


@dataclass
class Item:
    title: str
    year: int | None
    periodicity: str
    files: list[tuple[str, str]]  # (url, icon kind: pdf, xlsx, xls, …)
    listing: str  # the sub-category (or category) listing it came from


@dataclass
class CategoryReport:
    name: str
    id: str
    listings: list[str] = field(default_factory=list)
    year: int | None = None
    files: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    note: str = ""


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def social_listings(page: str, tab_id: str) -> dict[str, list[tuple[str, str]]]:
    """The social tab's accordion: category id → [(sub-category id, name)], in the page's order."""
    start = page.find(f'id="parent-tab{tab_id}"')
    if start < 0:
        return {}
    end = page.find('class="tab-pane', start + 1)
    section = page[start: end if end > 0 else len(page)]
    found: dict[str, list[tuple[str, str]]] = {}
    current = None
    for m in re.finditer(r'id="category-collapse-(\d+)"|data-category-id="(\d+)"\s*>([^<]*)<', section):
        if m.group(1):
            current = m.group(1)
            found.setdefault(current, [])
        elif current:
            found[current].append((m.group(2), clean(m.group(3))))
    return found


def listing_items(page: str, listing: str) -> tuple[list[Item], int | None]:
    """The publications of one listing page (the results only, one copy of each) and the total it states."""
    at = page.find('id="search-results-display-list"')
    results = page[at:] if at >= 0 else ""
    items = []
    for block in re.split(r"<!-- Publication Search Result Item[^>]*-->", results)[1:]:
        block = block.split("<!-- View Details Button -->")[0]
        title = re.search(r'class="row-header">(.*?)</span>', block, re.S)
        if not title:
            continue
        cells = [clean(x) for x in re.findall(r'<span class="th">(.*?)</span>', block, re.S)]
        year = cells[0].translate(_INDIC) if cells else "-"
        files = [(html.unescape(h), kind) for h, kind in re.findall(
            r'href="([^"]+)"[^>]*>\s*<i class="dl-file-earmark-(\w+)-icon', block)]
        items.append(Item(clean(title.group(1)), int(year) if year.isdigit() else None,
                          cells[1] if len(cells) > 1 else "", files, listing))
    total = re.search(r'class="pagination-results"[^>]*>([^<]+)', page)
    numbers = [int(n) for n in re.findall(r"\d+", total.group(1).translate(_INDIC))] if total else []
    return items, (numbers[-1] if numbers else None)


_YEAR = re.compile(r"[0-9٠-٩]+(?:هـ|م)?")
_EDITION = re.compile(r"\b(?:للربع|الربع|الأول|الاول|الثاني|الثالث|الرابع|لعام|عام|نشرة|تقرير)\b")


def series(title: str) -> str:
    """A publication's editions share this key: the title without its year, quarter and edition words,
    its alef forms and spacing made one."""
    text = _EDITION.sub(" ", _YEAR.sub(" ", title))
    text = re.sub("[إأآ]", "ا", re.sub(r"[()\-–:،,.]", " ", text))
    return re.sub(r"\s+", " ", text).strip()


def select(items: list[Item]) -> tuple[int | None, list[Item]]:
    """The category's latest year, and in it the first (latest) item of each publication."""
    years = [i.year for i in items if i.year]
    if not years:
        return None, []
    latest = max(years)
    chosen, seen = [], set()
    for item in items:  # newest first, listing by listing
        key = (item.listing, series(item.title))
        if item.year == latest and key not in seen:
            seen.add(key)
            chosen.append(item)
    return latest, chosen


def file_name(url: str, disposition: str | None) -> str:
    name = None
    if disposition:
        m = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)", disposition)
        if m:
            name = unquote(m.group(1) or m.group(2))
    if not name:
        parts = [p for p in urlsplit(url).path.split("/") if p]
        name = unquote((parts[-2] if len(parts) >= 2 else parts[-1]).replace("+", " "))
    name = re.sub(r"_fixed_\d+$", "", name)
    stem, dot, ext = name.rpartition(".")
    stem = re.sub(r"[^\w\-. ()]+", "_", stem if dot else name).strip(" ._") or "document"
    return f"{stem[:120]}.{ext.lower()}" if dot else stem[:120]


def get(http: httpx.Client, url: str) -> httpx.Response:
    time.sleep(PAUSE_S)
    response = http.get(url, headers=HEADERS, follow_redirects=True)
    response.raise_for_status()
    return response


def download(http: httpx.Client, url: str) -> tuple[bytes, httpx.Headers]:
    time.sleep(PAUSE_S)
    with http.stream("GET", url, headers=HEADERS, follow_redirects=True) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_bytes():
            data += chunk
            if len(data) > MAX_BYTES:
                raise ValueError("over 20 MiB, the KB upload limit")
        return bytes(data), response.headers


def main() -> int:
    sys.path.insert(0, str(REPO / "scripts"))
    from get_token import env  # noqa: PLC0415

    cfg = env()
    if cfg.get("PLATFORM_PROFILE") != "development" or cfg.get("MCP_PROFILE") == "airgap":
        print("kb-fetch: development only (PLATFORM_PROFILE=development, not under airgap); nothing fetched")
        return 1
    spec = yaml.safe_load((FOLDER / "fetch.yaml").read_text(encoding="utf-8"))
    site = spec["site"].rstrip("/")
    manifest_path = FOLDER / "manifest.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {"documents": []}
    reports: list[CategoryReport] = []
    documents: list[dict] = []
    taken: set[str] = set()
    with httpx.Client(timeout=TIMEOUT_S) as http:
        accordion = social_listings(get(http, site + spec["social_tab"]).text, spec["social_tab_id"])
        for category in spec["categories"]:
            report = CategoryReport(category["name"], category["id"])
            reports.append(report)
            subs = accordion.get(category["id"]) or []
            listings = [(sid, name) for sid, name in subs] or [(category["id"], category["name"])]
            if category["id"] not in accordion:
                report.note = "not in the social tab's list; read from its own listing"
            elif not subs:
                report.note = "listed with no sub-category; read from its own listing"
            items: list[Item] = []
            for listing_id, listing_name in listings:
                report.listings.append(f"{listing_name} ({listing_id})")
                page = 1
                while True:
                    try:
                        text = get(http, site + spec["listing"].format(id=listing_id, page=page)).text
                    except httpx.HTTPError as exc:
                        report.failures.append({"listing": listing_id, "reason": f"listing: {type(exc).__name__} {exc}"[:200]})
                        break
                    found, total = listing_items(text, listing_name)
                    items += found
                    if not found or total is None or len([i for i in items if i.listing == listing_name]) >= total:
                        break
                    page += 1
            report.year, chosen = select(items)
            if not chosen:
                report.note = (report.note + "; " if report.note else "") + "no publication listed"
            for item in chosen:
                kinds = {kind for _, kind in item.files}
                for url, kind in item.files:
                    absolute = url if url.startswith("http") else site + url
                    if kind not in KINDS:
                        report.failures.append({"title": item.title, "url": absolute,
                                                "reason": f"a .{kind} file: KBs ingest PDF, DOCX, XLSX, TXT and MD"})
                        continue
                    try:
                        data, headers = download(http, absolute)
                    except (httpx.HTTPError, ValueError) as exc:
                        detail = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else str(exc)
                        report.failures.append({"title": item.title, "url": absolute, "reason": detail[:200]})
                        continue
                    name = file_name(absolute, headers.get("content-disposition"))
                    if not name.lower().endswith(KINDS[kind]):
                        name += KINDS[kind]
                    base, n = name, 2
                    while name in taken:
                        stem, _, ext = base.rpartition(".")
                        name, n = f"{stem}-{n}.{ext}", n + 1
                    taken.add(name)
                    modified = headers.get("last-modified")
                    published = (parsedate_to_datetime(modified).date() if modified else datetime.now(UTC).date()).isoformat()
                    title = item.title + (" (جداول Excel)" if kind == "xlsx" and "pdf" in kinds else "")
                    (FOLDER / name).write_bytes(data)
                    sidecar = {"title": title[:300], "publisher": spec["publisher"], "published_on": published,
                               "source_url": absolute, "category": category["name"], "subcategory": item.listing,
                               "year": item.year, "periodicity": item.periodicity,
                               "published_on_from": "the file's Last-Modified date (the listing shows none)"}
                    (FOLDER / (name + ".meta.yaml")).write_text(
                        yaml.safe_dump(sidecar, allow_unicode=True, sort_keys=False), encoding="utf-8")
                    digest = hashlib.sha256(data).hexdigest()
                    documents.append({"file": name, "url": absolute, "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
                                      "bytes": len(data), "sha256": digest, "category": category["name"],
                                      "subcategory": item.listing, "year": item.year, "title": title})
                    report.files.append(name)
                    print(f"  ok    {category['name']} {item.year}: {name} ({len(data)} bytes)")
    # A file an earlier run fetched that is no longer selected goes; files dropped by hand stay.
    for old in previous.get("documents") or []:
        if old.get("file") and old["file"] not in taken:
            for stale in (FOLDER / old["file"], FOLDER / (old["file"] + ".meta.yaml")):
                stale.unlink(missing_ok=True)
            print(f"  gone  {old['file']}: no longer the latest edition; make kb-seed reports it, the index keeps it")
    manifest = {
        "kb": "stats-gov",
        "source": site + "/ar/ (الإحصاءات → الإحصاءات الاجتماعية)",
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "categories": [report.__dict__ for report in reports],
        "documents": documents,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\nkb-fetch: per category")
    for r in reports:
        print(f"- {r.name}: year {r.year or '-'}, {len(r.files)} file(s), {len(r.failures)} failure(s)"
              + (f" — {r.note}" if r.note else ""))
        for failure in r.failures:
            print(f"    failed: {failure.get('title', failure.get('listing'))}: {failure['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
