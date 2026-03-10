"""
Phase 8: Runtime Configuration Schema

Complete configuration for the Agent Runtime Engine.
In production, loaded from a YAML file + environment variable overrides.

Configuration hierarchy:
  RuntimeConfig
  ├── RedisConfig (hot checkpoint store, response cache)
  ├── PostgresConfig (cold store, cost records, vector memory)
  ├── ObservabilityConfig (OTel tracing, metrics, logging)
  ├── ExecutionDefaults (iteration limits, budgets, timeouts)
  ├── MemoryDefaults (context windows, embedding, recall)
  ├── ToolRegistryDefaults (rate limits, retries, health checks)
  ├── LLM providers (loaded from providers list)
  ├── Feature flags (caching, memory, cost tracking, circuit breaker)
  └── Server settings (host, port, workers)

Every field has a sensible default. A bare RuntimeConfig() works for
local development — only production needs YAML overrides.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pathlib import Path

# --------------------------------------------------------------------------
# Security & middleware sub-configs (Phase 9: Production Readiness)
# --------------------------------------------------------------------------


class AuthConfig(BaseModel):
    """API authentication configuration.

    When enabled, all requests (except health probes) must carry a valid
    API key in the configured header.  Disabled by default for local dev.
    """

    enabled: bool = False
    api_key_header: str = "X-API-Key"
    api_keys: list[str] = Field(default_factory=list)
    jwt_enabled: bool = False
    jwt_secret_ref: str = ""
    jwt_algorithm: str = "HS256"


class APIRateLimitConfig(BaseModel):
    """Global API rate limiting (token-bucket per client key).

    Separate from the per-tool RateLimitConfig in tool_registry.py —
    this controls overall API access, not individual tool calls.
    """

    enabled: bool = False
    requests_per_minute: int = Field(default=120, ge=1, le=100000)
    per_key: bool = True
    burst_size: int = Field(default=20, ge=1, le=1000)


class SecurityConfig(BaseModel):
    """Request-level security hardening.

    SSRF protection blocks tools from calling internal network addresses.
    Body-size limits prevent memory exhaustion from oversized payloads.
    """

    blocked_url_patterns: list[str] = Field(
        default_factory=lambda: [
            r"^https?://localhost",
            r"^https?://127\.",
            r"^https?://10\.",
            r"^https?://172\.(1[6-9]|2\d|3[01])\.",
            r"^https?://192\.168\.",
            r"^https?://169\.254\.",
            r"^https?://\[::1\]",
            r"^https?://0\.0\.0\.0",
        ]
    )
    max_request_body_bytes: int = Field(default=1_048_576, ge=1024, le=104_857_600)
    enforce_ssrf_protection: bool = True


class CORSConfig(BaseModel):
    """CORS middleware configuration.

    Defaults to allow-all for local development.
    Production should restrict to known front-end origins.
    """

    allow_origins: list[str] = Field(default_factory=lambda: ["*"])
    allow_methods: list[str] = Field(default_factory=lambda: ["*"])
    allow_headers: list[str] = Field(default_factory=lambda: ["*"])


# --------------------------------------------------------------------------
# Infrastructure sub-configs
# --------------------------------------------------------------------------


class RedisConfig(BaseModel):
    """Redis connection configuration for hot checkpoint store and caching."""

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password_ref: str = ""
    ssl: bool = False
    max_connections: int = 50
    socket_timeout_seconds: float = 5.0
    retry_on_timeout: bool = True


class PostgresConfig(BaseModel):
    """PostgreSQL connection configuration for cold store, cost records, vector memory."""

    host: str = "localhost"
    port: int = 5432
    database: str = "agentworks"
    user: str = "agentworks"
    password_ref: str = ""
    ssl_mode: str = "prefer"
    min_connections: int = 5
    max_connections: int = 25
    statement_cache_size: int = 100


class ObservabilityConfig(BaseModel):
    """OpenTelemetry export configuration."""

    tracing_enabled: bool = True
    metrics_enabled: bool = True
    logging_format: Literal["json", "text"] = "json"
    otlp_endpoint: str = "http://otel-collector:4317"
    service_name: str = "agentworks"
    environment: str = "production"
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)


class ExecutionDefaults(BaseModel):
    """Default execution parameters for agent runs."""

    max_iterations: int = Field(default=25, ge=1, le=100)
    max_budget_usd: float = Field(default=1.0, ge=0.01, le=100.0)
    default_timeout_seconds: float = Field(default=120.0, ge=10.0, le=600.0)
    checkpoint_on_every_transition: bool = True
    hot_checkpoint_ttl_seconds: int = Field(default=86400, ge=3600, le=604800)


class MemoryDefaults(BaseModel):
    """Default memory manager settings."""

    max_context_tokens: int = Field(default=12000, ge=1024, le=200000)
    long_term_recall_top_k: int = Field(default=5, ge=1, le=20)
    long_term_recall_min_score: float = Field(default=0.7, ge=0.0, le=1.0)
    long_term_budget_ratio: float = Field(default=0.2, ge=0.0, le=0.5)
    token_counting_model: str = "gpt-4"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536


class ToolRegistryDefaults(BaseModel):
    """Default settings for the tool registry."""

    default_timeout_seconds: int = 30
    default_max_retries: int = 3
    default_backoff_strategy: Literal["fixed", "exponential", "linear"] = "exponential"
    health_check_interval_seconds: int = 30
    max_registered_tools: int = 500
    rate_limit_default_rpm: int = 60


class RuntimeConfig(BaseModel):
    """
    Top-level configuration for the Agent Runtime Engine.

    Loaded from YAML at startup, with environment variable overrides
    for secrets and environment-specific settings.
    """

    # Infrastructure
    redis: RedisConfig = Field(default_factory=RedisConfig)
    postgres: PostgresConfig = Field(default_factory=PostgresConfig)

    # Security & middleware
    auth: AuthConfig = Field(default_factory=AuthConfig)
    rate_limit: APIRateLimitConfig = Field(default_factory=APIRateLimitConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    cors: CORSConfig = Field(default_factory=CORSConfig)

    # Observability
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    # Execution
    execution: ExecutionDefaults = Field(default_factory=ExecutionDefaults)

    # Memory
    memory: MemoryDefaults = Field(default_factory=MemoryDefaults)

    # Tool registry
    tools: ToolRegistryDefaults = Field(default_factory=ToolRegistryDefaults)

    # LLM providers
    providers: list[dict[str, Any]] = Field(default_factory=list)

    # Feature flags
    enable_response_caching: bool = True
    enable_long_term_memory: bool = True
    enable_cost_tracking: bool = True
    enable_circuit_breaker: bool = True

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4
    log_level: str = "info"

    # Environment tag
    environment: str = "development"

    def validate_for_production(self) -> list[str]:
        """Return warnings for production-unsafe configuration.

        Returns a list of human-readable warning strings.  An empty list
        means the configuration is acceptable for production.

        Called during startup when ``environment == "production"``; the
        lifespan handler decides whether to block or just log.
        """
        warnings: list[str] = []

        if not self.auth.enabled:
            warnings.append("CRITICAL: Authentication is disabled")

        if "*" in self.cors.allow_origins:
            warnings.append("WARNING: CORS allows all origins")

        if not self.rate_limit.enabled:
            warnings.append("WARNING: API rate limiting is disabled")

        if not self.security.enforce_ssrf_protection:
            warnings.append("WARNING: SSRF protection is disabled")

        if not self.providers:
            warnings.append("CRITICAL: No LLM providers configured")

        if self.redis.password_ref == "":
            warnings.append("WARNING: Redis has no password configured")

        if self.postgres.password_ref == "":
            warnings.append("WARNING: PostgreSQL has no password configured")

        return warnings

    @classmethod
    def from_yaml(cls, path: str | Path) -> RuntimeConfig:
        """Load configuration from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data)

    @classmethod
    def from_env(cls) -> RuntimeConfig:
        """
        Load configuration from environment variables.

        Looks for AGENTWORKS_CONFIG_PATH first. If set, loads from YAML.
        Otherwise, returns defaults (suitable for local development).
        """
        config_path = os.environ.get("AGENTWORKS_CONFIG_PATH")
        if config_path:
            return cls.from_yaml(config_path)
        return cls()
