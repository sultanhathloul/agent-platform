"""Block producers of the Context Assembly pipeline (README §7 steps 1–8). Each
step keeps its block within the block's budget in config/context.yaml and
records what it cut; the budget guard enforces the tier budget afterwards."""

from app.context.budget import Budget
from app.context.compress import extractive, extractive_delimited, truncate
from app.context.messages import visible
from app.context.package import ContextPackage
from app.context.selectors.base import Step


class SystemInstructions(Step):
    name = "system_instructions"
    block = "system"

    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage:
        req = package.request
        prompt = self.deps.prompts.render(
            req.system_prompt,
            req.locale,
            tools=req.tools,
            has_evidence=any(f.get("findings") for f in req.findings)
            or bool(req.retrieved or req.tool_results),
            policy_hints=req.policy_hints,
            **req.prompt_vars,
        )
        package.blocks.append(self.make_block("system", "system", prompt.text, prompt=prompt.ref))
        package.prompt_refs.append(prompt.ref)
        return package


class StateDigest(Step):
    name = "state_digest"
    block = "state_digest"

    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage:
        req, state = package.request, package.request.state
        facts = {
            "selected_kbs": state.get("selected_kbs") or [],
            "decisions": state.get("decisions") or [],
            "open_threads": state.get("open_threads") or [],
            "artifacts": [a.get("name") or a.get("id", "") for a in state.get("artifacts") or []],
        }
        if not any(facts.values()):
            # Nothing to report. An empty digest costs tokens and measurably
            # degrades the fast tier's answers (ADR-0023).
            return package
        prompt = self.deps.prompts.render("context.state_digest", req.locale, **facts)
        text = truncate(prompt.text, self.budget_for(package).max_tokens, self.deps.counter)
        if text != prompt.text:
            self.drop(
                package, "state_digest", self.deps.counter.count(prompt.text[len(text) :]), "block_budget"
            )
        package.blocks.append(self.make_block("state_digest", "system", text, prompt=prompt.ref))
        package.prompt_refs.append(prompt.ref)
        return package


class RollingSummary(Step):
    """`state.summary`, the rolling summary of messages older than the window
    (README §7 step 4). Its newest part is kept when it exceeds its budget."""

    name = "rolling_summary"
    block = "summary"

    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage:
        req = package.request
        summary = (req.state.get("summary") or "").strip()
        if not summary:
            return package
        config = self.deps.config
        limit = config.compress.summary_max_tokens if req.compress else self.budget_for(package).max_tokens
        block = render_summary(self, package, summary, limit, reason="block_budget")
        if block is not None:
            package.blocks.append(block)
            package.prompt_refs.append(block.meta["prompt"])
        return package


def render_summary(step: Step, package: ContextPackage, summary: str, limit: int, *, reason: str):
    """The summary block within `limit` tokens, or None when not even the header fits.
    Shared with the budget guard, which shortens the summary last."""
    counter, locale = step.deps.counter, package.locale
    header = step.deps.prompts.render("context.summary", locale, summary="")
    kept = truncate(summary, limit - counter.count(header.text) - 1, counter, keep="end")
    if kept != summary:
        step.drop(package, "summary", counter.count(summary) - counter.count(kept), reason)
    if not kept:
        return None
    prompt = step.deps.prompts.render("context.summary", locale, summary=kept)
    # User-derived text is a delimited data block in the user role, never system (ADR-0068).
    return step.make_block("summary", "user", prompt.text, prompt=prompt.ref, summary=kept)


class SlidingWindow(Step):
    """The last N messages after the summary cursor, raw. `state.messages` holds
    earlier turns only; the current user message is added by UserMessage."""

    name = "sliding_window"
    block = "window"

    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage:
        req, config = package.request, self.deps.config
        # Pairs are found on the whole history, so a retracted reply's question goes too (ADR-0076).
        history = visible(req.state.get("messages") or [])[req.state.get("summary_cursor") or 0 :]
        if req.window_messages is not None:
            size = req.window_messages
        else:
            size = config.compress.window_messages if req.compress else config.window_messages
        recent = history[-size:] if size else []
        blocks = [self.make_block("window", parsed[0], parsed[1]) for parsed in recent if parsed]
        limit = self.budget_for(package).max_tokens
        while blocks and sum(b.tokens for b in blocks) > limit:
            oldest = blocks.pop(0)
            self.drop(package, "window", oldest.tokens, "block_budget")
        package.blocks.extend(blocks)
        return package


class MemoryRetrieval(Step):
    """Top-k memory items of the request's principal by similarity to the message
    (README §7 step 5). The scope is set by core code, never from state."""

    name = "memory_retrieval"
    block = "memory"

    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage:
        req = package.request
        if req.memory_scope is None or self.deps.memory is None:
            return package
        spec = self.budget_for(package)
        top_k = self.deps.config.compress.memory_top_k if req.compress else spec.top_k
        if not top_k:
            return package
        hits = await self.deps.memory.retrieve(req.memory_scope, req.state.get("user_message") or "", top_k)
        counter = self.deps.counter
        while hits:
            prompt = self.deps.prompts.render(
                "context.memory", req.locale, items=[{"text": h.text} for h in hits]
            )
            if counter.count(prompt.text) <= spec.max_tokens:
                break
            weakest = min(hits, key=lambda hit: hit.similarity)
            hits.remove(weakest)
            self.drop(package, "memory", counter.count(weakest.text), "block_budget")
        if not hits:
            return package
        items = [{"id": h.id, "kind": h.kind, "similarity": round(h.similarity, 4)} for h in hits]
        # User-derived text is a delimited data block in the user role, never system (ADR-0068).
        package.blocks.append(self.make_block("memory", "user", prompt.text, prompt=prompt.ref, items=items))
        package.prompt_refs.append(prompt.ref)
        return package


