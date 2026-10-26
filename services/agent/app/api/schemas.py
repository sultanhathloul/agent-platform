"""Request and response bodies of the internal agent API (README §6.2)."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.api.events import Citation, ErrorData, SkillData, SkillLabel
from app.identity import Locale, Principal
from app.skills.contract import NAME_PATTERN

# A conversation id is a UUID in canonical lower-case form; the gateway normalises it (ADR-0050).
CONVERSATION_ID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
# A turn id is the runner's uuid4().hex.
TURN_ID_PATTERN = r"^[0-9a-f]{32}$"
# A memory item id is a lower-case UUID, as Postgres returns it.
MEMORY_ITEM_ID_PATTERN = CONVERSATION_ID_PATTERN


class RetractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: Principal
    reason: str = Field(pattern=r"^[a-z_]{1,64}$", description="Error code that withdrew the reply.")


# An attachment id is a lower-case UUID, as Postgres returns it.
ATTACHMENT_ID_PATTERN = CONVERSATION_ID_PATTERN
# A skill name (phase 06, ADR-0296, ADR-0317): the open format's names and the platform's slugs.
SKILL_SLUG_PATTERN = NAME_PATTERN


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(
        pattern=CONVERSATION_ID_PATTERN, description="UUID, lower-case; the gateway normalises it (ADR-0050)."
    )
    principal: Principal
    message: str = Field(min_length=1, max_length=8000)
    attachment_ids: list[Annotated[str, Field(pattern=ATTACHMENT_ID_PATTERN)]] = Field(
        default=[],
        max_length=10,
        description="Attachments sent with this turn; the agent loads them after the ownership check (A1).",
    )
    selected_kbs: list[str] = []
    locale: Locale = "ar"
    skill: str | None = Field(
        default=None,
        pattern=SKILL_SLUG_PATTERN,
        description="The skill the user picked in the composer (phase 06, ADR-0292): applied with no router "
        "decision on the skill, when it is available to the principal; else 403 skill_forbidden before any stream.",
    )


class MCPDisabledTool(BaseModel):
    tool: str = Field(description="{server}.{tool}")
    reason: str = Field(
        description="not_connected | missing | schema_mismatch | server_<why the server is disabled>"
    )


class MCPServerHealth(BaseModel):
    """One server of config/mcp.yaml as this environment runs it (I8): content-free, no URL or token."""

    id: str
    internal: bool
    enabled: bool
    reason: str | None = Field(
        default=None,
        description="Why disabled: profile, external_not_allowed, external_in_airgap, no_url, no_token.",
    )
    connected: bool
    circuit: Literal["closed", "open", "half_open"] | None = None
    disabled_tools: list[MCPDisabledTool] = []


class HealthResponse(BaseModel):
    status: str
    capabilities: list[str]
    mcp: list[MCPServerHealth] = Field(default=[], description="MCP servers and their disabled tools (I8).")


class MemoryItemView(BaseModel):
    id: str
    kind: str = Field(description="preference | fact_about_user | project_context | decision (README §8).")
    text: str
    confidence: float
    created_at: datetime
    expires_at: datetime | None = None


class MemoryList(BaseModel):
    enabled: bool = Field(description="Whether memory is written and retrieved for this user.")
    items: list[MemoryItemView]


class MemorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class RecordedContext(BaseModel):
    node: str
    created_at: datetime
    package: dict[str, Any] = Field(
        description="The ContextPackage a model call received, with its accounting."
    )


class DebugContext(BaseModel):
    turn_id: str
    conversation_id: str
    packages: list[RecordedContext] = Field(description="Every model call of the turn, in assembly order.")


class ApiError(BaseModel):
    code: str
    message_ar: str
    message_en: str
    details: list[str] | None = Field(
        default=None, description="What is wrong, e.g. a policy's validation errors."
    )


class ErrorEnvelope(BaseModel):
    request_id: str
    error: ApiError

    @classmethod
    def of(cls, request_id: str, error: ErrorData, details: list[str] | None = None) -> "ErrorEnvelope":
        return cls(
            request_id=request_id,
            error=ApiError(
                code=error.code, message_ar=error.message_ar, message_en=error.message_en, details=details
            ),
        )


# ------------------------------------------------------------------ admin: guard policies (phase 03)

POLICY_ID_PATH = r"^[a-z0-9][a-z0-9-]{1,63}$"


class PolicyDocument(BaseModel):
    """A policy as the admin API reads and writes it (README §10.1). On create and on a new
    version, `version` is ignored: the store assigns it (I2)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: int | None = None
    # Both names are required; the policy model says so, with the field named (ADR-0119).
    name_ar: str | None = None
    name_en: str | None = None
    description_ar: str = ""
    description_en: str = ""
    purpose: Literal["production", "test"] = Field(
        default="production",
        description="test: made by a test script, hidden from the admin list by default.",
    )
    enabled: bool = True
    scope: str
    checkpoints: list[str]
    checker: str
    config: dict[str, Any] = {}
    action: str
    severity: str = "medium"
    message_ar: str | None = None
    message_en: str | None = None
    on_error: str | None = None
    group: str | None = None


