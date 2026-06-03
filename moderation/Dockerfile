FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

WORKDIR /app

COPY pyproject.toml ./
RUN uv sync --no-dev

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./

RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

EXPOSE 8003
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 8003"]