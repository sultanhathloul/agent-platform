"""Document collections and whose they are (phase 05, I1, ADR-0202).

A collection is where a backend keeps a set of documents: `sess_{conversation_id}` holds the
files one user attached to one conversation (README §9.3), `kb_{slug}` a knowledge base of the
tenant (README §9.4). A scope names the collections a call may touch; the broker builds that
list from the verified principal and the turn, never from a tool's arguments or a model (S1):
the conversation's session collection, plus `kb_{slug}` for each `kb:{slug}:read` the principal
holds that the turn selected, kept to the kinds the calling capability's manifest declares
(`documents.collections`). Capability-agnostic: no capability, backend or server is named here."""

import re
from collections.abc import Collection, Iterable
from typing import Literal, get_args

CollectionKind = Literal["session", "kb"]
COLLECTION_KINDS: frozenset[str] = frozenset(get_args(CollectionKind))

_SESSION = "sess_"
_KB = "kb_"
_KB_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_KB_PERMISSION = re.compile(r"^kb:([a-z][a-z0-9_-]{0,62}):read$")


def session_collection(conversation_id: str) -> str:
    """A conversation's session-scoped collection (README §9.3, ADR-0131)."""
    if not conversation_id:
        raise ValueError("a session collection needs a conversation id")
    return f"{_SESSION}{conversation_id}"


def kb_collection(slug: str) -> str:
    if not _KB_SLUG.match(slug):
        raise ValueError(f"invalid knowledge base slug {slug!r}")
    return f"{_KB}{slug}"


def kind_of(collection: str) -> CollectionKind | None:
    if collection.startswith(_SESSION) and len(collection) > len(_SESSION):
        return "session"
    if collection.startswith(_KB) and _KB_SLUG.match(collection[len(_KB) :]):
        return "kb"
    return None


def conversation_of(collection: str) -> str | None:
    """The conversation id of a session collection; None for any other collection."""
    return collection[len(_SESSION) :] if kind_of(collection) == "session" else None


def permitted_kbs(permissions: Iterable[str]) -> list[str]:
    """The knowledge base slugs a principal may read: one per `kb:{slug}:read` (ADR-0006)."""
    return [m.group(1) for p in permissions if (m := _KB_PERMISSION.match(p))]


def turn_collections(
    *,
    permissions: Iterable[str],
    conversation_id: str,
    selected_kbs: Iterable[str],
    kinds: Collection[str],
) -> tuple[str, ...]:
    """The collections a capability's tools may touch this turn (I1): the conversation's session
    collection, and `kb_{slug}` for each knowledge base both permitted and selected, each kept
    only when its kind is one the capability declares. A selected slug without its permission,
    or a permission the turn did not select, adds nothing."""
    unknown = set(kinds) - COLLECTION_KINDS
    if unknown:
        raise ValueError(f"unknown collection kinds {sorted(unknown)}")
    collections: list[str] = []
    if "session" in kinds and conversation_id:
        collections.append(session_collection(conversation_id))
    if "kb" in kinds:
        selected = set(selected_kbs)
        collections += [kb_collection(slug) for slug in permitted_kbs(permissions) if slug in selected]
    return tuple(dict.fromkeys(collections))
