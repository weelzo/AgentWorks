"""
Tests for Runtime Configuration (Phase 8).

Covers:
  - Default values for all sub-configurations
  - Pydantic validation (ranges, types, enums)
  - YAML loading
  - Environment-based loading (AGENTWORKS_CONFIG_PATH)
  - Nested config overrides
"""

import pytest
import yaml

from agentworks.config import (
    APIRateLimitConfig,
    AuthConfig,
    CORSConfig,
    ExecutionDefaults,
    MemoryDefaults,
    ObservabilityConfig,
    PostgresConfig,
    RedisConfig,
    RuntimeConfig,
    SecurityConfig,
    ToolRegistryDefaults,
)

# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------


class TestDefaults:
    """Verify every sub-config has sensible defaults for local development."""

    def test_bare_runtime_config(self):
        """A bare RuntimeConfig() should work out of the box."""
        cfg = RuntimeConfig()
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 8000
        assert cfg.workers == 4
        assert cfg.log_level == "info"

    def test_redis_defaults(self):
        cfg = RedisConfig()
        assert cfg.host == "localhost"
        assert cfg.port == 6379
        assert cfg.db == 0
        assert cfg.password_ref == ""
        assert cfg.ssl is False
        assert cfg.max_connections == 50

    def test_postgres_defaults(self):
        cfg = PostgresConfig()
        assert cfg.host == "localhost"
        assert cfg.port == 5432
        assert cfg.database == "agentworks"
        assert cfg.user == "agentworks"
        assert cfg.ssl_mode == "prefer"

    def test_observability_defaults(self):
        cfg = ObservabilityConfig()
        assert cfg.tracing_enabled is True
        assert cfg.metrics_enabled is True
        assert cfg.logging_format == "json"
        assert cfg.service_name == "agentworks"
        assert cfg.sample_rate == 1.0

    def test_execution_defaults(self):
        cfg = ExecutionDefaults()
        assert cfg.max_iterations == 25
        assert cfg.max_budget_usd == 1.0
        assert cfg.default_timeout_seconds == 120.0
        assert cfg.checkpoint_on_every_transition is True
        assert cfg.hot_checkpoint_ttl_seconds == 86400

    def test_memory_defaults(self):
        cfg = MemoryDefaults()
        assert cfg.max_context_tokens == 12000
        assert cfg.long_term_recall_top_k == 5
        assert cfg.long_term_recall_min_score == 0.7
        assert cfg.embedding_dimensions == 1536

    def test_tool_registry_defaults(self):
        cfg = ToolRegistryDefaults()
        assert cfg.default_timeout_seconds == 30
        assert cfg.default_max_retries == 3
        assert cfg.default_backoff_strategy == "exponential"
        assert cfg.rate_limit_default_rpm == 60

    def test_feature_flags_default_to_enabled(self):
        cfg = RuntimeConfig()
        assert cfg.enable_response_caching is True
        assert cfg.enable_long_term_memory is True
        assert cfg.enable_cost_tracking is True
        assert cfg.enable_circuit_breaker is True

    def test_providers_default_empty(self):
        cfg = RuntimeConfig()
        assert cfg.providers == []


# --------------------------------------------------------------------------
# Pydantic validation
# --------------------------------------------------------------------------


class TestValidation:
    """Test that Pydantic field constraints enforce valid ranges."""

    def test_sample_rate_range(self):
        with pytest.raises(ValueError):
            ObservabilityConfig(sample_rate=1.5)
        with pytest.raises(ValueError):
            ObservabilityConfig(sample_rate=-0.1)

    def test_max_iterations_range(self):
        with pytest.raises(ValueError):
            ExecutionDefaults(max_iterations=0)
        with pytest.raises(ValueError):
            ExecutionDefaults(max_iterations=101)

    def test_max_budget_range(self):
        with pytest.raises(ValueError):
            ExecutionDefaults(max_budget_usd=0.001)
        with pytest.raises(ValueError):
            ExecutionDefaults(max_budget_usd=200.0)

    def test_timeout_range(self):
        with pytest.raises(ValueError):
            ExecutionDefaults(default_timeout_seconds=5.0)
        with pytest.raises(ValueError):
            ExecutionDefaults(default_timeout_seconds=700.0)

    def test_context_tokens_range(self):
        with pytest.raises(ValueError):
            MemoryDefaults(max_context_tokens=500)
        with pytest.raises(ValueError):
            MemoryDefaults(max_context_tokens=300000)

    def test_logging_format_literal(self):
        ObservabilityConfig(logging_format="json")
        ObservabilityConfig(logging_format="text")
        with pytest.raises(ValueError):
            ObservabilityConfig(logging_format="csv")  # type: ignore[arg-type]

    def test_backoff_strategy_literal(self):
        ToolRegistryDefaults(default_backoff_strategy="fixed")
        ToolRegistryDefaults(default_backoff_strategy="linear")
        with pytest.raises(ValueError):
            ToolRegistryDefaults(default_backoff_strategy="random")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# YAML loading
