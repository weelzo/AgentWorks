"""
Phase 8: FastAPI API

HTTP interface for the Agent Runtime Engine. Exposes:
  - /api/v1/runs    — Start, query, and resume agent runs
  - /api/v1/tools   — Self-service tool registration and management
  - /api/v1/health  — Liveness, readiness, and tool health checks
  - /api/v1/admin   — LLM provider status and runtime info

Every request gets:
  - A unique request ID (X-Request-ID header)
  - An OpenTelemetry trace span
  - Structured error responses with correlation IDs

Dependency injection:
  Components (ToolRegistry, ExecutionEngine, etc.) are wired through
  FastAPI's Depends() system. RuntimeConfig drives the assembly.
  In tests, override dependencies with mocks via app.dependency_overrides.
"""

from __future__ import annotations

import hmac
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agentworks.checkpoint import CheckpointManager
from agentworks.config import RuntimeConfig
from agentworks.engine import ExecutionEngine
from agentworks.llm_gateway import LLMGateway, LLMProvider
from agentworks.observability import ObservabilityManager
from agentworks.state_machine import (
    AgentState,
    ExecutionContext,
    StateMachine,
    create_agent_state_machine,
)
from agentworks.tool_registry import (
    ToolDefinition,
    ToolRegistry,
    ToolStatus,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Request / Response models
# --------------------------------------------------------------------------


class RunRequest(BaseModel):
    """Request body for starting a new agent run."""

    message: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="The user message / task for the agent.",
    )
    agent_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Agent identifier (which agent to use).",
    )
    team_id: str = Field(
        default="",
        max_length=128,
        description="Team identifier for cost attribution.",
    )
    project_id: str = Field(
        default="",
        max_length=128,
        description="Project identifier for cost attribution.",
    )
    max_iterations: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="Override default max iterations.",
    )
    max_budget_usd: float | None = Field(
        default=None,
        ge=0.01,
        le=100.0,
        description="Override default budget.",
    )
    system_prompt: str | None = Field(
        default=None,
        max_length=5000,
        description="Optional system prompt override.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque metadata passed through to the execution context.",
    )


class RunResponse(BaseModel):
    """Response for a completed or in-progress run."""

    run_id: str
    agent_id: str
    team_id: str
    state: str
    outcome: str | None = None
    iteration_count: int = 0
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    duration_ms: float | None = None
    error: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    state_history: list[dict[str, Any]] = Field(default_factory=list)
    token_usage: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    completed_at: str | None = None


class ErrorSummary(BaseModel):
    """Compact error summary for run listings."""

    retryable: int = 0  # tool calls with retry_count > 0
    recoverable: int = 0  # tool calls with error in output_data
    fatal: int = 0  # tool calls with error field set


class RunListItem(BaseModel):
    """Summary item for run listing."""

    run_id: str
    agent_id: str
    team_id: str
    state: str
    outcome: str | None = None
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    iteration_count: int = 0
    created_at: str | None = None
    error_summary: ErrorSummary = Field(default_factory=ErrorSummary)


class ToolResponse(BaseModel):
    """Response for tool registration."""

    tool_id: str
    version: str
    status: str
    schema_hash: str
    registered_at: str


class ToolListItem(BaseModel):
    """Summary of a registered tool."""

    tool_id: str
    name: str
    version: str
    status: str
    owner_team: str
    tags: list[str]
    total_calls: int
    avg_latency_ms: float


