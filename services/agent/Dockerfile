# syntax=docker/dockerfile:1.7
FROM python:3.12.14-slim-bookworm AS base
COPY --from=ghcr.io/astral-sh/uv:0.12.12 /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

FROM base AS deps
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
# Bake the tokenizer file so token counting works offline (app/context/tokens.py).
RUN python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"

FROM deps AS test
# An Arabic-capable font for the PDFs the extractor tests generate (phase 04, G3, G4).
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
ENV ARABIC_TEST_FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
COPY conftest.py ./
COPY app ./app
COPY tests ./tests
# The reference MCP servers (infra/mcp-ref, named context `mcpref`): the contract suite runs the
# reference RAG server in-process against the MCP document backend (phase 05, D2).
COPY --from=mcpref mcp_ref ./mcp_ref
CMD ["pytest", "-q"]

FROM base AS runtime
COPY --from=deps /app/.venv /app/.venv
COPY --from=deps /opt/tiktoken /opt/tiktoken
COPY app ./app
RUN useradd --system --uid 10001 --no-create-home agent
USER agent
EXPOSE 8000
# An idle keep-alive connection is kept 75 s, longer than the gateway keeps one (30 s,
# gateway.agent.keep-alive), so the gateway never sends on a connection the agent has closed
# (timeout budget PR, I8, ADR-0260); uvicorn's own default is 5 s.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "75"]
