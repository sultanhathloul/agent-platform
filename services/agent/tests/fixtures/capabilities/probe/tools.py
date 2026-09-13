from pydantic import BaseModel

from app.tools.spec import ToolOutput, ToolSpec


class LookupArgs(BaseModel):
    text: str


async def _lookup(args: LookupArgs) -> ToolOutput:
    return ToolOutput(content=f"lookup:{args.text}", sources=[{"id": "probe-1"}])


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
        )
    ]
