"""The declarative capability runner (phase 05, I12, ADR-0213). A capability whose manifest has a
`declarative` section and no graph.py gets its subgraph from here; the core compiles it like any
other (ADR-0053) and runs it behind the same boundary, with the same RuntimeContext. Capability-
agnostic: it names no capability, tool, server or result shape.

1. Extract: one structured-output call (the section's role and prompt, the capability's own) fills
   the extract schema from the message; the answer is validated against the schema, and an invalid
   one gets one repair call. A required field still empty stops the run: the findings say what is
   missing, and the answer asks the user for it (`on_missing: ask`).
2. Steps, in order, each one broker call. Arguments are mapped deterministically from
   `$.extract.<field>`, `$.steps.<id>.result.<path>` or literals; the model never fills a tool's
   arguments, and the broker validates every call against the tool's (pinned) schema. A step
   that returns nothing, or whose declared result list (`result_list`) holds fewer items than
   `empty_when` says, stops the run (`empty`). A reference that resolves to nothing stops it too:
   `ambiguous` when the step it names declared `ambiguous_when` and its list holds more items
   than that, `unresolved` otherwise; up to three items of that list are the candidates (for
   example places to choose from) (ADR-0238). A tool that fails stops the run with its
   content-free code.
3. Findings: the section's prompt renders what happened (the extract, each step's result, where
   and why it stopped) for the answer. Step results arrive already checked at POST_TOOL
   (sanitize_untrusted first, every string and key of a structured result, ADR-0208, ADR-0234).
   Whatever the capability's template says, the core then appends each step's result inside the
   untrusted delimiters, rendered by `context.tool_result` (ADR-0239), and the findings reach the
   answer in the user role (ADR-0165).

No expressions, conditionals, loops or parallelism (phase 07 may supersede this syntax)."""

import json
import re
from pathlib import Path
from typing import Any, TypedDict

from jsonschema import Draft202012Validator
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.capabilities.contract import (
    CapabilityInput,
    CapabilityOutput,
    DeclarativeSpec,
    Manifest,
    RuntimeContext,
    StepSpec,
)
from app.models.prompts import PromptLibrary
from app.settings import _default_config_dir

TOOL_RESULT_PROMPT = "context.tool_result"

_JSON = re.compile(r"\{.*\}", re.S)
MAX_CANDIDATES = 3


class _State(TypedDict, total=False):
    input: CapabilityInput
    output: CapabilityOutput


class _Missing(Exception):
    """A reference resolved to nothing."""


def _parse(text: str) -> dict[str, Any] | None:
    match = _JSON.search(text or "")
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _resolve(value: Any, extract: dict[str, Any], results: dict[str, Any]) -> Any:
    """A literal as it is; a reference to the extract or to an earlier step's result, or _Missing."""
    if not (isinstance(value, str) and value.startswith("$.")):
        return value
    parts = value.split(".")[1:]
    if parts[0] == "extract":
        found = extract.get(parts[1])
    else:  # steps.<id>.result.<path>
        found = results.get(parts[1])
        for key in parts[3:]:
            if isinstance(found, list) and key.isdigit() and int(key) < len(found):
                found = found[int(key)]
            elif isinstance(found, dict) and key in found:
                found = found[key]
            else:
                found = None
                break
    if _empty(found):
        raise _Missing(value)
    return found


def _arguments(
    args: dict[str, Any], extract: dict[str, Any], results: dict[str, Any], optional: set[str]
) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in args.items():
        try:
            resolved[key] = _resolve(value, extract, results)
        except _Missing:
            if value in optional:
                continue
            raise
    return resolved


def _result(content: str, data: Any) -> Any:
    if data is not None:
        return data
    parsed = _parse(content)
    return parsed if parsed is not None else ({"text": content} if content else None)


def _step_of(reference: str) -> str | None:
    parts = reference.split(".")
    return parts[2] if len(parts) > 2 and parts[1] == "steps" else None


def _unresolved(steps: dict[str, StepSpec], results: dict[str, Any], step_id: str | None) -> dict[str, Any]:
    """Why a reference into `step_id` resolved to nothing, by that step's declared list."""
    spec = steps.get(step_id) if step_id else None
    items = spec.list_of(results.get(step_id)) if spec is not None else None
    if items is None:
        return {"status": "unresolved", "candidates": []}
    ambiguous = spec.ambiguous_when is not None and len(items) > spec.ambiguous_when.more_than
    return {"status": "ambiguous" if ambiguous else "unresolved", "candidates": items[:MAX_CANDIDATES]}


