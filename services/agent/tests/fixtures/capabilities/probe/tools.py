import json

from pydantic import BaseModel

from app.tools.spec import ToolOutput, ToolSpec


class LookupArgs(BaseModel):
    text: str


async def _lookup(args: LookupArgs, ctx) -> ToolOutput:
    return ToolOutput(content=f"lookup:{args.text}", sources=[{"id": "probe-1"}])


class FindArgs(BaseModel):
    query: str


# By query: one option (chosen), several (none chosen), or none. The list sits under
# `options.items`, a shape the declarative runner knows nothing of (ADR-0238).
_OPTIONS = {"واحد": ["الأول"], "عدة": ["الأول", "الثاني", "الثالث", "الرابع"], "لا شيء": []}


async def _find(args: FindArgs, ctx) -> ToolOutput:
    names = _OPTIONS.get(args.query, [])
    data = {
        "query": args.query,
        "options": {"items": [{"name": n} for n in names]},
        "chosen": {"name": names[0]} if len(names) == 1 else None,
    }
    return ToolOutput(content=json.dumps(data, ensure_ascii=False), data=data)


def tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="probe_lookup",
            description_ar="بحث تجريبي",
            description_en="Test lookup",
            input_schema=LookupArgs,
            risk="read",
            required_permission="cap:probe",
            timeout_s=5,
            max_retries=0,
            handler=_lookup,
        ),
        ToolSpec(
            name="probe_find",
            description_ar="بحث تجريبي بقائمة",
            description_en="Test find with a list",
            input_schema=FindArgs,
            risk="read",
            required_permission="cap:probe",
            timeout_s=5,
            max_retries=0,
            handler=_find,
        ),
    ]
