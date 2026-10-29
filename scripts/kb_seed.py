# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "pyyaml>=6"]
# ///
"""`make kb-seed`, which `make up` runs (owner decision B on the report branch, ADR-0392): the knowledge bases
of kb-seed/<slug>/, committed with their documents, made present in the running stack.

For each folder: the KB is created through the gateway as the admin when it is missing (the gateway makes
its realm role, which infra/keycloak/realm-export.json already gives the development users of kb.yaml);
every document whose SHA-256 the KB does not hold is uploaded with its sidecar's source metadata
(`<file>.meta.yaml`: title, publisher, published_on, source_url, optionally supersedes: <an earlier file of
the folder>); the rest is skipped. A file without a sidecar is uploaded with its file name as its title,
kb.yaml's `default_publisher` and the file's date, flagged `metadata_missing` for its admin page. A document
the KB holds whose file is no longer in the folder is reported, never deleted. Then the run waits, bounded,
until what it uploaded is indexed or failed, and says which.

Idempotent and offline: it reads the folder and talks to the local gateway and Keycloak only. The gateway
computes each stored file's SHA-256 (ADR-0392); a document registered before that has none and is matched
by its title instead.

    uv run scripts/kb_seed.py [--only <slug>] [--wait-s 900]
"""

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "kb-seed"
# Files of a KB folder that are not documents.
RESERVED = {"kb.yaml", "manifest.json", "fetch.yaml", "README.md"}
SIDECAR = ".meta.yaml"
KINDS = {".pdf", ".docx", ".xlsx", ".txt", ".md"}
TIMEOUT_S = 60.0
# A 429 (too many uploads in flight, or the per-minute rate): wait and try again, a bounded number of times.
BUSY_RETRIES = 30
BUSY_WAIT_S = 3.0


@dataclass
class SeedDocument:
    path: Path
    sha256: str
    title: str
    publisher: str
    published_on: str
    source_url: str | None = None
    supersedes: str | None = None  # an earlier file's name in the same folder
    metadata_missing: bool = False

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class SeedKb:
    folder: Path
    slug: str
    name: dict
    description: dict
    role: str
    users: list[str]
    documents: list[SeedDocument] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)  # files of a kind KBs cannot ingest


@dataclass
class Plan:
    upload: list[SeedDocument] = field(default_factory=list)
    held: list[tuple[SeedDocument, dict]] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)  # in the KB, no longer in the folder: reported only


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def load_kb(folder: Path) -> SeedKb:
    meta = yaml.safe_load((folder / "kb.yaml").read_text(encoding="utf-8"))
    kb = SeedKb(folder=folder, slug=meta["slug"], name=meta["name"], description=meta["description"],
                role=meta["role"], users=list(meta.get("users") or []))
    if kb.slug != folder.name or kb.role != f"kb:{kb.slug}:read":
        raise ValueError(f"{folder}: slug must be the folder's name and role kb:<slug>:read")
    default_publisher = str(meta.get("default_publisher") or "غير محدد")
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.name in RESERVED or path.name.endswith(SIDECAR) or path.name.startswith("."):
            continue
        if path.suffix.lower() not in KINDS:
            kb.ignored.append(path.name)
            continue
        sidecar = path.with_name(path.name + SIDECAR)
        if sidecar.is_file():
            side = yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {}
            kb.documents.append(SeedDocument(
                path=path, sha256=sha256(path), title=str(side["title"]), publisher=str(side["publisher"]),
                published_on=str(side["published_on"]), source_url=side.get("source_url") or None,
                supersedes=side.get("supersedes") or None,
            ))
        else:
            day = datetime.fromtimestamp(path.stat().st_mtime, UTC).date().isoformat()
            kb.documents.append(SeedDocument(path=path, sha256=sha256(path), title=path.name[:300],
                                             publisher=default_publisher, published_on=day, metadata_missing=True))
    names = {d.name for d in kb.documents}
    for doc in kb.documents:
        if doc.supersedes and doc.supersedes not in names:
            raise ValueError(f"{doc.path}: supersedes {doc.supersedes!r}, which is not a document of {folder.name}")
    return kb


def ordered(documents: list[SeedDocument]) -> list[SeedDocument]:
    """Each earlier version before the document that supersedes it, otherwise the folder's order."""
    by_name = {d.name: d for d in documents}
    out: list[SeedDocument] = []

    def visit(doc: SeedDocument, seen: tuple[str, ...] = ()) -> None:
        if doc in out:
            return
        if doc.name in seen:
            raise ValueError(f"supersedes loop at {doc.name}")
        if doc.supersedes:
            visit(by_name[doc.supersedes], (*seen, doc.name))
        out.append(doc)

    for doc in documents:
        visit(doc)
    return out


def plan(kb: SeedKb, held: list[dict]) -> Plan:
    """What to upload, what the KB already holds, and what it holds that the folder no longer has."""
    by_sha = {d["sha256"]: d for d in held if d.get("sha256")}
    legacy = {d["metadata"]["title"]: d for d in held if not d.get("sha256")}
    out = Plan()
    matched: set[str] = set()
    seen: set[str] = set()
    for doc in ordered(kb.documents):
        if doc.sha256 in seen:
            continue  # the same file twice in the folder: one document
        seen.add(doc.sha256)
        found = by_sha.get(doc.sha256) or legacy.get(doc.title)
        if found is None:
            out.upload.append(doc)
        else:
            out.held.append((doc, found))
            matched.add(found["id"])
    out.removed = [d for d in held if d["id"] not in matched]
    return out


