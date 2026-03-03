FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Create non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --create-home appuser

# Create data directory for SQLite volume mount
RUN mkdir -p /data && chown appuser:appuser /data

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock .python-version ./

# Install dependencies without the project itself
RUN uv sync --frozen --no-install-project

# Copy source code
COPY src/ src/
COPY README.md ./

# Install the project
RUN uv sync --frozen

# Switch to non-root user
USER appuser

EXPOSE 8000

ENV DATABASE_URL=sqlite:////data/rankings.db

CMD ["uv", "run", "uvicorn", "prague_lions_ranking.main:app", "--host", "0.0.0.0", "--port", "8000"]
