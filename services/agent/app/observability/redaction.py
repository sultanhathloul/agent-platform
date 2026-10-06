"""What model-call content may reach Langfuse (D5, phase 03).

With OBSERVABILITY_CAPTURE_CONTENT on, generation spans carry their messages and
output, except that no memory, summary or extractor content goes to Langfuse:
- the `memory` and `summary` blocks of any package are replaced by a placeholder
  naming the block, its token count, a SHA-256 of its text and, for memory, the item ids;
- the calls of the `summarizer` and `extractor` roles carry no messages and no output
  at all, only token counts and content hashes.
The rolling summary and memory items stay in Postgres, where their owner can delete
them; a trace never holds a copy that outlives that delete."""

import hashlib
import json

from app.context.package import ContextPackage

CONTENT_FREE_BLOCKS = frozenset({"memory", "summary"})
CONTENT_FREE_ROLES = frozenset({"summarizer", "extractor"})


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def observable_messages(package: ContextPackage) -> list[dict[str, str]]:
    """The package's messages with memory and summary blocks reduced to placeholders."""
    blocks = []
    for block in package.blocks:
        if block.name in CONTENT_FREE_BLOCKS:
            ids = ",".join(item["id"] for item in block.meta.get("items", []))
            placeholder = f"[{block.name}: {block.tokens} tokens, sha256 {sha256(block.content)[:16]}"
            placeholder += f", items {ids}]" if ids else "]"
            block = block.model_copy(update={"content": placeholder})
        blocks.append(block)
    return package.model_copy(update={"blocks": blocks}).to_messages()


def messages_digest(messages: list[dict[str, str]]) -> str:
    return sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True))
