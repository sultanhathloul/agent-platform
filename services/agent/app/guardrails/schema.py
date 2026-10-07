"""Guardrail contracts (README §10; phase 03 rule G2): the policy model, scopes and
their precedence, the checker result, the DATA payload and its target, the call
context of a checkpoint, and the outcome every call site acts on.

Everything here is data. The engine (engine.py) resolves which policies apply and
combines their results; the checkers (checkers/) only answer whether a text
matches. No capability-specific logic lives in this package (README §18.4): a
capability's policies are ordinary rows with scope `tool:{slug}`.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.guardrails.checkpoints import Checkpoint
from app.identity import Principal

# ------------------------------------------------------------------ vocabulary

Action = Literal["allow", "block", "redact", "rewrite", "warn", "require_approval", "log_only"]
ACTIONS: tuple[str, ...] = get_args(Action)
# Strictness (owner decision, ADR-0105): block > rewrite > redact > require_approval > warn >
# log_only > allow. The most restrictive action wins across groups and between two policy
# roles of one group, and a narrower policy with a less strict action is an exception.
ACTION_RANK: dict[str, int] = {
    "allow": 0,
    "log_only": 1,
    "warn": 2,
    "require_approval": 3,
    "redact": 4,
    "rewrite": 5,
    "block": 6,
}
Severity = Literal["low", "medium", "high", "critical"]
OnError = Literal["open", "closed"]
CheckerName = Literal["regex", "presidio", "keyword_list", "llm_judge", "tier_ceiling"]
# D8: deterministic checkers fail closed, the judge fails open, unless a policy says otherwise.
DEFAULT_ON_ERROR: dict[str, OnError] = {
    "regex": "closed",
    "presidio": "closed",
    "keyword_list": "closed",
    "llm_judge": "open",
    "tier_ceiling": "closed",
}

# Checkers that answer for the whole text, without spans (ADR-0115); the others match spans.
WHOLE_TEXT_CHECKERS = frozenset({"llm_judge", "tier_ceiling"})

# A4: what the text at the DATA checkpoint is on its way to become.
DataTarget = Literal["answer", "memory_candidate", "summary"]
DATA_TARGETS: tuple[str, ...] = get_args(DataTarget)

# Checkpoints whose payload carries text a redaction can rewrite in place.
TEXT_CHECKPOINTS = frozenset({Checkpoint.INPUT, Checkpoint.POST_TOOL, Checkpoint.OUTPUT, Checkpoint.DATA})

# ------------------------------------------------------------------ scopes

ScopeKind = Literal["global", "tenant", "role", "kb", "tool"]
# D2: narrow over wide.
SCOPE_PRECEDENCE: dict[str, int] = {"global": 0, "tenant": 1, "role": 2, "kb": 3, "tool": 4}
_SCOPE_VALUE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
# A role scope names a Keycloak realm role `policy:{name}` (ADR-0107); the name is a slug.
ROLE_NAME_PATTERN = r"[a-z0-9][a-z0-9_-]{0,63}"
_ROLE_NAME = re.compile(f"^{ROLE_NAME_PATTERN}$")
POLICY_ROLE_PREFIX = "policy:"


class ScopeError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class Scope:
    """`global | tenant:{id} | role:{name} | kb:{slug} | tool:{name}` (README §10.1, ADR-0025).
    `role:{name}` applies to a principal holding the policy role `policy:{name}`, the only
    roles the gateway forwards (ADR-0107)."""

    kind: ScopeKind
    value: str | None = None

    @classmethod
    def parse(cls, text: str) -> "Scope":
        if text == "global":
            return cls("global")
        kind, sep, value = text.partition(":")
        if not sep or kind not in SCOPE_PRECEDENCE or kind == "global":
            raise ScopeError(
                f"scope {text!r} must be global, tenant:<id>, role:<name>, kb:<slug> or tool:<name>"
            )
        if kind == "role" and not _ROLE_NAME.match(value):
            raise ScopeError(f"scope {text!r}: a role name is a slug of [a-z0-9_-], up to 64 characters")
        if not _SCOPE_VALUE.match(value):
            raise ScopeError(f"scope {text!r}: the value must be 1-128 characters of [A-Za-z0-9_.-]")
        return cls(kind, value)  # type: ignore[arg-type]

    @property
    def precedence(self) -> int:
        return SCOPE_PRECEDENCE[self.kind]

    def contains(self, other: "Scope") -> bool:
        """Containment (owner decision, ADR-0108): `global` contains every other scope;
        tenant, role, kb and tool scopes contain nothing but themselves, and a scope does
        not override itself. So only a policy at a non-global scope can override, and only
        a global policy of its group."""
        return self.kind == "global" and other.kind != "global"

    def __str__(self) -> str:
        return "global" if self.kind == "global" else f"{self.kind}:{self.value}"


# ------------------------------------------------------------------ checker configs


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A4: DATA policies may name the targets they guard; None means every target.
    targets: list[DataTarget] | None = None


class RegexPattern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z0-9_]{1,64}$", description="Entity type of a whole-match span.")
    pattern: str = Field(min_length=1)
    ignore_case: bool = True

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        return value


class RegexConfig(_Config):
    """Named groups become spans typed by the group name; a pattern without named
    groups yields one span typed by the pattern's name (README §10.2)."""

    patterns: list[RegexPattern] = Field(min_length=1)
    fold: bool = Field(
        default=True,
        description="Match folded text: Arabic-Indic digits as ASCII, tashkeel and tatweel removed, "
        "alef, alef maqsura and taa marbuta forms folded.",
    )


