FROM python:3.12-slim AS base
WORKDIR /srv
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
CMD ["uvicorn", "mpmt.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