class SemanticRetrieval(Step):
    """Chunks retrieved for docs, kb and sql nodes (README §7 step 6): the top-k by
    score, one block per chunk, compressed extractively when over budget. The
    node's form decides full text, a summary, or a reference only (step 8)."""

    name = "semantic_retrieval"
    block = "retrieval"

    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage:
        req, counter = package.request, self.deps.counter
        if not req.retrieved:
            return package
        spec = self.budget_for(package)
        top_k = self.deps.config.compress.retrieval_top_k if req.compress else spec.top_k
        ranked = sorted(req.retrieved, key=lambda chunk: chunk.get("score") or 0.0, reverse=True)
        for chunk in ranked[top_k:]:
            self.drop(package, "retrieval", counter.count(chunk.get("text") or ""), "top_k")
        chunks = ranked[:top_k]
        if not chunks:
            return package
        form = self.deps.config.form(package.node, "retrieval")
        total = sum(counter.count(chunk.get("text") or "") for chunk in chunks)
        share = spec.max_tokens // max(len(chunks), 1)
        question = req.state.get("user_message") or ""
        for chunk in chunks:
            text = chunk.get("text") or ""
            if form == "reference":
                kept = ""
            elif form == "summary" or total > spec.max_tokens:
                kept = extractive(text, question, share, counter)
            else:
                kept = text
            if kept != text:
                reason = "block_budget" if form == "full" else f"form_{form}"
                self.drop(package, "retrieval", counter.count(text) - counter.count(kept), reason)
            # Compression works on the chunk's text alone; the delimiters are rendered around
            # what is kept, so they are never cut (owner decision S2).
            prompt = self.deps.prompts.render(
                "context.retrieval",
                req.locale,
                source=chunk.get("source") or "",
                kb=chunk.get("kb"),
                page=chunk.get("page"),
                locator=chunk.get("locator"),
                text=kept,
            )
            # Untrusted data travels in the user role, with the question, never as system
            # instructions (ADR-0068, ADR-0076; owner decision S2).
            package.blocks.append(
                self.make_block(
                    "retrieval",
                    "user",
                    prompt.text,
                    prompt=prompt.ref,
                    id=chunk.get("id"),
                    document_id=chunk.get("document_id"),
                    score=chunk.get("score"),
                )
            )
        package.prompt_refs.append(prompt.ref)
        return package


class ToolResults(Step):
    """This turn's tool results only (README §7 step 7), already sanitized by
    POST_TOOL, each truncated to its share of the block budget."""

    name = "tool_results"
    block = "tool_results"

    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage:
        req, counter = package.request, self.deps.counter
        if not req.tool_results:
            return package
        config = self.deps.config
        limit = (
            config.compress.tool_results_max_tokens if req.compress else self.budget_for(package).max_tokens
        )
        share = limit // len(req.tool_results)
        form = config.form(package.node, "tool_results")
        question = req.state.get("user_message") or ""
        for result in req.tool_results:
            content = result.get("content") or ""
            if form == "reference":
                kept = ""
            elif form == "summary":
                kept = extractive(content, question, share, counter)
            else:
                kept = truncate(content, share, counter)
            if kept != content:
                reason = "block_budget" if form == "full" else f"form_{form}"
                self.drop(package, "tool_results", counter.count(content) - counter.count(kept), reason)
            prompt = self.deps.prompts.render(
                "context.tool_result", req.locale, tool=result.get("tool") or "", content=kept
            )
            package.blocks.append(self.make_block("tool_results", "user", prompt.text, prompt=prompt.ref))
        package.prompt_refs.append(prompt.ref)
        return package


class CapabilityFindings(Step):
    """What this turn's capabilities found (README §7 step 8): in full, compressed
    extractively when over budget, or as references only, per the node's form."""

    name = "capability_findings"
    block = "findings"

    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage:
        req, counter = package.request, self.deps.counter
        findings = [dict(f) for f in req.findings if f.get("findings")]
        if not findings:
            return package
        form = self.deps.config.form(package.node, "findings")
        limit = self.budget_for(package).max_tokens
        total = sum(counter.count(f["findings"]) for f in findings)
        share = limit // len(findings)
        for finding in findings:
            text = finding["findings"]
            if form == "reference":
                finding["findings"] = ""
            elif form == "summary" or total > limit:
                # Inside each delimited block only: a delimiter line is never cut (S2).
                question = req.state.get("user_message") or ""
                finding["findings"] = extractive_delimited(text, question, share, counter)
            if finding["findings"] != text:
                reason = "block_budget" if form == "full" else f"form_{form}"
                self.drop(
                    package, "findings", counter.count(text) - counter.count(finding["findings"]), reason
                )
        prompt = self.deps.prompts.render("context.findings", req.locale, findings=findings)
        # Evidence is data in the user role, never system (ADR-0076 principle; owner decision S2).
        package.blocks.append(self.make_block("findings", "user", prompt.text, prompt=prompt.ref))
        package.prompt_refs.append(prompt.ref)
        return package


class UserMessage(Step):
    name = "user_message"
    block = "user_message"

    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage:
        req = package.request
        if req.user_prompt:
            # Helper nodes (summarizer, memory writer) ask with a versioned prompt, not a user message.
            prompt = self.deps.prompts.render(req.user_prompt, req.locale, **req.prompt_vars)
            package.blocks.append(self.make_block("user_message", "user", prompt.text, prompt=prompt.ref))
            package.prompt_refs.append(prompt.ref)
            return package
        package.blocks.append(self.make_block("user_message", "user", req.state.get("user_message", "")))
        return package
