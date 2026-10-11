"""Request and response bodies of the internal agent API (README §6.2)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.api.events import ErrorData
from app.identity import Locale, Principal

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


class AttachmentRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    mime: str
    size: int = Field(ge=0)


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(
        pattern=CONVERSATION_ID_PATTERN, description="UUID, lower-case; the gateway normalises it (ADR-0050)."
    )
    principal: Principal
    message: str = Field(min_length=1, max_length=8000)
    attachments: list[AttachmentRef] = []
    selected_kbs: list[str] = []
    locale: Locale = "ar"


class HealthResponse(BaseModel):
    status: str
    capabilities: list[str]


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