class ToolDetailResponse(BaseModel):
    """Full detail of a registered tool."""

    definition: dict[str, Any]
    status: str
    stats: dict[str, Any]
    registered_at: str
    updated_at: str


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    uptime_seconds: float
    checks: dict[str, str] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Structured error response."""

    error: str
    detail: str | None = None
    request_id: str | None = None


# --------------------------------------------------------------------------
# Application state (populated during lifespan)
# --------------------------------------------------------------------------


class AppState:
    """Holds all runtime components. Assembled during startup."""

    def __init__(self) -> None:
        self.config: RuntimeConfig = RuntimeConfig()
        self.tool_registry: ToolRegistry = ToolRegistry()
        self.state_machine: StateMachine = create_agent_state_machine()
        self.checkpoint_mgr: CheckpointManager | None = None
        self.llm_gateway: LLMGateway | None = None
        self.engine: ExecutionEngine | None = None
        self.observability: ObservabilityManager = ObservabilityManager.create_noop()
        self.start_time: float = time.monotonic()
        # Graceful shutdown state
        self._shutting_down: bool = False
        self._active_runs: set[str] = set()


_app_state = AppState()


# --------------------------------------------------------------------------
# Lifespan: startup and shutdown
# --------------------------------------------------------------------------


def _resolve_secret(ref: str) -> str:
    """Resolve a secret reference to its actual value.

    Supports:
      - "env:VAR_NAME" → reads from environment variable
      - plain string → used as-is (for local dev)
    """
    if not ref:
        return ""
    if ref.startswith("env:"):
        var_name = ref[4:]
        value = os.environ.get(var_name, "")
        if not value:
            logger.warning("Secret env var %s is empty or not set", var_name)
        return value
    return ref


async def _ensure_schema(pool: Any) -> None:
    """Create the agent_checkpoints table if it doesn't exist.

    Safe to call on every startup — uses IF NOT EXISTS.
    """
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_checkpoints (
                run_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                team_id TEXT NOT NULL DEFAULT '',
                checkpoint_version INTEGER NOT NULL,
                state_snapshot JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                size_bytes INTEGER NOT NULL DEFAULT 0,
                checksum TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (run_id, checkpoint_version)
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_checkpoints_agent
            ON agent_checkpoints (agent_id)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_checkpoints_created
            ON agent_checkpoints (created_at)
        """)
    logger.info("Database schema ensured (agent_checkpoints table)")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Application lifespan handler.

    Startup: Load config, wire components, configure observability.
    Shutdown: Close HTTP clients, flush telemetry.
    """
    config = RuntimeConfig.from_env()
    _app_state.config = config
    _app_state.start_time = time.monotonic()

    # Production config validation
    if config.environment == "production":
        warnings = config.validate_for_production()
        critical = [w for w in warnings if w.startswith("CRITICAL")]
        for w in warnings:
            logger.warning("Production config: %s", w)
        if critical:
            logger.error(
                "Blocking startup: %d critical config issues",
                len(critical),
            )
            raise RuntimeError(f"Production config validation failed: {'; '.join(critical)}")

    # Configure observability
    _app_state.observability = ObservabilityManager.configure(
        service_name=config.observability.service_name,
        environment=config.observability.environment,
        json_logs=config.observability.logging_format == "json",
        log_level=getattr(logging, config.log_level.upper(), logging.INFO),
    )
    _app_state.observability.register_state_machine_hooks(_app_state.state_machine)

    # Wire LLM gateway if providers configured
    if config.providers:
        providers = [LLMProvider.model_validate(p) for p in config.providers]
        _app_state.llm_gateway = LLMGateway(providers=providers)

    # Wire Redis (hot store for checkpoints)
    redis_client = None
    try:
        import redis.asyncio as aioredis

        redis_password = _resolve_secret(config.redis.password_ref)
        redis_client = aioredis.Redis(
            host=config.redis.host,
            port=config.redis.port,
            db=config.redis.db,
            password=redis_password or None,
            ssl=config.redis.ssl,
            max_connections=config.redis.max_connections,
            socket_timeout=config.redis.socket_timeout_seconds,
            retry_on_timeout=config.redis.retry_on_timeout,
        )
        await redis_client.ping()
        logger.info("Redis connected: %s:%d", config.redis.host, config.redis.port)
    except Exception as e:
        logger.warning("Redis not available, continuing without: %s", e)
        redis_client = None

    # Wire PostgreSQL (cold store for completed runs)
    pg_pool = None
    try:
        import asyncpg  # type: ignore[import-untyped]

        pg_password = _resolve_secret(config.postgres.password_ref)
        pg_pool = await asyncpg.create_pool(
            host=config.postgres.host,
            port=config.postgres.port,
            database=config.postgres.database,
            user=config.postgres.user,
            password=pg_password or None,
            ssl=config.postgres.ssl_mode if config.postgres.ssl_mode != "prefer" else None,
            min_size=config.postgres.min_connections,
            max_size=config.postgres.max_connections,
            statement_cache_size=config.postgres.statement_cache_size,
        )
        await _ensure_schema(pg_pool)
        logger.info(
            "PostgreSQL connected: %s:%d/%s",
            config.postgres.host,
            config.postgres.port,
            config.postgres.database,
        )
    except Exception as e:
        logger.warning("PostgreSQL not available, continuing without: %s", e)
        pg_pool = None

    # Wire CheckpointManager + ExecutionEngine
    if redis_client is not None:
        _app_state.checkpoint_mgr = CheckpointManager(
            hot_store=redis_client,
            cold_store=pg_pool,
            hot_ttl_seconds=config.execution.hot_checkpoint_ttl_seconds,
        )

        # Register the checkpoint side effect on the state machine
        # so every transition persists state to Redis
        async def _checkpoint_side_effect(ctx: Any, _result: Any) -> None:
            if _app_state.checkpoint_mgr is not None:
                await _app_state.checkpoint_mgr.save(ctx)

        _app_state.state_machine.register_side_effect("checkpoint", _checkpoint_side_effect)

        if _app_state.llm_gateway is not None:
            _app_state.engine = ExecutionEngine(
                state_machine=_app_state.state_machine,
                tool_registry=_app_state.tool_registry,
                checkpoint_mgr=_app_state.checkpoint_mgr,
                llm_gateway=_app_state.llm_gateway,
            )
            logger.info("Execution engine wired and ready")
        else:
            logger.warning("Engine not wired: no LLM providers configured")
    else:
        logger.warning("Engine not wired: no Redis connection")

    _app_state._redis_client = redis_client  # type: ignore[attr-defined]
    _app_state._pg_pool = pg_pool  # type: ignore[attr-defined]

    logger.info(
        "Agent Runtime started: host=%s port=%d env=%s",
        config.host,
        config.port,
        config.environment,
    )

    yield

    # Graceful shutdown: stop accepting new runs, drain active ones
    import asyncio

    _app_state._shutting_down = True
    if _app_state._active_runs:
        logger.info(
            "Draining %d active runs (max 30s)...",
            len(_app_state._active_runs),
        )
        for _ in range(30):  # Wait up to 30 seconds
            if not _app_state._active_runs:
                break
            await asyncio.sleep(1)
        if _app_state._active_runs:
            logger.warning(
                "Shutdown timeout: %d runs still active",
                len(_app_state._active_runs),
            )

    # Cleanup
    if _app_state.llm_gateway:
        await _app_state.llm_gateway.close()
    await _app_state.tool_registry.close()
    if redis_client:
        await redis_client.aclose()
    if pg_pool:
        await pg_pool.close()
    logger.info("Agent Runtime shut down")


# --------------------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------------------

app = FastAPI(
    title="Agent Runtime Engine",
    version="1.0.0",
    description=(
        "Custom AI agent execution platform with deterministic state "
        "management, self-service tool registration, multi-provider "
        "LLM routing, and full OpenTelemetry observability."
    ),
    lifespan=lifespan,
)

# --------------------------------------------------------------------------
# Middleware: Security stack
# --------------------------------------------------------------------------
# FastAPI processes middleware in reverse registration order.
# Registration order:  body_size → cors → rate_limit → auth → request_context
# Execution order:     request_context → auth → rate_limit → cors → body_size


@app.middleware("http")
async def body_size_middleware(request: Request, call_next: Any) -> Response:
    """Reject request bodies that exceed the configured max size."""
    max_bytes = _app_state.config.security.max_request_body_bytes
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > max_bytes:
        return JSONResponse(
            status_code=413,
            content=ErrorResponse(
                error=f"Request body too large (max {max_bytes} bytes)",
            ).model_dump(),
        )
    response: Response = await call_next(request)
    return response


@app.middleware("http")
async def cors_middleware(request: Request, call_next: Any) -> Response:
    """CORS handling driven by config.cors instead of hardcoded values."""
    cors_config = _app_state.config.cors
    origin = request.headers.get("origin", "")

    # Handle preflight
    if request.method == "OPTIONS":
        headers = {
            "Access-Control-Allow-Methods": ", ".join(cors_config.allow_methods),
            "Access-Control-Allow-Headers": ", ".join(cors_config.allow_headers),
        }
        if "*" in cors_config.allow_origins or origin in cors_config.allow_origins:
            headers["Access-Control-Allow-Origin"] = origin or "*"
        return Response(status_code=200, headers=headers)

    response: Response = await call_next(request)

    if "*" in cors_config.allow_origins:
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif origin in cors_config.allow_origins:
        response.headers["Access-Control-Allow-Origin"] = origin

    return response


# Per-key rate limiting state (global + per-key token buckets)
_rate_limit_buckets: dict[str, Any] = {}


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next: Any) -> Response:
    """Global + per-key token bucket rate limiting from config.rate_limit."""
    from agentworks.tool_registry import TokenBucket

    rl = _app_state.config.rate_limit
    if not rl.enabled:
        early_resp: Response = await call_next(request)
        return early_resp

    # Determine bucket key
    if rl.per_key:
        key = request.headers.get(_app_state.config.auth.api_key_header, "anonymous")
    else:
        key = "__global__"

    if key not in _rate_limit_buckets:
        _rate_limit_buckets[key] = TokenBucket(
            rate_per_second=rl.requests_per_minute / 60.0,
            burst_size=rl.burst_size,
        )

    bucket = _rate_limit_buckets[key]
    if not bucket.acquire():
        return JSONResponse(
            status_code=429,
            content=ErrorResponse(
                error="Rate limit exceeded",
            ).model_dump(),
            headers={
                "Retry-After": str(int(bucket.wait_time) + 1),
                "X-RateLimit-Limit": str(rl.requests_per_minute),
                "X-RateLimit-Remaining": "0",
            },
        )

    response: Response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(rl.requests_per_minute)
    response.headers["X-RateLimit-Remaining"] = str(max(0, int(bucket.tokens)))
    return response


@app.middleware("http")
async def auth_middleware(request: Request, call_next: Any) -> Response:
    """API key authentication from config.auth.  Skips health endpoints."""
    auth_config = _app_state.config.auth
    if not auth_config.enabled:
        early_resp: Response = await call_next(request)
        return early_resp

    # Skip authentication for k8s probes and the main health check.
    # /health/tools is NOT exempt — it triggers external HTTP calls.
    path = request.url.path
    if path in ("/api/v1/health", "/api/v1/health/live", "/api/v1/health/ready"):
        health_resp: Response = await call_next(request)
        return health_resp

    api_key = request.headers.get(auth_config.api_key_header)
    if not api_key:
        return JSONResponse(
            status_code=401,
            content=ErrorResponse(error="Missing API key").model_dump(),
        )

    if not any(hmac.compare_digest(api_key, k) for k in auth_config.api_keys):
        return JSONResponse(
            status_code=403,
            content=ErrorResponse(error="Invalid API key").model_dump(),
        )

    response: Response = await call_next(request)
    return response


@app.middleware("http")
async def request_context_middleware(request: Request, call_next: Any) -> Response:
    """
    Inject request ID and track request duration.

    Every response gets:
      - X-Request-ID header (for client-side correlation)
      - X-Response-Time header (for latency monitoring)
    """
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    start = time.monotonic()

    response: Response = await call_next(request)

    duration_ms = (time.monotonic() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time"] = f"{duration_ms:.1f}ms"

    logger.info(
        "%s %s %d %.0fms",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        extra={"duration_ms": duration_ms},
    )

    return response


# --------------------------------------------------------------------------
# Exception handlers
# --------------------------------------------------------------------------


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Return structured error responses for HTTP exceptions."""
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=str(exc.detail),
            request_id=request_id,
        ).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Wrap Pydantic validation errors in the standard ErrorResponse format."""
    request_id = getattr(request.state, "request_id", None)
    details = [
        {
            "field": " → ".join(str(loc) for loc in err.get("loc", [])),
            "message": err.get("msg", ""),
        }
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": "Validation error",
            "detail": details,
            "request_id": request_id,
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unhandled exceptions. Never leak internals."""
    request_id = getattr(request.state, "request_id", None)
    logger.error(
        "Unhandled exception: %s",
        exc,
        exc_info=True,
        extra={"request_id": request_id},
    )
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error="Internal server error",
            request_id=request_id,
        ).model_dump(),
    )


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------


