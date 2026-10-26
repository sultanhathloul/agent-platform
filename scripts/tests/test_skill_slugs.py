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
REFERENCE = {"official_memo", "internal-comms"}


def _folders(root: Path) -> set[str]:
    return {p.name for p in root.iterdir() if p.is_dir()} if root.is_dir() else set()


def shipped() -> set[str]:
    """The reference skills and every folder of skills/: named in no scanned file, tests included."""
    return REFERENCE | _folders(REPO / "skills")


def fixtures() -> set[str]:
    """The agent's test-only skills: a gateway or web test may use their names, code may not."""
    return _folders(REPO / "services" / "agent" / "tests" / "fixtures" / "skills")


def is_test(path: Path) -> bool:
    return "test" in path.parts or ".test." in path.name or path.name.endswith("Test.java")


def named(paths: list[Path], names: set[str], *, skip_tests: bool = False) -> list[str]:
    pattern = re.compile(r"(?<![a-z0-9_])(" + "|".join(sorted(map(re.escape, names))) + r")(?![a-z0-9_])")
    hits = []
    for root in paths:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            if SKIPPED_PARTS & set(path.parts) or (skip_tests and is_test(path)):
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
    assert named(SCANNED, shipped()) == []
    assert named(SCANNED, fixtures(), skip_tests=True) == []


def test_the_check_catches_a_named_slug(tmp_path):
    (tmp_path / "route.py").write_text('if skill == "official_memo":\n    pass\n', encoding="utf-8")
    (tmp_path / "ok.py").write_text("official_memos_count = 1\n", encoding="utf-8")
    hits = named([tmp_path], {"official_memo"})
    assert len(hits) == 1 and hits[0].endswith("route.py:1")
