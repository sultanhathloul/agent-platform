# syntax=docker/dockerfile:1.7
# The reference MCP servers (phase 05, I10, ADR-0211): one image, two containers
# (`python -m mcp_ref.routing`, `python -m mcp_ref.rag`). Built on the agent's dependencies and
# its shared libraries (`app/`, from the named build context `agent`), so the RAG server runs the
# phase 04 pipeline unchanged and the routing server folds Arabic with app/text/arabic.py.
FROM python:3.12.14-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.12 /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY --from=agent pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
RUN python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"
COPY --from=agent app ./app
COPY mcp_ref ./mcp_ref
RUN useradd --system --uid 10002 --no-create-home mcpref
USER mcpref
EXPOSE 8000
CMD ["python", "-m", "mcp_ref.routing"]