class PresidioConfig(_Config):
    entities: list[str] = Field(min_length=1)
    score_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    language: str = "en"


class KeywordListConfig(_Config):
    keywords: list[str] = Field(min_length=1)
    # deny: matched when a keyword occurs; allow: matched when the text is none of the keywords.
    mode: Literal["deny", "allow"] = "deny"


class JudgeExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    violates: bool


class LlmJudgeConfig(_Config):
    topic_description: str = Field(min_length=1, description="The topic in natural language (README §10.2).")
    examples: list[JudgeExample] = []
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class TierCeilingConfig(_Config):
    """A MODEL_TIER policy is a ceiling only (D2): it matches when the requested tier is
    above `max_tier`, and the request is served at `max_tier`."""

    max_tier: str = Field(min_length=1)
    roles: list[str] | None = Field(default=None, description="Model roles it applies to; None means all.")


CONFIG_MODELS: dict[str, type[_Config]] = {
    "regex": RegexConfig,
    "presidio": PresidioConfig,
    "keyword_list": KeywordListConfig,
    "llm_judge": LlmJudgeConfig,
    "tier_ceiling": TierCeilingConfig,
}

# ------------------------------------------------------------------ policy

POLICY_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{1,63}$"


class Policy(BaseModel):
    """One immutable version of a policy (README §10.1, I2). `enabled` is not part
    of the version: switching a policy on or off never creates a version."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=POLICY_ID_PATTERN)
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    enabled: bool = True
    scope: str
    checkpoints: list[Checkpoint] = Field(min_length=1)
    checker: CheckerName
    config: dict[str, Any] = {}
    action: Action
    severity: Severity = "medium"
    message_ar: str | None = None
    message_en: str | None = None
    on_error: OnError | None = None
    group: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$",
        description="Policies of one group override each other narrow over wide; default: the id.",
    )

    @field_validator("scope")
    @classmethod
    def _scope(cls, value: str) -> str:
        return str(Scope.parse(value))

    @field_validator("checkpoints")
    @classmethod
    def _unique(cls, value: list[Checkpoint]) -> list[Checkpoint]:
        if len(set(value)) != len(value):
            raise ValueError("checkpoints must not repeat")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "Policy":
        checkpoints = set(self.checkpoints)
        try:
            CONFIG_MODELS[self.checker].model_validate(self.config)
        except ValidationError as exc:
            raise ValueError(f"config for checker {self.checker}: {exc}") from exc
        if self.action == "block" and not (self.message_ar and self.message_en):
            raise ValueError("a block policy needs message_ar and message_en")
        if self.action == "rewrite" and checkpoints != {Checkpoint.OUTPUT}:
            raise ValueError("rewrite exists only at OUTPUT, in buffered mode (D3)")
        if self.action == "require_approval" and checkpoints != {Checkpoint.PRE_TOOL}:
            raise ValueError("require_approval exists only at PRE_TOOL")
        if self.action == "redact" and not checkpoints <= TEXT_CHECKPOINTS:
            raise ValueError("redact needs a text checkpoint: INPUT, POST_TOOL, OUTPUT or DATA")
        tier = Checkpoint.MODEL_TIER in checkpoints
        if tier != (self.checker == "tier_ceiling") or (tier and checkpoints != {Checkpoint.MODEL_TIER}):
            raise ValueError("MODEL_TIER policies use the tier_ceiling checker, and only they do")
        if tier and self.action not in ("log_only", "warn"):
            raise ValueError("a MODEL_TIER policy is a ceiling (D2); its action is log_only or warn")
        return self

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def parsed_scope(self) -> Scope:
        return Scope.parse(self.scope)

    @property
    def effective_on_error(self) -> OnError:
        return self.on_error or DEFAULT_ON_ERROR[self.checker]

    @property
    def effective_group(self) -> str:
        return self.group or self.id

    def typed_config(self) -> _Config:
        return CONFIG_MODELS[self.checker].model_validate(self.config)

    def guards_target(self, target: str | None) -> bool:
        targets = self.config.get("targets")
        return target is None or targets is None or target in targets

    def content(self) -> dict[str, Any]:
        """The fields a version is made of: everything but id, version and enabled."""
        return self.model_dump(mode="json", exclude={"id", "version", "enabled"})


# ------------------------------------------------------------------ results


class Span(BaseModel):
    """A matched region of the checked text, by character offsets. Events carry the
    entity type only, never the matched text."""

    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    entity: str
    score: float | None = None


class CheckResult(BaseModel):
    """What a checker returns: `check(payload, config) -> CheckResult` (README §10.2)."""

    model_config = ConfigDict(extra="forbid")

    matched: bool
    spans: list[Span] = []
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    explanation: str = ""
    # The explanation for a reader in either language: a key under the web's
    # `policies.explanation.*` and its arguments (rule 7); `explanation` stays for traces.
    reason: str = ""
    reason_args: dict[str, str] = {}


class DataPayload(BaseModel):
    """The DATA checkpoint's payload (A4): text, and where it is going."""

    model_config = ConfigDict(extra="forbid")

    text: str
    target: DataTarget


