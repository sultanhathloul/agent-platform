"""Request and response bodies of the internal agent API (README §6.2)."""

from pydantic import BaseModel, ConfigDict, Field

from app.api.events import ErrorData
from app.identity import Locale, Principal


class AttachmentRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    mime: str
    size: int = Field(ge=0)


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=64)
    principal: Principal
    message: str = Field(min_length=1, max_length=8000)
    attachments: list[AttachmentRef] = []
    selected_kbs: list[str] = []
    locale: Locale = "ar"


class HealthResponse(BaseModel):
    status: str
    capabilities: list[str]


class ApiError(BaseModel):
    code: str
    message_ar: str
    message_en: str


class ErrorEnvelope(BaseModel):
    request_id: str
    error: ApiError

    @classmethod
    def of(cls, request_id: str, error: ErrorData) -> "ErrorEnvelope":
        return cls(
            request_id=request_id,
            error=ApiError(code=error.code, message_ar=error.message_ar, message_en=error.message_en),
        )