class PolicyWarning(BaseModel):
    """Saved anyway, but worth knowing (ADR-0115)."""

    code: Literal["whole_text_cannot_lift_spans"] = Field(
        description="whole_text_cannot_lift_spans: this exception cannot lift span-based redaction."
    )
    policies: list[str] = Field(description="id@version of the span-based policies it cannot lift.")


class PolicyView(BaseModel):
    policy: PolicyDocument
    created_at: datetime = Field(description="When this version was created.")
    created_by: str
    created_by_username: str | None = Field(
        default=None, description="The creator's username at the time (ADR-0123); none on older rows."
    )
    created_by_name: str | None = Field(
        default=None, description="The creator's display name at the time (ADR-0123); none on older rows."
    )
    updated_at: datetime = Field(description="When the policy's head (current version, switch) last changed.")
    updated_by: str
    overrides: list[str] = Field(
        default=[],
        description="id@version of the wider policies of its group it overrides among the current "
        "versions, recomputed for the whole group on every change (ADR-0108).",
    )
    exception: bool = Field(
        default=False, description="It overrides a stricter policy of its group (ADR-0105, ADR-0108)."
    )
    warnings: list[PolicyWarning] = Field(default=[], description="On a save response only (ADR-0115).")


class PolicyVersionView(BaseModel):
    policy: PolicyDocument
    created_at: datetime
    created_by: str
    created_by_username: str | None = Field(
        default=None, description="The creator's username at the time (ADR-0123); none on older rows."
    )
    created_by_name: str | None = Field(
        default=None, description="The creator's display name at the time (ADR-0123); none on older rows."
    )
    overrides: list[str] = Field(default=[], description="What this version overrode when it was saved.")
    exception: bool = Field(default=False, description="Whether it was an exception when it was saved.")


ChangeKind = Literal["create", "version", "enable", "disable", "import", "seed", "flagged", "unflagged"]


class PolicyChangeView(BaseModel):
    """One change of a policy, written by the agent in the change's transaction (ADR-0108)."""

    changed_at: datetime
    policy_ref: str = Field(description="id@version the change left current.")
    change: ChangeKind = Field(
        description="flagged / unflagged: another policy's change made this one an exception, "
        "or cleared it (ADR-0115)."
    )
    actor: str
    actor_username: str | None = Field(
        default=None, description="The actor's username at the time (ADR-0123); none on older rows."
    )
    actor_name: str | None = Field(
        default=None, description="The actor's display name at the time (ADR-0123); none on older rows."
    )
    exception: bool
    overrides: list[str]
    induced: list["InducedFlag"] = Field(
        default=[], description="Policies whose exception flag this change set or cleared (ADR-0115)."
    )


class InducedFlag(BaseModel):
    policy_ref: str
    exception: bool


class PolicyChanges(BaseModel):
    changes: list[PolicyChangeView] = Field(description="Newest first.")


class PolicyOverrides(BaseModel):
    """What a draft would override if saved now, and whether that makes it an exception."""

    overrides: list[str]
    exception: bool
    warnings: list[PolicyWarning] = []


class PolicyList(BaseModel):
    policies: list[PolicyView]


class PolicyDetail(BaseModel):
    current: PolicyView
    versions: list[PolicyVersionView] = Field(description="Every version, newest first.")


