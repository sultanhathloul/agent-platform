"""Several texts checked as one (owner decision S2). A checkpoint whose payload holds more than
one text a person reads (a tool's content and its sources' titles and locators; a message and
the names of the files sent with it; the citations under an answer) is evaluated once, on the
texts joined with marker lines, and split back after a redaction. The marker carries a tag
derived from the texts themselves, so a document cannot forge one and move text from its body
into a title."""

import hashlib
import re
from collections.abc import Sequence

_LETTERS = str.maketrans("0123456789", "ghijklmnop")  # no digits: no PII pattern ever matches a marker


def _tag(head: str, fields: Sequence[str]) -> str:
    digest = hashlib.sha256("\x00".join([head, *fields]).encode("utf-8")).hexdigest()
    return digest[:12].translate(_LETTERS)


def join_fields(head: str, fields: Sequence[str]) -> str:
    """`head`, then each field on a line of its own after its marker."""
    if not fields:
        return head
    tag = _tag(head, fields)
    return head + "".join(f"\n⟦{tag} {index}⟧ {field}" for index, field in enumerate(fields))


def split_fields(text: str, original_head: str, original_fields: Sequence[str]) -> tuple[str, list[str]]:
    """The head and the fields of a joined text after a checkpoint changed it. A field whose
    marker did not survive comes back empty (fail closed: no unchecked text is kept)."""
    if not original_fields:
        return text, []
    tag = _tag(original_head, original_fields)
    marker = re.compile(rf"\n⟦{tag} (\d+)⟧ ")
    found = list(marker.finditer(text))
    head = text[: found[0].start()] if found else text
    fields = [""] * len(original_fields)
    for position, match in enumerate(found):
        index = int(match.group(1))
        end = found[position + 1].start() if position + 1 < len(found) else len(text)
        if 0 <= index < len(fields):
            fields[index] = text[match.end() : end]
    return head, fields
