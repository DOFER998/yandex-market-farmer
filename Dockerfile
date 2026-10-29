# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.13.5 AS uv
FROM python:3.13-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE THIRD_PARTY_NOTICES.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --extra server

FROM python:3.13-slim-bookworm
RUN groupadd --gid 1000 farmer && useradd --uid 1000 --gid farmer --no-create-home farmer
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY src ./src
COPY scripts/__init__.py scripts/health.py scripts/account.py ./scripts/
COPY alembic.ini LICENSE THIRD_PARTY_NOTICES.md ./
COPY migrations ./migrations
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
LABEL org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/DOFER998/yandex-market-farmer"
USER 1000:1000
CMD ["python", "-m", "src.main"]
