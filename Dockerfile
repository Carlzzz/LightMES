FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
# Install dependencies first (cached layer) without building the project itself
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# Now copy the project source + readme and install the project
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
EXPOSE 8000
CMD ["sh", "-c", "uv run alembic upgrade head && exec uv run uvicorn lightmes.main:app --host 0.0.0.0 --port 8000"]