class PolicyEnabled(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class SimulatedPrincipal(BaseModel):
    """A principal for the test panel: which tenant and policy roles to evaluate the policy for."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(default="default", min_length=1, max_length=128)
    policy_roles: list[str] = Field(default=[], max_length=32, description="Full names, policy:{name}.")


class PolicyTestRequest(BaseModel):
    """Evaluates one policy, stored (`policy_id`, its current version, on or off) or a draft
    (`policy`), against sample text on the runtime engine path; nothing is recorded."""

    model_config = ConfigDict(extra="forbid")

    policy_id: str | None = None
    policy: PolicyDocument | None = None
    text: str = Field(min_length=1, max_length=20000)
    principal: SimulatedPrincipal | None = Field(
        default=None, description="Whom to evaluate for; default: the admin, with no policy roles."
    )
    checkpoint: str | None = Field(default=None, description="Default: the policy's first checkpoint.")
    target: str | None = Field(default=None, description="DATA only; default: the first target, else answer.")


class SpanView(BaseModel):
    start: int
    end: int
    entity: str
    score: float | None = None


class PolicyTestResult(BaseModel):
    policy_ref: str
    checkpoint: str
    target: str | None = None
    action: str = Field(description="What the engine decided for this text.")
    matched: bool
    score: float
    explanation: str = Field(description="The checker's explanation in English, as traces record it.")
    explanation_key: str = Field(
        default="", description="The explanation as a key under the web's policies.explanation.* (rule 7)."
    )
    explanation_args: dict[str, str] = Field(
        default={}, description="Arguments of explanation_key; `items` lists names separated by newlines."
    )
    spans: list[SpanView] = Field(description="Matched regions of `text`, by character offsets.")
    output_text: str | None = Field(
        default=None, description="The text after redaction, for text checkpoints."
    )
    message_ar: str | None = None
    message_en: str | None = None
    error: str | None = Field(default=None, description="The checker's error, when it failed (see on_error).")


class PolicyYaml(BaseModel):
    model_config = ConfigDict(extra="forbid")

    yaml: str = Field(min_length=1, max_length=500_000)


class PolicyImportReport(BaseModel):
    created: list[str]
    versioned: list[str]
    switched: list[str]
    unchanged: list[str]


class PolicyMatchRow(BaseModel):
    day: str
    policy_id: str
    action: str
    matches: int


class PolicyMetrics(BaseModel):
    days: int
    rows: list[PolicyMatchRow] = Field(
        description="Matched evaluations of real turns per UTC day, policy and action."
    )


# ------------------------------------------------------------------ attachments (phase 04)


class AttachmentRegistration(BaseModel):
    """The gateway registers an upload it stored in MinIO (ADR-0129)."""

    model_config = ConfigDict(extra="forbid")

    principal: Principal
    name: str = Field(min_length=1, max_length=255)
    mime: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0)
    object_key: str = Field(min_length=1, max_length=512)


class AttachmentWarning(BaseModel):
    """D7 (ADR-0201): `count` pages of an indexed PDF held too little text and were not read."""

    code: Literal["pages_without_text"]
    count: int = Field(ge=1)


class AttachmentView(BaseModel):
    id: str
    conversation_id: str
    name: str
    mime: str
    kind: Literal["pdf", "docx", "xlsx", "txt", "md"]
    size: int
    status: Literal["pending", "indexed", "failed"]
    reason: str | None = Field(default=None, description="Why ingestion failed (failed only).")
    pages: int | None = None
    chunks: int | None = None
    warnings: list[AttachmentWarning] = Field(
        default=[], description="Indexed with something the user should know (D7, ADR-0201)."
    )
    created_at: datetime
    indexed_at: datetime | None = None
    object_key: str = Field(description="Where the gateway reads the bytes; never sent to a browser.")


class AttachmentList(BaseModel):
    attachments: list[AttachmentView] = Field(description="Oldest first.")


class ConversationSummary(BaseModel):
    id: str
    title: str = Field(description="The conversation's first question, at most 80 characters (ADR-0192).")
    last_turn_at: datetime = Field(description="When the latest finished turn ended.")


class ConversationList(BaseModel):
    conversations: list[ConversationSummary] = Field(description="Newest first.")
    next_before: datetime | None = Field(
        default=None, description="Pass as `before` for the next page; absent on the last page."
    )


class HistoryAttachment(BaseModel):
    id: str
    name: str


class SkillView(BaseModel):
    """A skill the caller may use, for the composer's picker (phase 06, ADR-0301): labels from its
    SKILL.md, so a new skill needs no web change."""

    slug: str
    name: SkillLabel
    description: SkillLabel


class SkillList(BaseModel):
    skills: list[SkillView] = Field(description="The skills available to the caller, by slug.")


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(description="The post-guard text, the text later turns see (ADR-0193).")
    turn_id: str | None = Field(default=None, description="A reply's turn.")
    attachments: list[HistoryAttachment] = Field(
        default=[], description="The files sent with a question, when recorded (ADR-0193)."
    )
    citations: list[Citation] = Field(
        default=[], description="A reply's citations, when recorded (ADR-0193)."
    )
    skill: SkillData | None = Field(
        default=None, description="The skill applied to a reply, when one was (phase 06, ADR-0301); additive."
    )


class ConversationMessages(BaseModel):
    conversation_id: str
    messages: list[HistoryMessage] = Field(description="Oldest first; a retracted exchange is left out.")


# ------------------------------------------------------------------ admin: platform settings (ADR-0276)

# A setting's value: seconds or a count, a map from tenant id to a count, or null (no limit of its own).
SettingValue = int | float | dict[str, int] | None


class SettingField(BaseModel):
    key: str = Field(description="The setting's id, e.g. turn.per_user_max or mcp.rag.max_concurrency.")
    group: Literal["turns", "models", "uploads", "mcp"]
    unit: Literal["s", "count", "per_minute"]
    integer: bool
    nullable: bool = Field(description="null is a value: no limit of its own.")
    per_tenant: bool = Field(description="The value is a map from tenant id to a count.")
    minimum: float
    maximum: float
    default: SettingValue = Field(description="The files' value (the environment's, where it sets one).")
    value: SettingValue = Field(description="The value in force: the override, or the default.")
    overridden: bool
    source: str = Field(description="The file and key of the default.")
    server: str | None = Field(default=None, description="An MCP server's id, for its max_concurrency.")


class ReadOnlySetting(BaseModel):
    key: str
    value: int | float | str
    unit: Literal["s", "count", "MiB", "cpu_s"]
    source: str = Field(description="Where it is set; changing it needs a redeploy.")


class TimeoutPair(BaseModel):
    """One nested pair of the timeout budget's rules (app/resilience/budget.py), on the values in force
    or on a draft's."""

    rule: str
    family: str
    params: dict[str, str] = Field(description="The ids the rule names: capability, tool, server.")
    inner: str
    inner_value: float
    outer: str
    outer_value: float
    strict: bool = Field(description="inner < outer when true, inner ≤ outer when false.")
    holds: bool
    parents: list[str] = Field(
        default_factory=list,
        description="The rules whose inner bound is this pair's outer one: its parent rows (ADR-0286).",
    )


class SettingsProblem(BaseModel):
    """Why a draft cannot be saved: a rule it breaks or a range it leaves, in plain words."""

    rule: str
    family: str
    params: dict[str, str]
    inner_value: float | None = None
    outer_value: float | None = None
    minimum: float | None = None
    message_ar: str
    message_en: str


class PlatformSettingsView(BaseModel):
    version: int = Field(description="The stored version in force; 0 for none.")
    ignored: list[str] = Field(
        description="When the stored version fails the rules against the files: the rules it fails, "
        "and the files' values are in force."
    )
    fields: list[SettingField]
    read_only: list[ReadOnlySetting]
    tree: list[TimeoutPair]


class SettingsDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overrides: dict[str, Any] = Field(
        description="Every override the new version holds; a key left out is its default."
    )
    expected_version: int | None = Field(
        default=None, ge=0, description="The version the draft was made from; another save first is 409."
    )


class SettingsRevert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, description="The version whose overrides come back, as a new version.")
    expected_version: int | None = Field(default=None, ge=0)


