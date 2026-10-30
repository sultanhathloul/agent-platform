"""The seed step of kb-seed/ (owner decision B on the report branch, ADR-0392), against a fake gateway that
keeps its KBs and documents as the real one does (the SHA-256 computed on its side): seeding is idempotent,
a new file is picked up, a file removed from the folder is reported and never deleted, a file without a
sidecar is uploaded flagged, and an earlier version is uploaded before the one that supersedes it. Also:
every committed KB folder agrees with the realm export, and its manifest with its files."""

import dataclasses
import hashlib
import json
import sys
import uuid
from pathlib import Path

import httpx
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import kb_seed  # noqa: E402


class FakeGateway:
    def __init__(self):
        self.kbs: dict[str, dict] = {}
        self.docs: dict[str, list[dict]] = {}
        self.deletes = 0
        self.uploads: list[dict] = []
        self.reindexes: list[str] = []
        self.deleted: set[str] = set()
        self.busy = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "DELETE":
            self.deletes += 1
            return httpx.Response(405)
        if path == "/v1/admin/kbs" and request.method == "POST":
            body = json.loads(request.content)
            if body["slug"] in self.kbs or body["slug"] in self.deleted:
                # As the agent: the slug is taken by a live or a deleted KB.
                return httpx.Response(409, json={"error": {"code": "kb_exists"}})
            self.kbs[body["slug"]] = body
            self.docs[body["slug"]] = []
            return httpx.Response(201, json={"data": body})
        parts = path.split("/")
        slug = parts[4]
        if path.endswith("/reindex"):
            # As the agent does (ADR-0410): a due document goes back to pending, then indexes with the new
            # extractor; one already current comes back unchanged.
            # Since ADR-0424 the document stays indexed while it re-indexes beside its chunks, and a failed
            # re-index is due again; the agent counts it with the caller's uploads in flight (429 past them).
            if self.busy:
                self.busy -= 1
                return httpx.Response(429, json={"error": {"code": "too_many_uploads"}})
            doc = next(d for d in self.docs[slug] if d["id"] == parts[6])
            self.reindexes.append(doc["id"])
            if doc.get("reindex_due"):
                doc.update(reindex_due=False, reindex_status=None, reindex_reason=None)
                return httpx.Response(200, json={"data": {**doc, "status": "indexed", "reindex_status": "reindexing"}})
            return httpx.Response(200, json={"data": doc})
        if len(parts) == 5:
            return httpx.Response(200 if slug in self.kbs else 404, json={"data": self.kbs.get(slug)})
        if request.method == "GET":
            return httpx.Response(200, json={"data": {"documents": self.docs[slug]}})
        fields, data = _multipart(request)
        view = {
            "id": str(uuid.uuid4()), "name": fields["filename"], "status": "indexed",
            "sha256": hashlib.sha256(data).hexdigest(), "metadata_missing": fields.get("metadata_missing") == "true",
            "metadata": {k: fields[k] for k in ("title", "publisher", "published_on") if k in fields}
            | ({"supersedes": fields["supersedes"]} if "supersedes" in fields else {}),
        }
        self.docs[slug].append(view)
        self.uploads.append(view)
        return httpx.Response(201, json={"data": view})


def _multipart(request: httpx.Request) -> tuple[dict, bytes]:
    boundary = request.headers["content-type"].split("boundary=")[1].encode()
    fields, data = {}, b""
    for part in request.content.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, body = part.split(b"\r\n\r\n", 1)
        body = body.rsplit(b"\r\n", 1)[0]
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        if b'filename="' in head:
            fields["filename"] = head.split(b'filename="')[1].split(b'"')[0].decode()
            data = body
        else:
            fields[name] = body.decode()
    return fields, data


def _folder(tmp_path: Path) -> Path:
    folder = tmp_path / "fixture-stats"
    folder.mkdir()
    (folder / "kb.yaml").write_text(yaml.safe_dump({
        "slug": "fixture-stats", "name": {"ar": "إحصاءات", "en": "Stats"}, "description": {"ar": "و", "en": "d"},
        "role": "kb:fixture-stats:read", "users": ["analyst"], "default_publisher": "جهة",
    }, allow_unicode=True))
    (folder / "README.md").write_text("not a document")
    for name, title, extra in (("a-2025.pdf", "التقرير 2025", {}), ("a-2026.pdf", "التقرير 2026", {"supersedes": "a-2025.pdf"})):
        (folder / name).write_bytes(b"%PDF-1.7 " + name.encode())
        (folder / (name + ".meta.yaml")).write_text(yaml.safe_dump(
            {"title": title, "publisher": "الهيئة", "published_on": "2026-01-01", **extra}, allow_unicode=True))
    return folder


