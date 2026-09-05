FROM python:3.12-slim AS base
WORKDIR /srv
COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir .
CMD ["uvicorn", "marko.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
