"""
Phase 3: Tool Registry

Self-service tool registration with JSON Schema validation, versioning,
health monitoring, and rate limiting.

Tools are external HTTP services owned by product teams. The registry:
  - Validates tool definitions at registration time
  - Validates tool inputs/outputs at execution time
  - Monitors tool health
  - Enforces rate limits (token bucket per tool)
  - Manages tool versions (semver, no downgrades)

The same JSON Schema serves three purposes:
  1. Registration validation (is the schema valid?)
  2. Runtime validation (does input match schema?)
  3. LLM integration (convert to OpenAI function calling format)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
import jsonschema
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# SSRF protection
# --------------------------------------------------------------------------

# Default blocked URL patterns for SSRF protection.
# These match private/internal network addresses that tools should never call.
SSRF_BLOCKED_PATTERNS: list[str] = [
    r"^https?://localhost",
    r"^https?://127\.",
    r"^https?://10\.",
    r"^https?://172\.(1[6-9]|2\d|3[01])\.",
    r"^https?://192\.168\.",
    r"^https?://169\.254\.",
    r"^https?://\[::1\]",
    r"^https?://0\.0\.0\.0",
]


def validate_endpoint_url(url: str, blocked_patterns: list[str] | None = None) -> str | None:
    """Validate a URL is safe to call (not internal/SSRF).

    Returns None if the URL is safe, or an error message if blocked.
    """
    patterns = blocked_patterns if blocked_patterns is not None else SSRF_BLOCKED_PATTERNS

    # Enforce http/https scheme
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Invalid URL scheme '{parsed.scheme}': only http and https are allowed"

    for pattern in patterns:
        if re.match(pattern, url, re.IGNORECASE):
            return f"SSRF blocked: URL '{url}' matches blocked pattern"

    return None


# --------------------------------------------------------------------------
# Configuration models
# --------------------------------------------------------------------------


class RetryPolicy(BaseModel):
    """
    Retry configuration for tool execution.

    Each tool defines its own retry behavior:
      - A database query tool retries connection errors, fails fast on syntax errors
      - An email-sending tool NEVER retries (no duplicate sends)
      - A search tool retries timeouts with exponential backoff
    """

    max_retries: int = Field(default=3, ge=0, le=10)
    backoff_strategy: Literal["fixed", "exponential", "linear"] = "exponential"
    base_delay_seconds: float = Field(default=1.0, ge=0.1, le=30.0)
    max_delay_seconds: float = Field(default=60.0, ge=1.0, le=300.0)
    retryable_errors: list[str] = Field(
        default_factory=lambda: ["timeout", "rate_limit", "server_error"]
    )
    non_retryable_errors: list[str] = Field(
        default_factory=lambda: ["auth_failure", "invalid_input", "not_found"]
    )

    def compute_delay(self, attempt: int) -> float:
        """Compute delay for the given retry attempt (0-indexed)."""
        if self.backoff_strategy == "fixed":
            delay = self.base_delay_seconds
        elif self.backoff_strategy == "exponential":
            delay = self.base_delay_seconds * (2**attempt)
        elif self.backoff_strategy == "linear":
            delay = self.base_delay_seconds * (attempt + 1)
        else:
            delay = self.base_delay_seconds
        return min(delay, self.max_delay_seconds)

    def is_retryable(self, error_type: str) -> bool:
        """Check if an error type should be retried."""
        if error_type in self.non_retryable_errors:
            return False
        return error_type in self.retryable_errors


class RateLimitConfig(BaseModel):
    """
    Rate limiting configuration per tool.
    Uses a token bucket algorithm — handles bursts naturally.
    """

    requests_per_minute: int = Field(default=60, ge=1, le=10000)
    requests_per_hour: int = Field(default=1000, ge=1, le=100000)
    burst_size: int = Field(default=10, ge=1, le=100)
    per_team_limit: bool = True


class HealthCheckConfig(BaseModel):
    """Health check configuration for a tool's backing service."""

    url: str | None = None
    interval_seconds: int = Field(default=30, ge=5, le=300)
    timeout_seconds: int = Field(default=5, ge=1, le=30)
    unhealthy_threshold: int = Field(default=3, ge=1, le=10)
    healthy_threshold: int = Field(default=1, ge=1, le=5)