def build_declarative(
    manifest: Manifest, directory: Path, core_prompts: PromptLibrary | None = None
) -> StateGraph:
    """The uncompiled subgraph of a declarative capability; the core compiles it (ADR-0053).
    `core_prompts`: the core's prompt library (config/prompts), for `context.tool_result`."""
    spec: DeclarativeSpec = manifest.declarative  # type: ignore[assignment]
    prompts = PromptLibrary.load(directory / "prompts")
    for prompt in (spec.extract.prompt, spec.findings.prompt):
        prompts.require(prompt)
    core = core_prompts or PromptLibrary.load(_default_config_dir() / "prompts")
    core.require(TOOL_RESULT_PROMPT)
    by_id = {step.id: step for step in spec.steps}
    schema = spec.extract.schema_
    validator = Draft202012Validator(schema)
    required = [f for f in schema.get("required", []) if f in spec.extract.fields]
    # An optional extract field left empty leaves its argument out, so the tool's default applies.
    optional = {f"$.extract.{f}" for f in spec.extract.fields if f not in required}
    schema_text = json.dumps(schema, ensure_ascii=False, sort_keys=True)

    async def extract(rt: RuntimeContext) -> tuple[dict[str, Any] | None, int]:
        problems = 0
        for repair in (False, True):
            result = await rt.call_model(
                spec.extract.role,
                step="extract",
                prompt=spec.extract.prompt,
                prompt_vars={"schema": schema_text, "repair": repair, "problems": problems},
            )
            parsed = _parse(result.text)
            if parsed is not None:
                problems = sum(1 for _ in validator.iter_errors(parsed))
                if problems == 0:
                    return parsed, int(repair)
            else:
                problems = 1
        return None, 1

    async def run(state: _State, runtime: Runtime[RuntimeContext]) -> dict:
        rt = runtime.context
        request = state["input"]
        view: dict[str, Any] = {"status": "complete", "extract": {}, "steps": {}, "stopped_at": None}
        extracted, repairs = await extract(rt)
        if extracted is None:
            view["status"] = "not_understood"
        else:
            view["extract"] = extracted
            missing = [f for f in required if _empty(extracted.get(f))]
            if missing:
                view.update(status="missing", missing=missing)
        if view["status"] == "complete":
            results: dict[str, Any] = {}
            for step in spec.steps:
                try:
                    args = _arguments(step.args, view["extract"], results, optional)
                except _Missing as missing_ref:
                    reference = str(missing_ref)
                    view.update(
                        stopped_at=step.id,
                        reference=reference,
                        unresolved_step=_step_of(reference),
                        **_unresolved(by_id, results, _step_of(reference)),
                    )
                    break
                outcome = await rt.broker.execute(step.tool, args)
                if outcome.status != "ok":
                    view.update(
                        status="tool_failed", stopped_at=step.id, error=outcome.error or outcome.status
                    )
                    break
                result = _result(outcome.content, outcome.data)
                items = step.list_of(result)
                if _empty(result) or (items is not None and len(items) < step.empty_when.fewer_than):
                    if not _empty(result):
                        results[step.id] = result
                    view.update(status="empty", stopped_at=step.id, candidates=[])
                    break
                results[step.id] = result
            view["steps"] = results
        findings = prompts.render(spec.findings.prompt, request.locale, **view).text
        # The core delimits every step result, whatever the template rendered (ADR-0239).
        delimited = [
            core.render(
                TOOL_RESULT_PROMPT,
                request.locale,
                tool=by_id[step_id].tool,
                content=json.dumps(result, ensure_ascii=False, sort_keys=True),
            ).text
            for step_id, result in view["steps"].items()
        ]
        findings = "\n\n".join([findings.strip(), *delimited]) if delimited else findings
        runs = int(rt.cap_state.get("runs", 0)) + 1
        repaired = int(rt.cap_state.get("repairs", 0)) + repairs
        state_out = {"runs": runs, "last_status": view["status"], "repairs": repaired}
        return {"output": CapabilityOutput(findings=findings, cap_state=state_out)}

    graph = StateGraph(_State, context_schema=RuntimeContext)
    graph.add_node("run", run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return graph
