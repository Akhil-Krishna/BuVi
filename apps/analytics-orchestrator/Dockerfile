# analytics-orchestrator image (Section 28: multi-stage, non-root, health endpoints).
# Build from the repo root:  docker build -f apps/analytics-orchestrator/Dockerfile .
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /repo
COPY pyproject.toml uv.lock ./
COPY packages/python ./packages/python
COPY apps/analytics-orchestrator ./apps/analytics-orchestrator
RUN uv sync --locked --no-dev --package analytics-orchestrator

FROM python:3.12-slim AS runtime
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /repo /repo
ENV PATH=/repo/.venv/bin:$PATH PYTHONUNBUFFERED=1 \
    OTEL_SDK_DISABLED=true CREWAI_DISABLE_TELEMETRY=true CREWAI_DISABLE_TRACKING=true \
    CREWAI_TRACING_ENABLED=false CREWAI_DISABLE_VERSION_CHECK=true
WORKDIR /repo/apps/analytics-orchestrator
USER 10001
EXPOSE 8004
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8004/health/live')"
CMD ["uvicorn", "analytics_orchestrator.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8004"]
