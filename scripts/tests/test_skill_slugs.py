"""No core, gateway or web file names a skill (phase 06, G5): adding a skill needs no core change,
so a skill's slug appears only in its own folder, the realm, the eval datasets and thresholds, and
the tests. The reference skills are named here, and every folder of skills/ and of the agent's
test fixtures, so a new skill is covered the day it lands."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCANNED = [
    REPO / "services" / "agent" / "app",
    REPO / "services" / "gateway" / "src",
    REPO / "services" / "web" / "src",
    REPO / "config",
]
SKIPPED_PARTS = {"node_modules", "__pycache__", ".next", "build"}
REFERENCE = {"official_memo", "official_letter"}


def slugs() -> set[str]:
    roots = [REPO / "skills", REPO / "services" / "agent" / "tests" / "fixtures" / "skills"]
    found = {p.name for root in roots if root.is_dir() for p in root.iterdir() if p.is_dir()}
    return REFERENCE | found


def named(paths: list[Path], names: set[str]) -> list[str]:
    pattern = re.compile(r"(?<![a-z0-9_])(" + "|".join(sorted(map(re.escape, names))) + r")(?![a-z0-9_])")
    hits = []
    for root in paths:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            if SKIPPED_PARTS & set(path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{path}:{number}")
    return hits


def test_no_core_gateway_or_web_file_names_a_skill():
    assert named(SCANNED, slugs()) == []


def test_the_check_catches_a_named_slug(tmp_path):
    (tmp_path / "route.py").write_text('if skill == "official_memo":\n    pass\n', encoding="utf-8")
    (tmp_path / "ok.py").write_text("official_memos_count = 1\n", encoding="utf-8")
    hits = named([tmp_path], {"official_memo"})
    assert len(hits) == 1 and hits[0].endswith("route.py:1")