def get_config() -> RuntimeConfig:
    return _app_state.config


def get_tool_registry() -> ToolRegistry:
    return _app_state.tool_registry


def get_observability() -> ObservabilityManager:
    return _app_state.observability


# --------------------------------------------------------------------------
# Endpoints: Agent Runs
# --------------------------------------------------------------------------


@app.post(
    "/api/v1/runs",
    status_code=201,
    response_model=RunResponse,
    tags=["runs"],
    summary="Start a new agent run",
    responses={
        422: {"model": ErrorResponse, "description": "Validation error"},
        503: {"model": ErrorResponse, "description": "Engine not configured or shutting down"},
    },
)
async def start_run(
    body: RunRequest,
    config: RuntimeConfig = Depends(get_config),
    registry: ToolRegistry = Depends(get_tool_registry),
    obs: ObservabilityManager = Depends(get_observability),
) -> RunResponse:
    """
    Start a new agent run.

    Creates an ExecutionContext, starts the execution engine, and
    returns the run result. The run executes synchronously — for
    long-running runs, use the async endpoint (not yet implemented).
    """
    # Reject new runs during graceful shutdown
    if _app_state._shutting_down:
        raise HTTPException(
            status_code=503,
            detail="Server is shutting down, not accepting new runs",
        )

    ctx = ExecutionContext(
        agent_id=body.agent_id,
        team_id=body.team_id,
        project_id=body.project_id,
        max_iterations=body.max_iterations or config.execution.max_iterations,
        max_budget_usd=body.max_budget_usd or config.execution.max_budget_usd,
        metadata=body.metadata,
    )

    # Add user message
    from agentworks.state_machine import Message

    ctx.messages.append(
        Message(
            role="user",
            content=body.message,
        )
    )

    if body.system_prompt:
        ctx.messages.insert(
            0,
            Message(
                role="system",
                content=body.system_prompt,
            ),
        )

    # Start trace
    obs.tracer.start_run_span(
        run_id=ctx.run_id,
        agent_id=ctx.agent_id,
        team_id=ctx.team_id,
        user_request=body.message,
    )
    obs.metrics.record_run_start(ctx.agent_id, ctx.team_id)

    start_time = time.monotonic()

    if _app_state.engine is None:
        # No engine configured (no LLM gateway / no checkpoint store)
        # Return the context as-is for testing
        obs.tracer.end_run_span(
            run_id=ctx.run_id,
            outcome="failed",
            duration_ms=0,
            iterations=0,
            cost_usd=0,
            error="Engine not configured",
        )
        obs.metrics.record_run_end(
            ctx.agent_id,
            ctx.team_id,
            "failed",
            0,
            0,
            0,
        )
        raise HTTPException(
            status_code=503,
            detail="Agent engine not configured. Check LLM providers.",
        )

    try:
        ctx = await _app_state.engine.run(ctx)
        duration_ms = (time.monotonic() - start_time) * 1000

        outcome = ctx.current_state.value
        obs.tracer.end_run_span(
            run_id=ctx.run_id,
            outcome=outcome,
            duration_ms=duration_ms,
            iterations=ctx.iteration_count,
            cost_usd=ctx.token_usage.estimated_cost_usd,
        )
        obs.metrics.record_run_end(
            ctx.agent_id,
            ctx.team_id,
            outcome,
            duration_ms,
            ctx.iteration_count,
            ctx.token_usage.estimated_cost_usd,
        )

        return _ctx_to_response(ctx, duration_ms)

    except Exception as e:
        duration_ms = (time.monotonic() - start_time) * 1000
        obs.tracer.end_run_span(
            run_id=ctx.run_id,
            outcome="failed",
            duration_ms=duration_ms,
            iterations=ctx.iteration_count,
            cost_usd=ctx.token_usage.estimated_cost_usd,
            error=str(e),
        )
        obs.metrics.record_run_end(
            ctx.agent_id,
            ctx.team_id,
            "failed",
            duration_ms,
            ctx.iteration_count,
            ctx.token_usage.estimated_cost_usd,
        )
        raise HTTPException(status_code=500, detail=str(e)) from None


