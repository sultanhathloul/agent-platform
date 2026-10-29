# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "pyyaml>=6"]
# ///
"""`make kb-fetch` (owner decision B4 on the report branch, ADR-0393): fetches the KB stats-gov's documents from
stats.gov.sa into kb-seed/stats-gov/, in development only, as kb-seed/stats-gov/fetch.yaml says: for each
of the owner's ten social-statistics categories, the newest edition of every publication series it lists
(owner decision 3 on PR #17, ADR-0395; editions of one series share a title once its year, quarter and
edition words are taken out, a quarterly series apart from an annual one), its PDF and Excel files.

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
import random
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
ATTEMPTS = 3
BACKOFF_S = 5.0
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0 Safari/537.36 agent-platform-kb-fetch/1.0 (development)",
    "Accept-Language": "ar",
}
KINDS = {"pdf": ".pdf", "xlsx": ".xlsx"}
MAGIC = {"pdf": b"%PDF", "xlsx": b"PK\x03\x04"}
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
    year: int | None = None  # the newest year among the series taken
    series: list[dict] = field(default_factory=list)  # each series and the edition taken
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
    text = re.sub("[\u064b-\u0652\u0640]", "", title)  # harakat and tatweel: «السجليَّة» is «السجلية»
    text = _EDITION.sub(" ", _YEAR.sub(" ", text))
    text = re.sub("[إأآ]", "ا", re.sub(r"[()\-–:،,.]", " ", text))
    return re.sub(r"\s+", " ", text).strip()


_QUARTER = re.compile(r"الربع|للربع")


def series_key(item: Item) -> tuple[str, str, bool]:
    """One publication series: its listing, its title without year and edition words, and whether it is
    a quarterly edition (an annual index and its quarterly editions share a title otherwise)."""
    return item.listing, series(item.title), bool(_QUARTER.search(item.title))


def select(items: list[Item]) -> list[Item]:
    """The newest edition of every publication series (owner decision 3 on PR #17, ADR-0395): the item of
    the highest year in each series, the first in the listing's newest-first order among equals; an item
    with no year stands for its series only when the series has no dated edition."""
    chosen: dict[tuple, Item] = {}
    for item in items:
        key = series_key(item)
        held = chosen.get(key)
        if held is None or (item.year or 0) > (held.year or 0):
            chosen[key] = item
    return list(chosen.values())


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


def retried(call):
    """A timeout, a connection error or a 5xx is tried again, at most ATTEMPTS times in all, with
    exponential backoff and jitter (CLAUDE.md rule 12g); anything else at once."""
    for attempt in range(ATTEMPTS):
        time.sleep(PAUSE_S if attempt == 0 else BACKOFF_S * 2 ** (attempt - 1) * random.uniform(0.5, 1.5))
        try:
            return call()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500 or attempt == ATTEMPTS - 1:
                raise
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt == ATTEMPTS - 1:
                raise
    raise AssertionError("unreachable")


def get(http: httpx.Client, url: str) -> httpx.Response:
    def once() -> httpx.Response:
        response = http.get(url, headers=HEADERS, follow_redirects=True)
        response.raise_for_status()
        return response

    return retried(once)


def download(http: httpx.Client, url: str) -> tuple[bytes, httpx.Headers]:
    return retried(lambda: _download(http, url))


def _download(http: httpx.Client, url: str) -> tuple[bytes, httpx.Headers]:
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
    kept: dict[str, str] = {}  # sha256 → file name, so a file listed in two categories is written once
    with httpx.Client(timeout=TIMEOUT_S) as http:
        try:
            accordion = social_listings(get(http, site + spec["social_tab"]).text, spec["social_tab_id"])
            tab_note = ""
        except httpx.HTTPError as exc:
            # The tab page could not be read: the sub-categories fetch.yaml recorded stand in, said so.
            accordion = {c["id"]: [(s["id"], s["name"]) for s in c.get("subcategories") or []]
                         for c in spec["categories"] if c.get("subcategories") is not None}
            tab_note = f"the social tab did not answer ({type(exc).__name__}); its sub-categories from fetch.yaml"
            print(f"kb-fetch: {tab_note}")
        for category in spec["categories"]:
            report = CategoryReport(category["name"], category["id"])
            report.note = tab_note
            reports.append(report)
            subs = accordion.get(category["id"]) or []
            listings = [(sid, name) for sid, name in subs] or [(category["id"], category["name"])]
            if category["id"] not in accordion:
                report.note = "; ".join(filter(None, [report.note, "not in the social tab's list; read from its own listing"]))
            elif not subs:
                report.note = "; ".join(filter(None, [report.note, "listed with no sub-category; read from its own listing"]))
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
            chosen = select(items)
            report.year = max((i.year for i in chosen if i.year), default=None)
            report.series = [{"series": series(i.title), "edition": i.title, "year": i.year, "listing": i.listing,
                              "files": [kind for _, kind in i.files]} for i in chosen]
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
                    if not data.startswith(MAGIC[kind]):
                        # The link's icon says one kind, the bytes another (an old .xls behind an Excel icon).
                        report.failures.append({"title": item.title, "url": absolute,
                                                "reason": f"not a {kind} file (its first bytes {data[:4]!r}); KBs ingest no .xls"})
                        continue
                    digest = hashlib.sha256(data).hexdigest()
                    if digest in kept:
                        # The same file under a second category (a report listed twice): kept once.
                        report.files.append(kept[digest])
                        print(f"  same  {category['name']}: {item.title} is {kept[digest]}, already fetched")
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
                    kept[digest] = name
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
            print(f"  gone  {old['file']}: no longer selected (a newer edition, or refused); make kb-seed reports it, the index keeps it")
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
        print(f"- {r.name}: {len(r.series)} series, {len(r.files)} file(s), {len(r.failures)} failure(s)"
              + (f" — {r.note}" if r.note else ""))
        for taken in r.series:
            print(f"    {taken['edition']} ({taken['year'] or '-'}; {', '.join(taken['files']) or 'no file'})")
        for failure in r.failures:
            print(f"    failed: {failure.get('title', failure.get('listing'))}: {failure['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