class SettingsPreview(BaseModel):
    valid: bool
    problems: list[SettingsProblem]
    fields: list[SettingField]
    tree: list[TimeoutPair]
    changed: list[str] = Field(description="The keys whose value in force the draft changes.")


class SettingChange(BaseModel):
    key: str
    before: SettingValue
    after: SettingValue


class SettingsSaved(BaseModel):
    version: int
    change: Literal["save", "revert", "restore"]
    reverted_to: int | None = None
    changes: list[SettingChange] = Field(
        description="Each value in force the save changed, before and after."
    )
    settings: PlatformSettingsView


class SettingsVersionView(BaseModel):
    version: int
    change: Literal["seed", "save", "revert", "restore"]
    reverted_to: int | None = None
    overrides: dict[str, SettingValue]
    created_at: datetime
    created_by: str
    created_by_username: str | None = None
    created_by_name: str | None = None


class SettingsVersions(BaseModel):
    versions: list[SettingsVersionView]


class TenantUsage(BaseModel):
    tenant_id: str
    in_flight: int
    limit: int | None


class TurnsUsage(BaseModel):
    in_flight: int
    capacity: int | None
    per_user_max: int
    top_user_in_flight: int = Field(description="The busiest user's turns in flight: a count, never who.")
    tenants: list[TenantUsage]


class ServerUsage(BaseModel):
    id: str
    max_concurrency: int
    file_max_concurrency: int
    in_use: int
    background_max: int
    background_in_use: int
    breaker: Literal["closed", "open", "half_open"]
    open_for_s: float | None = Field(default=None, description="Seconds since the breaker opened.")


class PlatformUsage(BaseModel):
    turns: TurnsUsage
    mcp: list[ServerUsage]
    registrations_in_background: int
    tasks_abandoned: int = Field(description="Tasks that ignored their cancellation since the agent started.")


class EffectiveLimits(BaseModel):
    """What the gateway enforces itself, from the settings in force (ADR-0283)."""

    version: int
    deadline_s: float
    uploads_per_minute: int
    max_concurrent_uploads: int
