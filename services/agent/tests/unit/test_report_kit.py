"""The report kit (reports PR, I2, ADR-0370): its vendored files are the ones config/artifacts/kit/SOURCE.md
pins, the kit names no URL outside itself, and the design kit's prompt part loads with its front-matter."""

import hashlib
import re

from app.artifacts.config import load_artifacts
from app.settings import _default_config_dir

CONFIG = _default_config_dir()  # <repo>/config in a checkout, the /srv/config mount in the test container
KIT = CONFIG / "artifacts" / "kit"


def test_every_vendored_file_has_the_hash_source_md_pins():
    pinned = dict(re.findall(r"^\| (\S+) \|.*\| ([0-9a-f]{64}) \|$", (KIT / "SOURCE.md").read_text(), re.M))
    assert len(pinned) == 9
    for name, digest in pinned.items():
        assert hashlib.sha256((KIT / name).read_bytes()).hexdigest() == digest, name


def test_the_kit_loads_every_file_report_yaml_names():
    artifacts = load_artifacts(CONFIG)
    kit = artifacts.kit
    assert [name for name, _ in kit.scripts] == ["vendor/chart.umd.min.js", "kit.js"]
    assert len(kit.fonts) == 8 and all(data[:4] == b"wOF2" for data in kit.fonts.values())
    assert "data:font/woff2;base64," in kit.inline_stylesheet()
    assert artifacts.design.ref == "artifacts.design_system@1.0.0"


def test_the_kits_own_code_names_no_url_outside_itself():
    own = (KIT / "kit.css").read_text() + (KIT / "kit.js").read_text()
    assert not re.search(r"(?i)https?://|//[a-z0-9.-]+\.[a-z]{2,}/|@import", own)
    assert "localStorage" not in own and "fetch(" not in own and "XMLHttpRequest" not in own


def test_the_unverified_marker_reads_in_arabic_and_english():
    css = (KIT / "kit.css").read_text()
    assert 'content: "غير موثق"' in css and 'content: "Unverified"' in css