# --------------------------------------------------------------------------
# Tool definition and registration
# --------------------------------------------------------------------------


class ToolStatus(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"
    UNHEALTHY = "unhealthy"


class ToolDefinition(BaseModel):
    """
    Complete definition of a tool that can be registered in the runtime.

    This is the contract between a product team (tool owner) and the
    runtime (tool executor).
    """

    tool_id: str = Field(
        ...,
        pattern=r"^[a-z][a-z0-9_]{2,63}$",
        description="Unique tool identifier. Lowercase, alphanumeric + underscore.",
    )
    name: str = Field(..., min_length=3, max_length=128)
    description: str = Field(
        ...,
        min_length=10,
        max_length=1024,
        description="Clear description of what the tool does. Sent to the LLM.",
    )
    version: str = Field(
        ...,
        pattern=r"^\d+\.\d+\.\d+$",
        description="Semantic version string (e.g., '1.2.3').",
    )
    endpoint_url: str = Field(..., description="URL to call when executing this tool.")
    http_method: Literal["GET", "POST", "PUT"] = "POST"
    ssrf_check_enabled: bool = True  # can be disabled per-tool for internal services
    input_schema: dict[str, Any] = Field(
        ..., description="JSON Schema for the tool's input parameters."
    )
    output_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object"},
        description="JSON Schema for the tool's output.",
    )
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    rate_limit: RateLimitConfig | None = None
    health_check: HealthCheckConfig | None = None
    owner_team: str = Field(..., min_length=1, max_length=64)
    tags: list[str] = Field(default_factory=list)
    requires_auth: bool = False
    auth_header: str | None = None

    @field_validator("input_schema")
    @classmethod
    def validate_input_schema(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Ensure input_schema is a valid JSON Schema."""
        try:
            jsonschema.Draft7Validator.check_schema(v)
        except jsonschema.SchemaError as e:
            raise ValueError(f"Invalid JSON Schema for input_schema: {e.message}") from None
        return v

    @field_validator("output_schema")
    @classmethod
    def validate_output_schema(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Ensure output_schema is a valid JSON Schema."""
        try:
            jsonschema.Draft7Validator.check_schema(v)
        except jsonschema.SchemaError as e:
            raise ValueError(f"Invalid JSON Schema for output_schema: {e.message}") from None
        return v

    @field_validator("endpoint_url")
    @classmethod
    def validate_endpoint_url_scheme(cls, v: str) -> str:
        """Enforce http or https scheme on endpoint URLs."""
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"Invalid URL scheme '{parsed.scheme}': only http and https are allowed"
            )
        return v

    def to_llm_tool_spec(self) -> dict[str, Any]:
        """Convert to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.tool_id,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    def schema_hash(self) -> str:
        """Content hash of the schemas for change detection."""
        content = f"{self.input_schema}{self.output_schema}{self.version}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]


class ToolRegistration(BaseModel):
    """A registered tool instance in the registry (wraps definition + runtime metadata)."""

    definition: ToolDefinition
    status: ToolStatus = ToolStatus.ACTIVE
    registered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    schema_hash: str = ""
    total_calls: int = 0
    total_errors: int = 0
    avg_latency_ms: float = 0.0
    last_called_at: datetime | None = None
    last_health_check_at: datetime | None = None
    consecutive_failures: int = 0


class ToolResult(BaseModel):
    """Result of a tool execution."""

    tool_id: str
    success: bool
    output: dict[str, Any] | None = None
    error: str | None = None
    error_type: str | None = None
    latency_ms: float
    retry_count: int = 0
    cached: bool = False


class HealthStatus(BaseModel):
    """Health check result for a tool."""

    tool_id: str
    healthy: bool
    latency_ms: float | None = None
    error: str | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# --------------------------------------------------------------------------
# Token bucket rate limiter
# --------------------------------------------------------------------------


class TokenBucket:
    """
    Token bucket rate limiter.

    Each tool gets its own bucket. Tokens replenish at a fixed rate.
    Supports bursts up to burst_size concurrent requests.
    """

    def __init__(self, rate_per_second: float, burst_size: int) -> None:
        self.rate = rate_per_second
        self.burst = burst_size
        self.tokens = float(burst_size)
        self.last_refill = time.monotonic()

    def acquire(self) -> bool:
        """Try to acquire a token. Returns True if allowed."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    @property
    def wait_time(self) -> float:
        """Seconds until the next token is available."""
        if self.tokens >= 1.0:
            return 0.0
        return (1.0 - self.tokens) / self.rate


