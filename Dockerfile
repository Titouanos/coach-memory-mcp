# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    MIGRATIONS_DIR=/app/migrations

# uv drives the pyproject-based install.
RUN pip install --no-cache-dir uv

WORKDIR /app

# 1) Resolve & install dependencies first (cached until pyproject changes).
#    README is referenced by pyproject metadata, so it must be present.
COPY pyproject.toml README.md ./
RUN uv sync --no-dev --no-install-project

# 2) Add the source + migrations and install the project itself.
COPY src ./src
COPY migrations ./migrations
RUN uv sync --no-dev

# Drop privileges.
RUN useradd --system --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["python", "-m", "coach_memory.server"]
