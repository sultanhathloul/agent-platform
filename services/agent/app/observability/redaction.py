"""What model-call content may reach Langfuse (D5, phase 03).

With OBSERVABILITY_CAPTURE_CONTENT on, generation spans carry their messages and
output, except that no memory, summary or extractor content goes to Langfuse:
- the `memory` and `summary` blocks of any package are replaced by a placeholder
  naming the block, its token count, a content hash of its text and, for memory, the
  item ids; so are the evidence blocks, `findings`, `retrieval` and `tool_results`,
  which hold document chunks and tool output (phase 04, ADR-0144): uploaded content
  never reaches Langfuse through a model call's input;
- the calls of the `summarizer` and `extractor` roles, and judge calls on a memory
  candidate or a summary, carry no messages and no output at all, only token counts
  and content hashes.
Content hashes are HMAC-SHA256 under CONTENT_HASH_KEY (ADR-0110), so nobody who reads
a trace can confirm a guessed short text offline.
The rolling summary and memory items stay in Postgres, where their owner can delete
them; a trace never holds a copy that outlives that delete."""

import hashlib
import hmac
import json

from app.context.package import ContextPackage

CONTENT_FREE_BLOCKS = frozenset({"memory", "summary", "findings", "retrieval", "tool_results"})
CONTENT_FREE_ROLES = frozenset({"summarizer", "extractor"})


def content_hash(text: str, key: str) -> str:
    """HMAC-SHA256 of `text` under `key` (ADR-0110)."""
    return hmac.new(key.encode("utf-8"), text.encode("utf-8"), hashlib.sha256).hexdigest()


def is_content_free(package: ContextPackage, role: str) -> bool:
    return role in CONTENT_FREE_ROLES or bool(package.request and package.request.content_free)


def observable_messages(package: ContextPackage, key: str) -> list[dict[str, str]]:
    """The package's messages with memory, summary and evidence blocks reduced to placeholders."""
    blocks = []
    for block in package.blocks:
        if block.name in CONTENT_FREE_BLOCKS:
            ids = ",".join(item["id"] for item in block.meta.get("items", []))
            placeholder = (
                f"[{block.name}: {block.tokens} tokens, hmac {content_hash(block.content, key)[:16]}"
            )
            placeholder += f", items {ids}]" if ids else "]"
            block = block.model_copy(update={"content": placeholder})
        blocks.append(block)
    return package.model_copy(update={"blocks": blocks}).to_messages()


def messages_digest(messages: list[dict[str, str]], key: str) -> str:
    return content_hash(json.dumps(messages, ensure_ascii=False, sort_keys=True), key)