@app.get(
    "/api/v1/runs/{run_id}",
    response_model=RunResponse,
    tags=["runs"],
    summary="Get run status and trace",
    responses={
        404: {"model": ErrorResponse, "description": "Run not found"},
        503: {"model": ErrorResponse, "description": "Checkpoint store not configured"},
    },
)
async def get_run(run_id: str) -> RunResponse:
    """
    Get the status and execution trace of a run.

    Checks the checkpoint store for the run's latest state.
    """
    if _app_state.checkpoint_mgr is None:
        raise HTTPException(
            status_code=503,
            detail="Checkpoint store not configured",
        )

    state = await _app_state.checkpoint_mgr.restore(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    ctx = ExecutionContext.model_validate(state)
    return _ctx_to_response(ctx)


@app.post(
    "/api/v1/runs/{run_id}/resume",
    response_model=RunResponse,
    tags=["runs"],
    summary="Resume a suspended run",
    responses={
        404: {"model": ErrorResponse, "description": "Run not found"},
        503: {"model": ErrorResponse, "description": "Engine not configured"},
    },
)
async def resume_run(run_id: str) -> RunResponse:
    """
    Resume a suspended run from its last checkpoint.

    Only works for runs in SUSPENDED state (e.g., budget exceeded).
    """
    if _app_state.engine is None:
        raise HTTPException(
            status_code=503,
            detail="Agent engine not configured",
        )

    try:
        start_time = time.monotonic()
        ctx = await _app_state.engine.resume(run_id)
        duration_ms = (time.monotonic() - start_time) * 1000
        return _ctx_to_response(ctx, duration_ms)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


def _compute_error_summary(tool_calls: list[dict[str, Any]]) -> ErrorSummary:
    """Derive error summary from tool call records."""
    retryable = 0
    recoverable = 0
    fatal = 0
    for tc in tool_calls:
        if tc.get("error"):
            fatal += 1
        elif isinstance(tc.get("output_data"), dict) and (
            tc["output_data"].get("error_type") or tc["output_data"].get("error")
        ):
            recoverable += 1
        elif (tc.get("retry_count") or 0) > 0:
            retryable += 1
    return ErrorSummary(retryable=retryable, recoverable=recoverable, fatal=fatal)


@app.get(
    "/api/v1/runs",
    response_model=list[RunListItem],
    tags=["runs"],
    summary="List runs",
    responses={
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
)
async def list_runs(
    response: Response,
    agent_id: str | None = Query(default=None, description="Filter by agent ID"),
    team_id: str | None = Query(default=None, description="Filter by team ID"),
    limit: int = Query(default=50, ge=1, le=200, description="Max results"),
    offset: int = Query(default=0, ge=0, description="Offset for pagination"),
) -> list[RunListItem]:
    """
    List agent runs from both hot store (active) and cold store (completed).

    Returns summary items sorted by creation time (newest first).
    """
    items: list[RunListItem] = []

    # Hot store: active runs in Redis
    if _app_state.checkpoint_mgr is not None:
        try:
            active_run_ids = await _app_state.checkpoint_mgr.list_active_runs()
            for rid in active_run_ids:
                state = await _app_state.checkpoint_mgr.restore(rid)
                if state is None:
                    continue
                ctx = ExecutionContext.model_validate(state)
                # Apply filters
                if agent_id and ctx.agent_id != agent_id:
                    continue
                if team_id and ctx.team_id != team_id:
                    continue
                outcome = None
                if ctx.current_state in (AgentState.COMPLETED, AgentState.FAILED):
                    outcome = ctx.current_state.value
                tc_dicts = [tc.model_dump() for tc in ctx.tool_calls]
                items.append(
                    RunListItem(
                        run_id=ctx.run_id,
                        agent_id=ctx.agent_id,
                        team_id=ctx.team_id,
                        state=ctx.current_state.value,
                        outcome=outcome,
                        total_cost_usd=round(ctx.token_usage.estimated_cost_usd, 6),
                        total_tokens=ctx.token_usage.total_tokens,
                        iteration_count=ctx.iteration_count,
                        created_at=ctx.created_at.isoformat(),
                        error_summary=_compute_error_summary(tc_dicts),
                    )
                )
        except Exception as e:
            logger.warning("Failed to list active runs from hot store: %s", e)

    # Cold store: completed runs in PostgreSQL
    if _app_state.checkpoint_mgr and _app_state.checkpoint_mgr._cold is not None:
        try:
            pool = _app_state.checkpoint_mgr._cold
            query = """
                SELECT DISTINCT ON (run_id)
                    run_id, agent_id, team_id,
                    state_snapshot->>'current_state' AS state,
                    (state_snapshot->'token_usage'->>'estimated_cost_usd')::float AS cost,
                    (state_snapshot->'token_usage'->>'total_tokens')::int AS tokens,
                    (state_snapshot->>'iteration_count')::int AS iterations,
                    state_snapshot->>'tool_calls' AS tool_calls_json,
                    created_at
                FROM agent_checkpoints
                WHERE 1=1
            """
            params: list[Any] = []
            param_idx = 1
            if agent_id:
                query += f" AND agent_id = ${param_idx}"
                params.append(agent_id)
                param_idx += 1
            if team_id:
                query += f" AND team_id = ${param_idx}"
                params.append(team_id)
                param_idx += 1
            query += " ORDER BY run_id, checkpoint_version DESC"

            async with pool.acquire() as conn:
                rows = await conn.fetch(query, *params)

            # Deduplicate with hot store
            hot_ids = {item.run_id for item in items}
            for row in rows:
                if row["run_id"] in hot_ids:
                    continue
                state_val = row["state"] or "unknown"
                outcome = state_val if state_val in ("completed", "failed") else None
                # Extract tool_calls from state_snapshot for error summary
                cold_error_summary = ErrorSummary()
                if row.get("tool_calls_json"):
                    try:
                        import json as _json

                        tc_list = _json.loads(row["tool_calls_json"])
                        if isinstance(tc_list, list):
                            cold_error_summary = _compute_error_summary(tc_list)
                    except Exception:
                        pass
                items.append(
                    RunListItem(
                        run_id=row["run_id"],
                        agent_id=row["agent_id"],
                        team_id=row["team_id"],
                        state=state_val,
                        outcome=outcome,
                        total_cost_usd=round(row["cost"] or 0.0, 6),
                        total_tokens=row["tokens"] or 0,
                        iteration_count=row["iterations"] or 0,
                        created_at=row["created_at"].isoformat() if row["created_at"] else None,
                        error_summary=cold_error_summary,
                    )
                )
        except Exception as e:
            logger.warning("Failed to list runs from cold store: %s", e)

    # Sort by created_at descending, apply pagination
    items.sort(key=lambda x: x.created_at or "", reverse=True)
    response.headers["X-Total-Count"] = str(len(items))
    return items[offset : offset + limit]


@app.delete(
    "/api/v1/runs/{run_id}",
    status_code=204,
    tags=["runs"],
    summary="Delete a run",
    responses={
        404: {"model": ErrorResponse, "description": "Run not found"},
        503: {"model": ErrorResponse, "description": "Checkpoint manager not configured"},
    },
)
async def delete_run(run_id: str) -> Response:
    """Delete a run and its checkpoint from both hot and cold stores."""
    if _app_state.checkpoint_mgr is None:
        raise HTTPException(status_code=503, detail="Checkpoint manager not configured")
    deleted = await _app_state.checkpoint_mgr.delete(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return Response(status_code=204)


# --------------------------------------------------------------------------
# Endpoints: Tool Registry
# --------------------------------------------------------------------------


@app.post(
    "/api/v1/tools",
    status_code=201,
    response_model=ToolResponse,
    tags=["tools"],
    summary="Register or update a tool",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid tool definition"},
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
)
async def register_tool(
    definition: ToolDefinition,
    registry: ToolRegistry = Depends(get_tool_registry),
) -> ToolResponse:
    """
    Register a new tool or update an existing one.

    Product teams use this to self-service register their tools.
    Version rules: upgrades allowed, downgrades rejected.
    """
    try:
        registration = await registry.register(definition)
        return ToolResponse(
            tool_id=registration.definition.tool_id,
            version=registration.definition.version,
            status=registration.status.value,
            schema_hash=registration.schema_hash,
            registered_at=registration.registered_at.isoformat(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@app.get(
    "/api/v1/tools",
    response_model=list[ToolListItem],
    tags=["tools"],
    summary="List registered tools",
    responses={
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
)
async def list_tools(
    response: Response,
    status: ToolStatus | None = None,
    owner_team: str | None = None,
    tag: list[str] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200, description="Max results"),
    offset: int = Query(default=0, ge=0, description="Offset for pagination"),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> list[ToolListItem]:
    """List tools with optional filtering by status, team, or tags."""
    registrations = await registry.list_tools(
        status=status,
        owner_team=owner_team,
        tags=tag,
    )
    items = [
        ToolListItem(
            tool_id=r.definition.tool_id,
            name=r.definition.name,
            version=r.definition.version,
            status=r.status.value,
            owner_team=r.definition.owner_team,
            tags=r.definition.tags,
            total_calls=r.total_calls,
            avg_latency_ms=round(r.avg_latency_ms, 1),
        )
        for r in registrations
    ]
    response.headers["X-Total-Count"] = str(len(items))
    return items[offset : offset + limit]


@app.get(
    "/api/v1/tools/{tool_id}",
    response_model=ToolDetailResponse,
    tags=["tools"],
    summary="Get tool details",
    responses={
        404: {"model": ErrorResponse, "description": "Tool not found"},
    },
)
async def get_tool(
    tool_id: str,
    registry: ToolRegistry = Depends(get_tool_registry),
) -> ToolDetailResponse:
    """Get full details including definition, stats, and configuration."""
    registration = await registry.get(tool_id)
    if registration is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    return ToolDetailResponse(
        definition=registration.definition.model_dump(),
        status=registration.status.value,
        stats={
            "total_calls": registration.total_calls,
            "total_errors": registration.total_errors,
            "avg_latency_ms": round(registration.avg_latency_ms, 1),
            "error_rate": (
                round(registration.total_errors / registration.total_calls, 3)
                if registration.total_calls > 0
                else 0.0
            ),
            "last_called_at": (
                registration.last_called_at.isoformat() if registration.last_called_at else None
            ),
        },
        registered_at=registration.registered_at.isoformat(),
        updated_at=registration.updated_at.isoformat(),
    )


@app.delete(
    "/api/v1/tools/{tool_id}",
    status_code=204,
    tags=["tools"],
    summary="Unregister a tool",
    responses={
        404: {"model": ErrorResponse, "description": "Tool not found"},
    },
)
async def unregister_tool(
    tool_id: str,
    registry: ToolRegistry = Depends(get_tool_registry),
) -> Response:
    """Remove a tool from the registry."""
    removed = await registry.unregister(tool_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    return Response(status_code=204)


# --------------------------------------------------------------------------
# Endpoints: Health
# --------------------------------------------------------------------------


@app.get(
    "/api/v1/health",
    response_model=HealthResponse,
    tags=["health"],
    summary="Deep health check with component connectivity",
)
async def health_check() -> HealthResponse:
    """
    Deep health check — pings Redis, checks Postgres pool, reports connectivity.

    Checks:
      - runtime: always OK if this responds
      - engine: OK if execution engine is wired
      - redis: pings Redis to verify connectivity
      - postgres: checks pool status
      - tools: count of registered tools
    """
    checks: dict[str, str] = {
        "runtime": "ok",
    }

    if _app_state.engine:
        checks["engine"] = "ok"
    else:
        checks["engine"] = "not_configured"

    if _app_state.llm_gateway:
        checks["llm_gateway"] = "ok"
    else:
        checks["llm_gateway"] = "not_configured"

    # Deep Redis check
    redis_client = getattr(_app_state, "_redis_client", None)
    if redis_client is not None:
        try:
            await redis_client.ping()
            checks["redis"] = "ok"
        except Exception as e:
            checks["redis"] = f"error: {e}"
    else:
        checks["redis"] = "not_configured"

    # Deep Postgres check
    pg_pool = getattr(_app_state, "_pg_pool", None)
    if pg_pool is not None:
        try:
            async with pg_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            checks["postgres"] = "ok"
        except Exception as e:
            checks["postgres"] = f"error: {e}"
    else:
        checks["postgres"] = "not_configured"

    tools = await _app_state.tool_registry.list_tools()
    checks["tools"] = f"{len(tools)} registered"

    overall = "ok" if checks.get("engine") == "ok" else "degraded"

    return HealthResponse(
        status=overall,
        version="1.0.0",
        uptime_seconds=round(time.monotonic() - _app_state.start_time, 1),
        checks=checks,
    )


@app.get(
    "/api/v1/health/live",
    tags=["health"],
    summary="Kubernetes liveness probe",
)
async def liveness_probe() -> dict[str, str]:
    """Always returns 200 if the process is alive (k8s liveness probe)."""
    return {"status": "alive"}


@app.get(
    "/api/v1/health/ready",
    tags=["health"],
    summary="Kubernetes readiness probe",
)
async def readiness_probe() -> Response:
    """Returns 200 only if the engine is wired and ready (k8s readiness probe)."""
    if _app_state.engine is not None and not _app_state._shutting_down:
        return JSONResponse(
            status_code=200,
            content={"status": "ready"},
        )
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready"},
    )


@app.get(
    "/api/v1/health/tools",
    tags=["health"],
    summary="Tool health checks",
)
async def tool_health_checks(
    registry: ToolRegistry = Depends(get_tool_registry),
) -> list[dict[str, Any]]:
    """Run health checks against all registered tools."""
    results = await registry.health_check_all()
    return [
        {
            "tool_id": r.tool_id,
            "healthy": r.healthy,
            "latency_ms": r.latency_ms,
            "error": r.error,
            "checked_at": r.checked_at.isoformat(),
        }
        for r in results
    ]


# --------------------------------------------------------------------------
# Endpoints: Admin
# --------------------------------------------------------------------------


@app.get(
    "/api/v1/admin/providers",
    tags=["admin"],
    summary="LLM provider status",
)
async def provider_status() -> list[dict[str, Any]]:
    """Get status of all LLM providers including circuit breaker state."""
    if _app_state.llm_gateway is None:
        return []
    return _app_state.llm_gateway.get_provider_status()


@app.get(
    "/api/v1/admin/config",
    tags=["admin"],
    summary="Current runtime configuration",
)
async def get_runtime_config(
    config: RuntimeConfig = Depends(get_config),
) -> dict[str, Any]:
    """Return the current runtime configuration (secrets redacted)."""
    data = config.model_dump()
    # Redact secrets
    if data.get("redis", {}).get("password_ref"):
        data["redis"]["password_ref"] = "***"
    if data.get("postgres", {}).get("password_ref"):
        data["postgres"]["password_ref"] = "***"
    return data


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _ctx_to_response(
    ctx: ExecutionContext,
    duration_ms: float | None = None,
) -> RunResponse:
    """Convert an ExecutionContext to a RunResponse."""
    outcome = None
    if ctx.current_state in (AgentState.COMPLETED, AgentState.FAILED):
        outcome = ctx.current_state.value

    return RunResponse(
        run_id=ctx.run_id,
        agent_id=ctx.agent_id,
        team_id=ctx.team_id,
        state=ctx.current_state.value,
        outcome=outcome,
        iteration_count=ctx.iteration_count,
        total_cost_usd=round(ctx.token_usage.estimated_cost_usd, 6),
        total_tokens=ctx.token_usage.total_tokens,
        duration_ms=duration_ms,
        error=ctx.last_error,
        messages=[m.model_dump(mode="json") for m in ctx.messages],
        tool_calls=[tc.model_dump(mode="json") for tc in ctx.tool_calls],
        state_history=ctx.state_history,
        token_usage=ctx.token_usage.model_dump(),
        created_at=ctx.created_at.isoformat(),
        completed_at=(ctx.completed_at.isoformat() if ctx.completed_at else None),
    )


# --------------------------------------------------------------------------
# Dashboard static serving (optional — for single-container deployments)
# --------------------------------------------------------------------------

_DASHBOARD_DIR = Path(__file__).resolve().parent.parent.parent / "dashboard" / "dist"

if _DASHBOARD_DIR.is_dir():

    @app.get("/dashboard/{rest_of_path:path}")
    async def serve_dashboard(rest_of_path: str) -> Response:
        """Serve dashboard SPA. Falls back to index.html for client-side routing."""
        file_path = _DASHBOARD_DIR / rest_of_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_DASHBOARD_DIR / "index.html")

    @app.get("/dashboard")
    async def dashboard_redirect() -> Response:
        """Redirect /dashboard to /dashboard/."""
        return Response(status_code=301, headers={"Location": "/dashboard/"})

    app.mount(
        "/dashboard/assets",
        StaticFiles(directory=_DASHBOARD_DIR / "assets"),
        name="dashboard-assets",
    )
