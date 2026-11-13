# syntax=docker/dockerfile:1
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY config ./config
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.13-slim AS runtime
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
RUN useradd --system --create-home --uid 10001 app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
USER app
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-m", "xcopilot.healthcheck"]
CMD ["python", "-m", "xcopilot.main"]
