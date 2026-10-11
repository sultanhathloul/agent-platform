"""The seed policies under config/policies/ (owner review, 2026-10-11): each validates, and its
Arabic name, description and message are Arabic words only; ids, checkpoints, roles and
markers belong in the UI's chips, not in an Arabic sentence."""

import os
import re
from pathlib import Path

import pytest
import yaml

from app.guardrails.schema import Policy
from app.settings import _default_config_dir

SEEDS = sorted((Path(os.environ.get("CONFIG_DIR") or _default_config_dir()) / "policies").glob("*.yaml"))


def test_there_are_seeds():
    assert SEEDS


@pytest.mark.parametrize("path", SEEDS, ids=lambda p: p.stem)
def test_a_seed_validates_and_its_arabic_text_has_no_latin_letters(path):
    seed = yaml.safe_load(path.read_text(encoding="utf-8"))
    Policy.model_validate(seed)
    for field in ("name_ar", "description_ar", "message_ar"):
        text = seed.get(field) or ""
        assert not re.search(r"[A-Za-z]", text), f"{path.name} {field}: {text}"
