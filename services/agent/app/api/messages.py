"""Error texts for `error` events, loaded from config/messages/errors.yaml."""

from pathlib import Path

import yaml

from app.api.events import ErrorData

FALLBACK_CODE = "turn_failed"


class ErrorMessages:
    def __init__(self, texts: dict[str, dict[str, str]]):
        if FALLBACK_CODE not in texts:
            raise ValueError(f"errors.yaml must define {FALLBACK_CODE}")
        for code, text in texts.items():
            if not text.get("ar") or not text.get("en"):
                raise ValueError(f"errors.yaml: {code} needs both ar and en")
        self._texts = texts

    @classmethod
    def load(cls, config_dir: Path) -> "ErrorMessages":
        return cls(yaml.safe_load((config_dir / "messages" / "errors.yaml").read_text(encoding="utf-8")))

    def error(self, code: str, *, retryable: bool = False) -> ErrorData:
        text = self._texts.get(code, self._texts[FALLBACK_CODE])
        return ErrorData(code=code, message_ar=text["ar"], message_en=text["en"], retryable=retryable)