@dataclass(frozen=True)
class GuardContext:
    """Where a checkpoint is evaluated, beyond the principal: the KBs selected this
    turn, the tools or capabilities involved (each a `tool:` scope), the locale, and
    the running turn when there is one (its usage pays for judge calls)."""

    kbs: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    locale: str = "ar"
    turn: Any = None
    # The admin test panel evaluates one policy, enabled or not, and records nothing.
    only: Policy | None = None
    # `only` is an unsaved draft, so its id@version names no stored content (judge cache, ADR-0110).
    draft: bool = False
    # Executes only these actions (None: all). Every applicable policy of a group that holds one
    # of them still takes part in the group's resolution, so an exception weighs the same in
    # both answer modes; only the execution is limited (ADR-0108). While the answer streams,
    # OUTPUT runs per sentence executing redact only (ADR-0106).
    actions: frozenset[str] | None = None

    def scopes(self, principal: Principal) -> set[str]:
        scopes = {"global", f"tenant:{principal.tenant_id}"}
        scopes.update(f"role:{role.removeprefix(POLICY_ROLE_PREFIX)}" for role in principal.policy_roles)
        scopes.update(f"kb:{kb}" for kb in self.kbs)
        scopes.update(f"tool:{tool}" for tool in self.tools)
        return scopes


class GuardEventRecord(BaseModel):
    """One policy evaluation, kept in state.guard_events, streamed as a `guard` event
    and summarized in the audit delta, with the policy's id and version (A1)."""

    model_config = ConfigDict(extra="forbid")

    checkpoint: Checkpoint
    policy_id: str | None = None
    policy_version: int | None = None
    action: Action
    detail: str | None = None
    target: DataTarget | None = None
    entities: list[str] = []
    latency_ms: int | None = None

    @property
    def policy_ref(self) -> str | None:
        return f"{self.policy_id}@{self.policy_version}" if self.policy_id else None


