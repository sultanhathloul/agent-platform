# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "pyyaml>=6"]
# ///
"""`make kb-poc` (phase 08, D4, ADR-0331): seeds the proof-of-concept knowledge base «الاقتصاد الدولي» in a
development stack from the public sources of scripts/kb_poc/sources.yaml.

Development only: refused under the airgap profile (MCP_PROFILE=airgap). Downloads land in
build/kb-poc/ (git-ignored) and are never committed; scripts/kb_poc/manifest.json records each source's
URL, retrieval time, size and SHA-256 and is committed instead. Each download is bounded (30 s, 20 MiB,
the upload limit). HTML is kept as its visible text, as Markdown, since KB ingestion reads PDF, DOCX,
XLSX, TXT and MD. The KB and its documents are created through the gateway as the admin, with their
source metadata (D3); a document already there under the same title is kept. A site that refuses a
script (a bot check, as imf.org does) is never worked around: the owner saves that page from a browser
into build/kb-poc/manual/<key>.pdf or .md, and the run uses it."""

import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from get_token import env, login  # noqa: E402

SOURCES = REPO / "scripts" / "kb_poc" / "sources.yaml"
MANIFEST = REPO / "scripts" / "kb_poc" / "manifest.json"
OUT = REPO / "build" / "kb-poc"
# A source a site refuses to a script (a bot check) may be saved here from a browser as <key>.pdf or
# <key>.md; it is used as is, never fetched, and its hash recorded the same way.
MANUAL = OUT / "manual"
MAX_BYTES = 20 * 1024 * 1024
TIMEOUT_S = 30.0
HEADERS = {"User-Agent": "agent-platform-kb-poc/1.0 (development; public sources only)"}