# --------------------------------------------------------------------------
# Tool Registry
# --------------------------------------------------------------------------


class ToolRegistry:
    """
    Registry with schema validation, versioning, and health monitoring.

    Product teams register tools here. The runtime queries the registry
    to discover tools, validate inputs, execute tools, and monitor health.

    In production, backed by PostgreSQL + Redis. This in-memory implementation
    is used for tests and local development.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolRegistration] = {}
        self._rate_limiters: dict[str, TokenBucket] = {}
        self._http_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-initialized HTTP client with connection pooling."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
                follow_redirects=False,
            )
        return self._http_client

    # -- Registration --

    async def register(self, definition: ToolDefinition) -> ToolRegistration:
        """
        Register a new tool or update an existing one.

        Version rules:
          - Same version + same schemas → no-op
          - Same version + different schemas → warning + update
          - Newer version → update
          - Older version → reject (no downgrades)
        """
        existing = self._tools.get(definition.tool_id)

        if existing is not None:
            existing_version = tuple(int(x) for x in existing.definition.version.split("."))
            new_version = tuple(int(x) for x in definition.version.split("."))

            if new_version < existing_version:
                raise ValueError(
                    f"Cannot downgrade {definition.tool_id} from "
                    f"{existing.definition.version} to {definition.version}"
                )

            if new_version == existing_version:
                new_hash = definition.schema_hash()
                if new_hash == existing.schema_hash:
                    logger.info(
                        "Tool %s v%s: no changes",
                        definition.tool_id,
                        definition.version,
                    )
                    return existing
                logger.warning(
                    "Tool %s v%s: schema changed without version bump (hash %s -> %s)",
                    definition.tool_id,
                    definition.version,
                    existing.schema_hash,
                    new_hash,
                )

        registration = ToolRegistration(
            definition=definition,
            schema_hash=definition.schema_hash(),
        )
        self._tools[definition.tool_id] = registration

        if definition.rate_limit:
            rate = definition.rate_limit.requests_per_minute / 60.0
            self._rate_limiters[definition.tool_id] = TokenBucket(
                rate_per_second=rate,
                burst_size=definition.rate_limit.burst_size,
            )

        logger.info(
            "Registered tool: %s v%s (owner: %s)",
            definition.tool_id,
            definition.version,
            definition.owner_team,
        )
        return registration

    async def unregister(self, tool_id: str) -> bool:
        """Remove a tool from the registry."""
        if tool_id in self._tools:
            del self._tools[tool_id]
            self._rate_limiters.pop(tool_id, None)
            logger.info("Unregistered tool: %s", tool_id)
            return True
        return False

    async def get(self, tool_id: str) -> ToolRegistration | None:
        """Get a tool registration by ID."""
        return self._tools.get(tool_id)

    async def list_tools(
        self,
        status: ToolStatus | None = None,
        owner_team: str | None = None,
        tags: list[str] | None = None,
    ) -> list[ToolRegistration]:
        """List tools with optional filtering."""
        results = list(self._tools.values())
        if status:
            results = [r for r in results if r.status == status]
        if owner_team:
            results = [r for r in results if r.definition.owner_team == owner_team]
        if tags:
            results = [r for r in results if set(tags) & set(r.definition.tags)]
        return results

    def get_llm_tool_specs(self, tool_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """Get OpenAI-format tool specs for the given tools (or all active)."""
        specs = []
        for tool_id, reg in self._tools.items():
            if reg.status != ToolStatus.ACTIVE:
                continue
            if tool_ids and tool_id not in tool_ids:
                continue
            specs.append(reg.definition.to_llm_tool_spec())
        return specs

    # -- Execution --

    async def execute(
        self,
        tool_id: str,
        input_data: dict[str, Any],
        ctx: Any = None,
    ) -> ToolResult:
        """
        Execute a tool with full validation, rate limiting, and retries.

        Steps:
          1. Look up tool registration
          2. Validate input against JSON Schema
          3. Check rate limits
          4. Execute HTTP call with retries per tool's retry_policy
          5. Validate output against JSON Schema (non-blocking)
          6. Update usage statistics
        """
        start_time = time.monotonic()

        # Step 1: Look up tool
        registration = self._tools.get(tool_id)
        if registration is None:
            return ToolResult(
                tool_id=tool_id,
                success=False,
                error=f"Tool '{tool_id}' not found in registry",
                error_type="not_found",
                latency_ms=0.0,
            )

        if registration.status == ToolStatus.DISABLED:
            return ToolResult(
                tool_id=tool_id,
                success=False,
                error=f"Tool '{tool_id}' is disabled",
                error_type="disabled",
                latency_ms=0.0,
            )

        definition = registration.definition

        # Step 2: Validate input
        try:
            jsonschema.validate(instance=input_data, schema=definition.input_schema)
        except jsonschema.ValidationError as e:
            return ToolResult(
                tool_id=tool_id,
                success=False,
                error=f"Input validation failed: {e.message}",
                error_type="invalid_input",
                latency_ms=(time.monotonic() - start_time) * 1000,
            )

        # Step 3: Check rate limits
        limiter = self._rate_limiters.get(tool_id)
        if limiter and not limiter.acquire():
            return ToolResult(
                tool_id=tool_id,
                success=False,
                error=f"Rate limited. Retry after {limiter.wait_time:.1f}s",
                error_type="rate_limit",
                latency_ms=(time.monotonic() - start_time) * 1000,
            )

        # Step 3.5: SSRF check
        if definition.ssrf_check_enabled:
            ssrf_error = validate_endpoint_url(definition.endpoint_url)
            if ssrf_error:
                return ToolResult(
                    tool_id=tool_id,
                    success=False,
                    error=ssrf_error,
                    error_type="ssrf_blocked",
                    latency_ms=(time.monotonic() - start_time) * 1000,
                )

        # Step 4: Execute with retries
        retry_policy = definition.retry_policy
        last_error: str | None = None
        last_error_type: str | None = None
        retry_count = 0

        for attempt in range(retry_policy.max_retries + 1):
            try:
                client = await self._get_client()
                response = await client.request(
                    method=definition.http_method,
                    url=definition.endpoint_url,
                    json=input_data,
                    timeout=definition.timeout_seconds,
                )

                if response.status_code == 429:
                    last_error = "Tool rate limited"
                    last_error_type = "rate_limit"
                    if not retry_policy.is_retryable("rate_limit"):
                        break
                    retry_count = attempt + 1
                    delay = retry_policy.compute_delay(attempt)
                    logger.warning(
                        "Tool %s rate limited, retry %d after %.1fs",
                        tool_id,
                        attempt + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                if response.status_code >= 500:
                    last_error = f"Server error: HTTP {response.status_code}"
                    last_error_type = "server_error"
                    if not retry_policy.is_retryable("server_error"):
                        break
                    retry_count = attempt + 1
                    delay = retry_policy.compute_delay(attempt)
                    logger.warning(
                        "Tool %s server error (%d), retry %d after %.1fs",
                        tool_id,
                        response.status_code,
                        attempt + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                if response.status_code >= 400:
                    return ToolResult(
                        tool_id=tool_id,
                        success=False,
                        error=(f"Client error: HTTP {response.status_code}: {response.text[:500]}"),
                        error_type="client_error",
                        latency_ms=(time.monotonic() - start_time) * 1000,
                        retry_count=retry_count,
                    )

                # Success
                output = response.json()

                # Step 5: Validate output (non-blocking — warn but return)
                try:
                    jsonschema.validate(instance=output, schema=definition.output_schema)
                except jsonschema.ValidationError as e:
                    logger.warning(
                        "Tool %s output validation failed: %s. "
                        "Returning output anyway (non-blocking).",
                        tool_id,
                        e.message,
                    )

                # Step 6: Update stats
                latency_ms = (time.monotonic() - start_time) * 1000
                registration.total_calls += 1
                registration.last_called_at = datetime.now(UTC)
                alpha = 0.1  # exponential moving average
                registration.avg_latency_ms = (
                    alpha * latency_ms + (1 - alpha) * registration.avg_latency_ms
                )
                registration.consecutive_failures = 0

                return ToolResult(
                    tool_id=tool_id,
                    success=True,
                    output=output,
                    latency_ms=latency_ms,
                    retry_count=retry_count,
                )

            except httpx.TimeoutException:
                last_error = f"Timeout after {definition.timeout_seconds}s"
                last_error_type = "timeout"
                if not retry_policy.is_retryable("timeout"):
                    break
                retry_count = attempt + 1
                delay = retry_policy.compute_delay(attempt)
                logger.warning(
                    "Tool %s timed out, retry %d after %.1fs",
                    tool_id,
                    attempt + 1,
                    delay,
                )
                await asyncio.sleep(delay)

            except httpx.ConnectError as e:
                last_error = f"Connection error: {e!s}"
                last_error_type = "server_error"
                if not retry_policy.is_retryable("server_error"):
                    break
                retry_count = attempt + 1
                delay = retry_policy.compute_delay(attempt)
                await asyncio.sleep(delay)

            except Exception as e:
                last_error = f"Unexpected error: {e!s}"
                last_error_type = "unknown"
                logger.error("Tool %s unexpected error: %s", tool_id, e, exc_info=True)
                break  # Don't retry unknown errors

        # All retries exhausted
        registration.total_errors += 1
        registration.consecutive_failures += 1
        return ToolResult(
            tool_id=tool_id,
            success=False,
            error=last_error,
            error_type=last_error_type,
            latency_ms=(time.monotonic() - start_time) * 1000,
            retry_count=retry_count,
        )

    # -- Health checks --

    async def health_check(self, tool_id: str) -> HealthStatus:
        """Run a health check against a tool's health endpoint."""
        registration = self._tools.get(tool_id)
        if registration is None:
            return HealthStatus(tool_id=tool_id, healthy=False, error="Tool not found")

        hc = registration.definition.health_check
        if hc is None or hc.url is None:
            return HealthStatus(
                tool_id=tool_id,
                healthy=True,
                error="No health check configured",
            )

        try:
            client = await self._get_client()
            start = time.monotonic()
            response = await client.get(hc.url, timeout=hc.timeout_seconds)
            latency = (time.monotonic() - start) * 1000

            healthy = 200 <= response.status_code < 300
            registration.last_health_check_at = datetime.now(UTC)

            if healthy:
                registration.consecutive_failures = 0
                if registration.status == ToolStatus.UNHEALTHY:
                    registration.status = ToolStatus.ACTIVE
                    logger.info("Tool %s recovered, marking as active", tool_id)
            else:
                registration.consecutive_failures += 1
                if registration.consecutive_failures >= hc.unhealthy_threshold:
                    registration.status = ToolStatus.UNHEALTHY
                    logger.warning(
                        "Tool %s marked unhealthy after %d failures",
                        tool_id,
                        hc.unhealthy_threshold,
                    )

            return HealthStatus(
                tool_id=tool_id,
                healthy=healthy,
                latency_ms=latency,
                error=None if healthy else f"HTTP {response.status_code}",
            )
        except Exception as e:
            registration.consecutive_failures += 1
            return HealthStatus(tool_id=tool_id, healthy=False, error=str(e))

    async def health_check_all(self) -> list[HealthStatus]:
        """Run health checks for all registered tools."""
        results = []
        for tool_id in self._tools:
            status = await self.health_check(tool_id)
            results.append(status)
        return results

    # -- Lifecycle --

    async def close(self) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