class GuardOutcome(BaseModel):
    """The engine's verdict for one checkpoint call: the resulting action, the
    payload after redaction or filtering, every event, the message of the policy
    that blocked, and whether a fail-closed policy could not be evaluated (D8)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    action: Action = "allow"
    payload: Any = None
    events: list[GuardEventRecord] = []
    message_ar: str | None = None
    message_en: str | None = None
    decided_by: str | None = Field(default=None, description="policy_id@version of the deciding policy")
    unavailable: str | None = Field(default=None, description="policy_id@version that failed closed")
    results: dict[str, CheckResult] = Field(default_factory=dict, description="policy ref → its CheckResult")

    @property
    def blocked(self) -> bool:
        return self.action == "block"


def overridden(policy: Policy, others: "list[Policy]") -> "list[Policy]":
    """The policies `policy` overrides (ADR-0108): same group, a scope that contains its own
    (only `global` contains another scope), a shared checkpoint and, at DATA alone, a shared
    target."""
    mine = policy.parsed_scope
    out = []
    for other in others:
        if other.id == policy.id or other.effective_group != policy.effective_group:
            continue
        if not other.parsed_scope.contains(mine):
            continue
        shared = set(other.checkpoints) & set(policy.checkpoints)
        if not shared:
            continue
        if shared == {Checkpoint.DATA} and not _targets(policy) & _targets(other):
            continue
        out.append(other)
    return out


def _targets(policy: Policy) -> set[str]:
    return set(policy.config.get("targets") or DATA_TARGETS)


def is_exception(policy: Policy, overrides: "list[Policy]") -> bool:
    """A narrower policy that weakens a wider one of its group, by design (ADR-0105)."""
    return any(ACTION_RANK[policy.action] < ACTION_RANK[other.action] for other in overrides)


def whole_text_cannot_lift(policy: Policy, overrides: "list[Policy]") -> "list[Policy]":
    """The span-based policies a whole-text exception would weaken but cannot (ADR-0115): it
    cancels only a whole-text verdict, so their redactions and blocks stand. A warning, not a
    refusal."""
    if policy.checker not in WHOLE_TEXT_CHECKERS:
        return []
    return [
        other
        for other in overrides
        if other.checker not in WHOLE_TEXT_CHECKERS and ACTION_RANK[policy.action] < ACTION_RANK[other.action]
    ]


# Until phase 04 attributes each part of an answer to its source, a kb: or tool: policy may not
# weaken a wider one where the whole answer is checked (ADR-0108).
_SOURCE_SCOPES = frozenset({"kb", "tool"})
_ANSWER_CHECKPOINTS = frozenset({Checkpoint.OUTPUT, Checkpoint.DATA})


def weakening_refusals(policies: "list[Policy]") -> list[str]:
    """Why this set of current versions cannot be saved: each kb: or tool: policy that would
    weaken a wider policy of its group at OUTPUT or DATA (ADR-0108)."""
    reasons = []
    for policy in policies:
        if policy.parsed_scope.kind not in _SOURCE_SCOPES:
            continue
        for other in overridden(policy, policies):
            shared = set(policy.checkpoints) & set(other.checkpoints) & _ANSWER_CHECKPOINTS
            if shared and ACTION_RANK[policy.action] < ACTION_RANK[other.action]:
                points = ", ".join(sorted(c.value for c in shared))
                reasons.append(
                    f"{policy.id}: a {policy.parsed_scope.kind}: policy may not weaken {other.id} "
                    f"({policy.action} under {other.action}) at {points} before phase 04 "
                    "attributes answers to their sources (ADR-0108)"
                )
    return reasons


@dataclass
class EvaluatedPolicy:
    """Internal: one policy's result inside one evaluation. `spans` are the spans that still
    stand after narrower policies of the group cancelled theirs (ADR-0108)."""

    policy: Policy
    result: CheckResult | None
    error: str | None = None
    latency_ms: int = 0
    overridden_by: str | None = None
    partly_overridden_by: list[str] = field(default_factory=list)
    spans: list[Span] = field(default_factory=list)
