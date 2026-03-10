# Stage 0: Dashboard — build the frontend SPA
FROM node:22-slim AS dashboard
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build


# Stage 1: Builder — install dependencies with uv
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv for fast dependency management (pinned version)
COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /usr/local/bin/uv

# Copy dependency files first (better layer caching)
COPY pyproject.toml uv.lock ./

# Install dependencies into a virtual environment
RUN uv sync --frozen --no-dev --no-install-project

# Copy source code
COPY src/ src/

# Install the project itself
RUN uv sync --frozen --no-dev


# Stage 2: Runtime — minimal image with non-root user
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="AgentWorks" \
      org.opencontainers.image.description="Self-hosted AI agent runtime with state machine orchestration" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.source="https://github.com/your-org/agentworks" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Create non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash appuser

# Copy virtual environment and source from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# Copy built dashboard from Stage 0
COPY --from=dashboard /dashboard/dist /app/dashboard/dist

# Ensure venv binaries are on PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Switch to non-root user
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health/live')" || exit 1

CMD ["uvicorn", "agentworks.api:app", "--host", "0.0.0.0", "--port", "8000"]
