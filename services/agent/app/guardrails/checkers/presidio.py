"""`presidio` (README §10.2): PII entities from the presidio-analyzer service with the
custom Saudi recognizers mounted from infra/presidio/ (mobile, national ID / iqama,
SA IBAN, email, the Arabic-name heuristic). The text is sent folded with offsets
kept (digits to ASCII, tatweel and tashkeel removed), so `٠٥٥ـ١٢٣ـ٤٥٦٧` is
recognized, and spans are mapped back to the original text. The entity list and
score threshold come from the policy's config."""

import httpx

from app.guardrails.checkers.base import CheckContext, CheckerError
from app.guardrails.schema import CheckResult, PresidioConfig, Span
from app.text.arabic import fold_with_map, span_in_source


class PresidioChecker:
    name = "presidio"

    def __init__(self, base_url: str, *, timeout_s: float = 5.0, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout_s)

    async def check(self, payload: str, config: PresidioConfig, ctx: CheckContext) -> CheckResult:
        if not payload.strip():
            return CheckResult(matched=False, explanation="empty text", reason="empty_text")
        folded, mapping = fold_with_map(payload, letters=False)
        body = {
            "text": folded,
            "language": config.language,
            "entities": config.entities,
            "score_threshold": config.score_threshold,
        }
        try:
            response = await self._client.post("/analyze", json=body)
            response.raise_for_status()
            found = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CheckerError(f"presidio-analyzer: {type(exc).__name__}") from exc
        spans = []
        for item in found:
            if item["entity_type"] not in config.entities or item["score"] < config.score_threshold:
                continue
            start, end = span_in_source(mapping, item["start"], item["end"], len(payload))
            spans.append(
                Span(start=start, end=end, entity=item["entity_type"], score=round(item["score"], 4))
            )
        spans.sort(key=lambda span: (span.start, -span.end))
        names = sorted({span.entity for span in spans})
        return CheckResult(
            matched=bool(spans),
            spans=spans,
            score=max((span.score or 0.0 for span in spans), default=0.0),
            explanation=f"entities: {', '.join(names)}" if spans else "no entity",
            reason="entities" if spans else "no_entity",
            reason_args={"items": "\n".join(names)} if spans else {},
        )

    async def close(self) -> None:
        await self._client.aclose()