class Gateway:
    def __init__(self, http: httpx.Client, base: str, token: str):
        self.http, self.base = http, base.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}

    def ensure_kb(self, kb: SeedKb) -> str:
        """created, present, or why not."""
        got = self.http.get(f"{self.base}/v1/admin/kbs/{kb.slug}", headers=self.headers)
        if got.status_code == 200:
            return "present"
        made = self.http.post(f"{self.base}/v1/admin/kbs", headers=self.headers,
                              json={"slug": kb.slug, "name": kb.name, "description": kb.description})
        if made.status_code == 201:
            return "created"
        raise RuntimeError(f"creating {kb.slug}: {made.status_code} {made.text[:200]}")

    def documents(self, slug: str) -> list[dict]:
        listed = self.http.get(f"{self.base}/v1/admin/kbs/{slug}/documents", headers=self.headers)
        listed.raise_for_status()
        return listed.json()["data"]["documents"]

    def upload(self, slug: str, doc: SeedDocument, supersedes: str | None) -> dict:
        fields = {"title": doc.title, "publisher": doc.publisher, "published_on": doc.published_on}
        if doc.source_url:
            fields["source_url"] = doc.source_url
        if supersedes:
            fields["supersedes"] = supersedes
        if doc.metadata_missing:
            fields["metadata_missing"] = "true"
        for _ in range(BUSY_RETRIES):
            with doc.path.open("rb") as handle:
                answered = self.http.post(f"{self.base}/v1/admin/kbs/{slug}/documents", headers=self.headers,
                                          data=fields, files={"file": (doc.name, handle)})
            if answered.status_code != 429:
                break
            time.sleep(BUSY_WAIT_S)
        if answered.status_code != 201:
            raise RuntimeError(f"{answered.status_code} {answered.text[:200]}")
        return answered.json()["data"]


def seed(gateway: Gateway, kb: SeedKb, *, wait_s: float, out=print) -> dict:
    """One KB; returns its counts for the summary."""
    state = gateway.ensure_kb(kb)
    held = gateway.documents(kb.slug)
    work = plan(kb, held)
    out(f"kb-seed: {kb.slug} ({state}): {len(kb.documents)} file(s), {len(work.held)} already in the KB, "
        f"{len(work.upload)} to upload")
    for name in kb.ignored:
        out(f"  skip    {name}: not a kind a KB ingests (pdf, docx, xlsx, txt, md)")
    ids = {doc.name: found["id"] for doc, found in work.held}
    uploaded: dict[str, SeedDocument] = {}
    failed_uploads = 0
    for doc in work.upload:
        try:
            view = gateway.upload(kb.slug, doc, ids.get(doc.supersedes) if doc.supersedes else None)
        except (RuntimeError, httpx.HTTPError) as exc:
            failed_uploads += 1
            out(f"  FAIL    {doc.name}: upload refused: {exc}")
            continue
        ids[doc.name] = view["id"]
        uploaded[view["id"]] = doc
        flag = " (metadata missing: no sidecar)" if doc.metadata_missing else ""
        out(f"  upload  {doc.name}{flag}")
    for doc, found in work.held:
        if found["status"] == "failed":
            out(f"  failed  {doc.name}: in the KB, ingestion failed ({found.get('reason')}); not retried")
    for gone in work.removed:
        out(f"  REMOVED {gone['name']} («{gone['metadata']['title']}»): in the KB but no longer in "
            f"kb-seed/{kb.slug}/; kept in the index (delete it on /admin/kbs/{kb.slug} if that is meant)")
    status = wait(gateway, kb.slug, set(uploaded), wait_s) if uploaded else {}
    for doc_id, doc in uploaded.items():
        if status.get(doc_id) != "indexed":
            out(f"  {status.get(doc_id, 'pending'):<7} {doc.name}")
    indexed = sum(1 for s in status.values() if s == "indexed")
    return {"slug": kb.slug, "uploaded": len(uploaded), "indexed": indexed, "held": len(work.held),
            "removed": len(work.removed), "failed_uploads": failed_uploads}


def wait(gateway: Gateway, slug: str, ids: set[str], wait_s: float) -> dict[str, str]:
    deadline = time.monotonic() + wait_s
    status: dict[str, str] = {}
    while True:
        status = {d["id"]: d["status"] for d in gateway.documents(slug) if d["id"] in ids}
        if all(status.get(i) in ("indexed", "failed") for i in ids) or time.monotonic() >= deadline:
            return status
        time.sleep(2)


def folders(only: str | None) -> list[Path]:
    return [p for p in sorted(SEED.iterdir()) if (p / "kb.yaml").is_file() and (only is None or p.name == only)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", help="seed this KB's folder only")
    parser.add_argument("--wait-s", type=float, default=900.0, help="how long to wait for indexing (default 900)")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(REPO / "scripts"))
    from get_token import env, login  # noqa: PLC0415

    cfg = env()
    kbs = [load_kb(folder) for folder in folders(args.only)]
    token = login("admin", "admin")["access_token"]
    totals = []
    with httpx.Client(timeout=TIMEOUT_S) as http:
        gateway = Gateway(http, cfg["GATEWAY_PUBLIC_URL"], token)
        for kb in kbs:
            totals.append(seed(gateway, kb, wait_s=args.wait_s))
    print("kb-seed: " + json.dumps(totals, ensure_ascii=False))
    return 1 if any(t["failed_uploads"] for t in totals) else 0


if __name__ == "__main__":
    sys.exit(main())
