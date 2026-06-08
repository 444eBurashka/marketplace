FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

RUN useradd --create-home --shell /bin/false appuser

WORKDIR /app
RUN chown appuser:appuser /app

COPY --chown=appuser:appuser b2c/pyproject.toml ./
COPY --chown=appuser:appuser b2c/app/ ./app/
COPY --chown=appuser:appuser b2c/alembic/ ./alembic/
COPY --chown=appuser:appuser b2c/alembic.ini ./
COPY --chown=appuser:appuser shared/ ./shared/

USER appuser

RUN uv sync --no-dev

ENV PYTHONPATH="/app"

EXPOSE 8002
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 8002"]
