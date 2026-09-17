# visualization-service image (Section 28: multi-stage, non-root, health endpoints).
# Build from the repo root:  docker build -f apps/visualization-service/Dockerfile .
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /repo
COPY pyproject.toml uv.lock ./
COPY packages/python ./packages/python
COPY apps/visualization-service ./apps/visualization-service
RUN uv sync --locked --no-dev --package visualization-service

FROM python:3.12-slim AS runtime
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /repo /repo
ENV PATH=/repo/.venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /repo/apps/visualization-service
USER 10001
EXPOSE 8006
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8006/health/live')"
CMD ["uvicorn", "visualization_service.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8006"]