# --------------------------------------------------------------------------


class TestYAMLLoading:
    """Test loading configuration from YAML files."""

    def test_load_from_yaml(self, tmp_path):
        """Load a YAML file and verify overrides."""
        config_data = {
            "host": "127.0.0.1",
            "port": 9000,
            "workers": 8,
            "log_level": "debug",
            "redis": {"host": "redis.prod", "port": 6380, "ssl": True},
            "postgres": {"host": "pg.prod", "database": "my_agents"},
            "observability": {"environment": "staging", "sample_rate": 0.5},
            "execution": {"max_iterations": 50, "max_budget_usd": 5.0},
            "enable_response_caching": False,
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        cfg = RuntimeConfig.from_yaml(config_file)

        assert cfg.host == "127.0.0.1"
        assert cfg.port == 9000
        assert cfg.workers == 8
        assert cfg.log_level == "debug"
        assert cfg.redis.host == "redis.prod"
        assert cfg.redis.port == 6380
        assert cfg.redis.ssl is True
        assert cfg.postgres.host == "pg.prod"
        assert cfg.postgres.database == "my_agents"
        assert cfg.observability.environment == "staging"
        assert cfg.observability.sample_rate == 0.5
        assert cfg.execution.max_iterations == 50
        assert cfg.execution.max_budget_usd == 5.0
        assert cfg.enable_response_caching is False
        # Unchanged defaults should persist
        assert cfg.redis.db == 0
        assert cfg.enable_long_term_memory is True

    def test_load_empty_yaml(self, tmp_path):
        """An empty YAML file should produce default config."""
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")

        cfg = RuntimeConfig.from_yaml(config_file)
        assert cfg.port == 8000
        assert cfg.redis.host == "localhost"

    def test_load_partial_yaml(self, tmp_path):
        """A YAML with only some fields should merge with defaults."""
        config_file = tmp_path / "partial.yaml"
        config_file.write_text(yaml.dump({"port": 3000}))

        cfg = RuntimeConfig.from_yaml(config_file)
        assert cfg.port == 3000
        assert cfg.host == "0.0.0.0"  # unchanged default

    def test_yaml_with_providers(self, tmp_path):
        """Provider list should be loaded correctly."""
        config_data = {
            "providers": [
                {
                    "name": "openai",
                    "provider_type": "openai",
                    "api_key_ref": "OPENAI_API_KEY",
                    "model": "gpt-4",
                }
            ]
        }
        config_file = tmp_path / "providers.yaml"
        config_file.write_text(yaml.dump(config_data))

        cfg = RuntimeConfig.from_yaml(config_file)
        assert len(cfg.providers) == 1
        assert cfg.providers[0]["name"] == "openai"


# --------------------------------------------------------------------------
# Environment loading
# --------------------------------------------------------------------------


class TestEnvLoading:
    """Test the from_env() class method."""

    def test_from_env_no_path(self, monkeypatch):
        """Without AGENTWORKS_CONFIG_PATH, returns defaults."""
        monkeypatch.delenv("AGENTWORKS_CONFIG_PATH", raising=False)
        cfg = RuntimeConfig.from_env()
        assert cfg.port == 8000

    def test_from_env_with_path(self, tmp_path, monkeypatch):
        """With AGENTWORKS_CONFIG_PATH set, loads from that YAML file."""
        config_file = tmp_path / "runtime.yaml"
        config_file.write_text(yaml.dump({"port": 4000}))
        monkeypatch.setenv("AGENTWORKS_CONFIG_PATH", str(config_file))

        cfg = RuntimeConfig.from_env()
        assert cfg.port == 4000


# --------------------------------------------------------------------------
# Nested overrides
# --------------------------------------------------------------------------


class TestNestedOverrides:
    """Verify that nested sub-configs can be overridden independently."""

    def test_override_redis_only(self):
        cfg = RuntimeConfig(redis=RedisConfig(host="cache.internal", ssl=True))
        assert cfg.redis.host == "cache.internal"
        assert cfg.redis.ssl is True
        assert cfg.postgres.host == "localhost"  # unaffected

    def test_override_execution_only(self):
        cfg = RuntimeConfig(execution=ExecutionDefaults(max_iterations=10, max_budget_usd=0.5))
        assert cfg.execution.max_iterations == 10
        assert cfg.execution.max_budget_usd == 0.5
        assert cfg.memory.max_context_tokens == 12000  # unaffected


# --------------------------------------------------------------------------
# New sub-config defaults (Phase 9: Production Readiness)
# --------------------------------------------------------------------------


class TestAuthConfigDefaults:
    """Verify AuthConfig defaults — disabled for local dev."""

    def test_disabled_by_default(self):
        cfg = AuthConfig()
        assert cfg.enabled is False
        assert cfg.api_key_header == "X-API-Key"
        assert cfg.api_keys == []

    def test_jwt_disabled_by_default(self):
        cfg = AuthConfig()
        assert cfg.jwt_enabled is False
        assert cfg.jwt_secret_ref == ""
        assert cfg.jwt_algorithm == "HS256"


class TestAPIRateLimitConfigDefaults:
    """Verify APIRateLimitConfig defaults — disabled for local dev."""

    def test_disabled_by_default(self):
        cfg = APIRateLimitConfig()
        assert cfg.enabled is False
        assert cfg.requests_per_minute == 120
        assert cfg.per_key is True
        assert cfg.burst_size == 20

    def test_rpm_validation_range(self):
        with pytest.raises(ValueError):
            APIRateLimitConfig(requests_per_minute=0)
        with pytest.raises(ValueError):
            APIRateLimitConfig(requests_per_minute=100001)

    def test_burst_validation_range(self):
        with pytest.raises(ValueError):
            APIRateLimitConfig(burst_size=0)
        with pytest.raises(ValueError):
            APIRateLimitConfig(burst_size=1001)


class TestSecurityConfigDefaults:
    """Verify SecurityConfig defaults — SSRF on, 1MB body."""

    def test_ssrf_enabled_by_default(self):
        cfg = SecurityConfig()
        assert cfg.enforce_ssrf_protection is True
        assert len(cfg.blocked_url_patterns) > 0

    def test_default_body_limit_1mb(self):
        cfg = SecurityConfig()
        assert cfg.max_request_body_bytes == 1_048_576

    def test_body_limit_validation_range(self):
        with pytest.raises(ValueError):
            SecurityConfig(max_request_body_bytes=512)
        SecurityConfig(max_request_body_bytes=1024)  # minimum


class TestCORSConfigDefaults:
    """Verify CORSConfig defaults — allow all for local dev."""

    def test_allow_all_by_default(self):
        cfg = CORSConfig()
        assert cfg.allow_origins == ["*"]
        assert cfg.allow_methods == ["*"]
        assert cfg.allow_headers == ["*"]

    def test_restricted_origins(self):
        cfg = CORSConfig(allow_origins=["https://app.example.com"])
        assert cfg.allow_origins == ["https://app.example.com"]


class TestRuntimeConfigNewFields:
    """Verify new fields on RuntimeConfig."""

    def test_bare_config_still_works(self):
        """Backward compatibility: RuntimeConfig() still works for local dev."""
        cfg = RuntimeConfig()
        assert cfg.auth.enabled is False
        assert cfg.rate_limit.enabled is False
        assert cfg.security.enforce_ssrf_protection is True
        assert cfg.cors.allow_origins == ["*"]
        assert cfg.environment == "development"

    def test_environment_field(self):
        cfg = RuntimeConfig(environment="production")
        assert cfg.environment == "production"


# --------------------------------------------------------------------------
# Production validation
# --------------------------------------------------------------------------


class TestValidateForProduction:
    """Test the validate_for_production() method."""

    def test_default_config_has_critical_warnings(self):
        """Default config is NOT production-safe (auth disabled, no providers)."""
        cfg = RuntimeConfig()
        warnings = cfg.validate_for_production()
        assert any("Authentication is disabled" in w for w in warnings)
        assert any("No LLM providers" in w for w in warnings)

    def test_cors_wildcard_warning(self):
        cfg = RuntimeConfig()
        warnings = cfg.validate_for_production()
        assert any("CORS allows all origins" in w for w in warnings)

    def test_rate_limit_disabled_warning(self):
        cfg = RuntimeConfig()
        warnings = cfg.validate_for_production()
        assert any("rate limiting is disabled" in w for w in warnings)

    def test_ssrf_disabled_warning(self):
        cfg = RuntimeConfig(security=SecurityConfig(enforce_ssrf_protection=False))
        warnings = cfg.validate_for_production()
        assert any("SSRF protection is disabled" in w for w in warnings)

    def test_no_warnings_for_fully_configured(self):
        """A fully configured production setup returns no warnings."""
        cfg = RuntimeConfig(
            auth=AuthConfig(enabled=True, api_keys=["key-1"]),
            rate_limit=APIRateLimitConfig(enabled=True),
            cors=CORSConfig(allow_origins=["https://app.example.com"]),
            security=SecurityConfig(enforce_ssrf_protection=True),
            redis=RedisConfig(password_ref="env:REDIS_PASSWORD"),
            postgres=PostgresConfig(password_ref="env:PG_PASSWORD"),
            providers=[{"name": "openai", "provider_type": "openai"}],
        )
        warnings = cfg.validate_for_production()
        assert warnings == []

    def test_redis_no_password_warning(self):
        cfg = RuntimeConfig()
        warnings = cfg.validate_for_production()
        assert any("Redis has no password" in w for w in warnings)

    def test_postgres_no_password_warning(self):
        cfg = RuntimeConfig()
        warnings = cfg.validate_for_production()
        assert any("PostgreSQL has no password" in w for w in warnings)


# --------------------------------------------------------------------------
# YAML loading with new fields
# --------------------------------------------------------------------------


class TestYAMLWithNewFields:
    """Test that new fields load correctly from YAML."""

    def test_yaml_with_auth_config(self, tmp_path):
        config_data = {
            "auth": {
                "enabled": True,
                "api_keys": ["key-abc", "key-def"],
                "api_key_header": "Authorization",
            },
            "environment": "production",
        }
        config_file = tmp_path / "auth.yaml"
        config_file.write_text(yaml.dump(config_data))

        cfg = RuntimeConfig.from_yaml(config_file)
        assert cfg.auth.enabled is True
        assert cfg.auth.api_keys == ["key-abc", "key-def"]
        assert cfg.auth.api_key_header == "Authorization"
        assert cfg.environment == "production"

    def test_yaml_with_rate_limit(self, tmp_path):
        config_data = {
            "rate_limit": {
                "enabled": True,
                "requests_per_minute": 60,
                "burst_size": 10,
            }
        }
        config_file = tmp_path / "ratelimit.yaml"
        config_file.write_text(yaml.dump(config_data))

        cfg = RuntimeConfig.from_yaml(config_file)
        assert cfg.rate_limit.enabled is True
        assert cfg.rate_limit.requests_per_minute == 60

    def test_yaml_with_security_config(self, tmp_path):
        config_data = {
            "security": {
                "max_request_body_bytes": 5_242_880,
                "enforce_ssrf_protection": True,
            }
        }
        config_file = tmp_path / "security.yaml"
        config_file.write_text(yaml.dump(config_data))

        cfg = RuntimeConfig.from_yaml(config_file)
        assert cfg.security.max_request_body_bytes == 5_242_880