def _run(fake: FakeGateway, folder: Path) -> tuple[dict, list[str]]:
    lines: list[str] = []
    with httpx.Client(transport=httpx.MockTransport(fake.handle)) as http:
        gateway = kb_seed.Gateway(http, "http://gw", "token")
        totals = kb_seed.seed(gateway, kb_seed.load_kb(folder), wait_s=0, out=lines.append)
    return totals, lines


def test_seeding_creates_the_kb_uploads_in_supersedes_order_and_is_idempotent(tmp_path):
    folder, fake = _folder(tmp_path), FakeGateway()
    first, _ = _run(fake, folder)
    assert first["uploaded"] == 2 and "fixture-stats" in fake.kbs
    older, newer = fake.uploads
    assert older["name"] == "a-2025.pdf" and newer["metadata"]["supersedes"] == older["id"]
    again, lines = _run(fake, folder)
    assert again == {**again, "uploaded": 0, "held": 2, "removed": 0}
    assert len(fake.uploads) == 2 and "2 already in the KB, 0 to upload" in lines[0]


def test_a_new_file_is_picked_up_and_one_without_a_sidecar_is_flagged(tmp_path):
    folder, fake = _folder(tmp_path), FakeGateway()
    _run(fake, folder)
    (folder / "يدوي.xlsx").write_bytes(b"PK\x03\x04 dropped by hand")
    totals, lines = _run(fake, folder)
    assert totals["uploaded"] == 1
    dropped = fake.uploads[-1]
    assert dropped["name"] == "يدوي.xlsx" and dropped["metadata"]["title"] == "يدوي.xlsx"
    assert dropped["metadata_missing"] is True and dropped["metadata"]["publisher"] == "جهة"
    assert any("metadata missing" in line for line in lines)


def test_a_removed_file_is_reported_and_never_deleted_from_the_index(tmp_path):
    folder, fake = _folder(tmp_path), FakeGateway()
    _run(fake, folder)
    (folder / "a-2026.pdf").unlink()
    (folder / "a-2026.pdf.meta.yaml").unlink()
    totals, lines = _run(fake, folder)
    assert totals["removed"] == 1 and totals["uploaded"] == 0
    assert fake.deletes == 0 and len(fake.docs["fixture-stats"]) == 2
    assert any(line.startswith("  REMOVED a-2026.pdf") and "kept in the index" in line for line in lines)


def test_a_changed_file_is_a_new_document_and_the_old_one_is_reported(tmp_path):
    folder, fake = _folder(tmp_path), FakeGateway()
    _run(fake, folder)
    (folder / "a-2025.pdf").write_bytes(b"%PDF-1.7 corrected")
    totals, _ = _run(fake, folder)
    assert totals["uploaded"] == 1 and totals["removed"] == 1


def test_a_document_registered_before_hashes_is_matched_by_its_title(tmp_path):
    folder, fake = _folder(tmp_path), FakeGateway()
    fake.kbs["fixture-stats"] = {}
    fake.docs["fixture-stats"] = [{"id": "old", "name": "a-2025.pdf", "status": "indexed", "sha256": None,
                                   "metadata": {"title": "التقرير 2025"}}]
    totals, _ = _run(fake, folder)
    assert totals["held"] == 1 and totals["uploaded"] == 1 and fake.uploads[0]["metadata"]["supersedes"] == "old"


def test_the_same_file_twice_in_a_folder_is_one_document(tmp_path):
    folder, fake = _folder(tmp_path), FakeGateway()
    (folder / "copy.pdf").write_bytes((folder / "a-2025.pdf").read_bytes())
    totals, _ = _run(fake, folder)
    assert totals["uploaded"] == 2 and len(fake.docs["fixture-stats"]) == 2


def test_a_sidecar_naming_a_file_not_in_the_folder_is_refused(tmp_path):
    folder = _folder(tmp_path)
    (folder / "a-2026.pdf.meta.yaml").write_text(yaml.safe_dump(
        {"title": "x", "publisher": "p", "published_on": "2026-01-01", "supersedes": "missing.pdf"}))
    with pytest.raises(ValueError, match="missing.pdf"):
        kb_seed.load_kb(folder)


# ------------------------------------------------------------------ the committed folders


def _realm_roles() -> dict[str, set[str]]:
    realm = json.loads((REPO / "infra" / "keycloak" / "realm-export.json").read_text(encoding="utf-8"))
    return {u["username"]: set(u.get("realmRoles") or []) for u in realm["users"]}


@pytest.mark.parametrize("folder", kb_seed.folders(None), ids=lambda p: p.name)
def test_each_seeded_kb_agrees_with_the_realm_and_its_manifest(folder):
    kb = kb_seed.load_kb(folder)
    roles = _realm_roles()
    for user in kb.users:
        assert kb.role in roles[user], f"{user} lacks {kb.role} in infra/keycloak/realm-export.json"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    listed = {d["file"]: d for d in manifest["documents"] if d.get("file")}
    for doc in kb.documents:
        assert doc.name in listed, f"{doc.name} has no manifest entry"
        assert listed[doc.name]["sha256"] == doc.sha256, f"{doc.name}: the manifest's SHA-256 is not the file's"
        assert listed[doc.name]["url"].startswith("https://")
        assert not doc.metadata_missing, f"{doc.name}: a committed document needs its sidecar"
    assert not kb.ignored


