# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv
FROM python:3.13.18-slim-bookworm@sha256:c3f3ace1c09d3d90cb79b17c6a1037282e82df75a8870929b2cbc667ddb7898e
COPY --from=uv /uv /uvx /usr/local/bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin sandbox \
    && mkdir /data && chown sandbox:sandbox /data
COPY mock-services/service.py ./service.py
USER 10001:10001
CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "9001", "--no-access-log"]