class _Text(HTMLParser):
    """The visible text of a page: headings and paragraphs, scripts, styles and navigation left out."""

    SKIP = {"script", "style", "nav", "header", "footer", "noscript", "svg", "form"}
    BLOCK = {"p", "h1", "h2", "h3", "h4", "li", "tr", "br", "div", "section", "article", "td"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skipping = 0
        self.parts: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag in ("h1", "h2", "h3") and not self.skipping:
            self.parts.append("## ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skipping:
            self.skipping -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skipping:
            self.parts.append(data)

    def markdown(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line and line != "##").strip() + "\n"


def fetch(http: httpx.Client, source: dict) -> tuple[Path, bytes, str]:
    for kind in ("pdf", "md"):
        saved = MANUAL / f"{source['key']}.{kind}"
        if saved.is_file():
            data = saved.read_bytes()
            if len(data) > MAX_BYTES:
                raise ValueError("over 20 MiB")
            return saved, data, kind
    with http.stream("GET", source["url"], headers=HEADERS, follow_redirects=True) as response:
        response.raise_for_status()
        data = b""
        for chunk in response.iter_bytes():
            data += chunk
            if len(data) > MAX_BYTES:
                raise ValueError("over 20 MiB")
        kind = response.headers.get("content-type", "")
    if data[:5] == b"%PDF-":
        path = OUT / f"{source['key']}.pdf"
        path.write_bytes(data)
        return path, data, "pdf"
    parser = _Text()
    parser.feed(data.decode(response.encoding or "utf-8", errors="replace"))
    text = parser.markdown()
    if len(text) < 200:
        raise ValueError(f"too little text ({len(text)} characters, {kind})")
    path = OUT / f"{source['key']}.md"
    stored = f"# {source['title']}\n\n{source['url']}\n\n{text}".encode()
    path.write_bytes(stored)
    return path, stored, "md"


def main() -> int:
    cfg = env()
    if cfg.get("MCP_PROFILE") == "airgap":
        print("kb-poc: refused under the airgap profile (D4: development only)")
        return 1
    plan = yaml.safe_load(SOURCES.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    gateway = cfg["GATEWAY_PUBLIC_URL"]
    admin = {"Authorization": f"Bearer {login('admin', 'admin')['access_token']}"}
    kb = plan["kb"]
    entries: list[dict] = []
    with httpx.Client(timeout=TIMEOUT_S) as http:
        created = http.post(f"{gateway}/v1/admin/kbs", headers=admin,
                            json={"slug": kb["slug"], "name": kb["name"], "description": kb["description"]})
        if created.status_code not in (201, 409):
            print(f"kb-poc: creating {kb['slug']} failed: {created.status_code} {created.text[:200]}")
            return 1
        listed = http.get(f"{gateway}/v1/admin/kbs/{kb['slug']}/documents", headers=admin)
        listed.raise_for_status()
        held = {d["metadata"]["title"]: d["id"] for d in listed.json()["data"]["documents"] if d["status"] != "failed"}
        ids: dict[str, str] = {}
        for source in plan["sources"]:
            entry = {"key": source["key"], "url": source["url"], "title": source["title"]}
            if not source.get("url") or not source.get("published_on"):
                entry["status"] = "not_listed_yet"
                entries.append(entry)
                print(f"  skip  {source['key']}: no exact page written yet ({source['publisher']})")
                continue
            try:
                path, data, kind = fetch(http, source)
            except (httpx.HTTPError, ValueError) as exc:
                detail = f"{exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else str(exc)[:120]
                entry.update(status="fetch_failed", error=f"{type(exc).__name__}: {detail}",
                             retrieved_at=datetime.now(UTC).isoformat(timespec="seconds"))
                entries.append(entry)
                print(f"  FAIL  {source['key']}: {entry['error']}; save it from a browser as "
                      f"{(MANUAL / source['key']).relative_to(REPO)}.pdf (or .md) and run again")
                continue
            entry.update(retrieved_at=datetime.now(UTC).isoformat(timespec="seconds"), bytes=len(data),
                         sha256=hashlib.sha256(data).hexdigest(), stored_as=kind,
                         retrieved_by="saved from a browser" if path.parent == MANUAL else "fetched")
            if source["title"] in held:
                ids[source["key"]] = held[source["title"]]
                entry["status"] = "already_in_kb"
                entries.append(entry)
                print(f"  keep  {source['key']}")
                continue
            fields = {"title": source["title"], "publisher": source["publisher"], "published_on": source["published_on"],
                      "source_url": source["url"]}
            if source.get("supersedes") and source["supersedes"] in ids:
                fields["supersedes"] = ids[source["supersedes"]]
            with path.open("rb") as handle:
                response = http.post(f"{gateway}/v1/admin/kbs/{kb['slug']}/documents", headers=admin, data=fields,
                                     files={"file": (path.name, handle)})
            if response.status_code != 201:
                entry.update(status="upload_refused", error=response.text[:200])
                entries.append(entry)
                print(f"  FAIL  {source['key']}: upload {response.status_code}")
                continue
            ids[source["key"]] = response.json()["data"]["id"]
            entry["status"] = "uploaded"
            entries.append(entry)
            print(f"  ok    {source['key']} ({kind}, {len(data)} bytes)")
        # Wait for the uploads to finish indexing, then record each one's status.
        deadline = time.monotonic() + 300
        status: dict[str, str] = {}
        while time.monotonic() < deadline:
            listed = http.get(f"{gateway}/v1/admin/kbs/{kb['slug']}/documents", headers=admin)
            status = {d["id"]: d["status"] for d in listed.json()["data"]["documents"]}
            if all(status.get(i) in ("indexed", "failed") for i in ids.values()):
                break
            time.sleep(2)
        for entry in entries:
            if entry["key"] in ids:
                entry["index_status"] = status.get(ids[entry["key"]], "unknown")
    MANIFEST.write_text(json.dumps({"kb": kb["slug"], "sources": entries,
                                    "upload_by_subscription": plan.get("upload_by_subscription", [])},
                                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"kb-poc: manifest written to {MANIFEST.relative_to(REPO)}; content stays in {OUT.relative_to(REPO)} (not committed)")
    print("kb-poc: upload by subscription (never fetched): " + ", ".join(plan.get("upload_by_subscription", [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