def test_both_seeded_kbs_are_there():
    assert {p.name for p in kb_seed.folders(None)} >= {"intl-economy", "stats-gov"}


def test_a_document_an_older_extractor_cut_is_reindexed_once(tmp_path):
    """ADR-0410: the seed re-indexes what an older version of its kind's extractor indexed; a second run finds
    nothing due and re-indexes nothing."""
    folder, fake = _folder(tmp_path), FakeGateway()
    _run(fake, folder)
    fake.docs["fixture-stats"][0]["reindex_due"] = True
    first, lines = _run(fake, folder)
    assert first["reindexed"] == 1 and first["reindexed_ok"] == 1 and first["uploaded"] == 0
    assert any(line.startswith("  reindex ") for line in lines) and len(fake.uploads) == 2
    again, _ = _run(fake, folder)
    assert again["reindexed"] == 0 and len(fake.reindexes) == 1



def test_a_seeded_kb_an_admin_deleted_is_reported_skipped_and_never_recreated(tmp_path):
    """Review round 1 of PR #17, finding 10 (ADR-0423): GET answers 404 and POST 409 for a deleted KB; the
    seed reports and skips it, creates nothing and uploads nothing."""
    folder, fake = _folder(tmp_path), FakeGateway()
    fake.deleted.add("fixture-stats")
    totals, lines = _run(fake, folder)
    assert totals["skipped"] == "deleted" and totals["uploaded"] == 0 and totals["failed_uploads"] == 0
    assert "fixture-stats" not in fake.kbs and fake.uploads == []
    assert any("(deleted)" in line and "not recreated" in line for line in lines)


def test_main_goes_on_past_a_deleted_kb_and_exits_zero(tmp_path, monkeypatch):
    """The KBs after a deleted one are still seeded, and the run exits 0 (make up stays green)."""
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    gone, kept = _folder(first), _folder(second)
    fake = FakeGateway()
    fake.deleted.add("fixture-stats")
    kbs = {gone: kb_seed.load_kb(gone), kept: kb_seed.load_kb(kept)}
    kbs[kept] = dataclasses.replace(kbs[kept], slug="fixture-kept", role="kb:fixture-kept:read")
    seeded: list[str] = []
    real_seed = kb_seed.seed

    def recording(gateway, kb, **kwargs):
        seeded.append(kb.slug)
        return real_seed(gateway, kb, **kwargs)

    monkeypatch.setattr(kb_seed, "folders", lambda only: [gone, kept])
    monkeypatch.setattr(kb_seed, "load_kb", lambda folder: kbs[folder])
    monkeypatch.setattr(kb_seed, "seed", recording)
    monkeypatch.setitem(sys.modules, "get_token", type(sys)("get_token"))
    sys.modules["get_token"].env = lambda: {"GATEWAY_PUBLIC_URL": "http://gw"}
    sys.modules["get_token"].login = lambda user, password: {"access_token": "t"}
    real_client = httpx.Client
    monkeypatch.setattr(kb_seed.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(fake.handle)))
    assert kb_seed.main(["--wait-s", "0"]) == 0
    assert seeded == ["fixture-stats", "fixture-kept"] and "fixture-kept" in fake.kbs


def test_a_failed_reindex_is_retried_and_a_busy_agent_is_waited_for(tmp_path, monkeypatch):
    """ADR-0424: a re-index that failed keeps the document indexed on its old chunks and is due again; the seed
    retries it, waiting out a 429 too_many_uploads as an upload does."""
    monkeypatch.setattr(kb_seed, "BUSY_WAIT_S", 0)
    folder, fake = _folder(tmp_path), FakeGateway()
    _run(fake, folder)
    fake.docs["fixture-stats"][0].update(reindex_due=True, reindex_status="reindex_failed",
                                         reindex_reason="extraction_aborted")
    fake.busy = 2
    totals, lines = _run(fake, folder)
    assert totals["reindexed"] == 1 and totals["reindexed_ok"] == 1 and fake.busy == 0
    assert any("reindex " in line and "retry after extraction_aborted" in line for line in lines)
    again, _ = _run(fake, folder)
    assert again["reindexed"] == 0


def test_the_wait_reads_a_running_reindex_as_pending_and_a_failed_one_as_done():
    assert kb_seed.progress({"status": "indexed", "reindex_status": "reindexing"}) == "pending"
    assert kb_seed.progress({"status": "indexed", "reindex_status": "reindex_failed"}) == "reindex_failed"
    assert kb_seed.progress({"status": "indexed", "reindex_status": None}) == "indexed"
