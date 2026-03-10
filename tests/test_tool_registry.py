"""
Tests for the Tool Registry (Phase 3).

Covers:
  - Tool registration and validation
  - Version management (upgrades, no downgrades)
  - JSON Schema validation on inputs
  - Rate limiting (token bucket)
  - LLM tool spec generation
  - Tool status management
"""

import pytest

from agentworks.tool_registry import (
    RateLimitConfig,
    RetryPolicy,
    TokenBucket,
    ToolDefinition,
    ToolRegistry,
    ToolStatus,
    validate_endpoint_url,
)

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def make_tool_definition(
    tool_id: str = "test_search",
    version: str = "1.0.0",
    **overrides: object,
) -> ToolDefinition:
    """Create a tool definition with sensible defaults for testing."""
    defaults = {
        "tool_id": tool_id,
        "name": "Test Search Tool",
        "description": "Search the knowledge base for relevant information.",
        "version": version,
        "endpoint_url": "http://search-service:8080/search",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "results": {"type": "array"},
            },
        },
        "owner_team": "test-team",
        "tags": ["search", "knowledge"],
    }
    defaults.update(overrides)
    return ToolDefinition(**defaults)


# --------------------------------------------------------------------------
# Registration tests
# --------------------------------------------------------------------------


class TestToolRegistration:
    async def test_register_new_tool(self):
        registry = ToolRegistry()
        definition = make_tool_definition()

        reg = await registry.register(definition)

        assert reg.definition.tool_id == "test_search"
        assert reg.status == ToolStatus.ACTIVE
        assert reg.schema_hash != ""
        assert reg.total_calls == 0

    async def test_register_returns_existing_if_unchanged(self):
        """Re-registering the same version with same schema is a no-op."""
        registry = ToolRegistry()
        definition = make_tool_definition()

        reg1 = await registry.register(definition)
        reg2 = await registry.register(definition)

        assert reg1 is reg2  # same object — no-op

    async def test_version_upgrade_succeeds(self):
        registry = ToolRegistry()

        await registry.register(make_tool_definition(version="1.0.0"))
        reg = await registry.register(make_tool_definition(version="1.1.0"))

        assert reg.definition.version == "1.1.0"

    async def test_version_downgrade_rejected(self):
        registry = ToolRegistry()

        await registry.register(make_tool_definition(version="2.0.0"))

        with pytest.raises(ValueError, match="Cannot downgrade"):
            await registry.register(make_tool_definition(version="1.0.0"))

    async def test_unregister_tool(self):
        registry = ToolRegistry()
        await registry.register(make_tool_definition())

        removed = await registry.unregister("test_search")
        assert removed is True

        assert await registry.get("test_search") is None

    async def test_unregister_nonexistent_returns_false(self):
        registry = ToolRegistry()
        removed = await registry.unregister("nonexistent")
        assert removed is False


# --------------------------------------------------------------------------
# Tool definition validation tests
# --------------------------------------------------------------------------


class TestToolDefinitionValidation:
    def test_valid_tool_id_pattern(self):
        """tool_id must be lowercase, start with a letter, 3-64 chars."""
        defn = make_tool_definition(tool_id="customer_lookup")
        assert defn.tool_id == "customer_lookup"

    def test_invalid_tool_id_rejected(self):
        """Uppercase, too short, starting with number — all rejected."""
        with pytest.raises(ValueError):
            make_tool_definition(tool_id="AB")

    def test_invalid_json_schema_rejected(self):
        """A structurally invalid JSON Schema should be rejected."""
        with pytest.raises(ValueError, match="Invalid JSON Schema"):
            make_tool_definition(input_schema={"type": "not-a-real-type"})

    def test_valid_version_format(self):
        defn = make_tool_definition(version="12.3.456")
        assert defn.version == "12.3.456"

    def test_invalid_version_format_rejected(self):
        with pytest.raises(ValueError):
            make_tool_definition(version="1.0")

    def test_description_too_short_rejected(self):
        with pytest.raises(ValueError):
            make_tool_definition(description="Short")


# --------------------------------------------------------------------------
# LLM tool spec generation
# --------------------------------------------------------------------------


class TestLLMToolSpecs:
    async def test_generates_openai_format(self):
        registry = ToolRegistry()
        await registry.register(make_tool_definition())

        specs = registry.get_llm_tool_specs()

        assert len(specs) == 1
        spec = specs[0]
        assert spec["type"] == "function"
        assert spec["function"]["name"] == "test_search"
        assert "parameters" in spec["function"]
        assert spec["function"]["parameters"]["type"] == "object"

    async def test_excludes_disabled_tools(self):
        registry = ToolRegistry()
        reg = await registry.register(make_tool_definition())
        reg.status = ToolStatus.DISABLED

        specs = registry.get_llm_tool_specs()
        assert len(specs) == 0

    async def test_filter_by_tool_ids(self):
        registry = ToolRegistry()
        await registry.register(make_tool_definition(tool_id="tool_aaa"))
        await registry.register(make_tool_definition(tool_id="tool_bbb"))

        specs = registry.get_llm_tool_specs(tool_ids=["tool_aaa"])
        assert len(specs) == 1
        assert specs[0]["function"]["name"] == "tool_aaa"


# --------------------------------------------------------------------------
# Listing and filtering
# --------------------------------------------------------------------------


class TestListTools:
    async def test_list_all(self):
        registry = ToolRegistry()
        await registry.register(make_tool_definition(tool_id="tool_aaa"))
        await registry.register(make_tool_definition(tool_id="tool_bbb"))

        tools = await registry.list_tools()
        assert len(tools) == 2

    async def test_filter_by_status(self):
        registry = ToolRegistry()
        reg = await registry.register(make_tool_definition(tool_id="tool_aaa"))
        reg.status = ToolStatus.DISABLED
        await registry.register(make_tool_definition(tool_id="tool_bbb"))

        active = await registry.list_tools(status=ToolStatus.ACTIVE)
        assert len(active) == 1
        assert active[0].definition.tool_id == "tool_bbb"

    async def test_filter_by_owner(self):
        registry = ToolRegistry()
        await registry.register(make_tool_definition(tool_id="tool_aaa", owner_team="alpha"))
        await registry.register(make_tool_definition(tool_id="tool_bbb", owner_team="beta"))

        alpha_tools = await registry.list_tools(owner_team="alpha")
        assert len(alpha_tools) == 1

    async def test_filter_by_tags(self):
        registry = ToolRegistry()
        await registry.register(make_tool_definition(tool_id="tool_aaa", tags=["search"]))
        await registry.register(make_tool_definition(tool_id="tool_bbb", tags=["email"]))

        search_tools = await registry.list_tools(tags=["search"])
        assert len(search_tools) == 1
        assert search_tools[0].definition.tool_id == "tool_aaa"


# --------------------------------------------------------------------------
# Token bucket rate limiter tests
# --------------------------------------------------------------------------


class TestTokenBucket:
    def test_allows_burst(self):
        """Bucket allows burst_size requests immediately."""
        bucket = TokenBucket(rate_per_second=1.0, burst_size=5)

        for _ in range(5):
            assert bucket.acquire() is True

        # 6th request should be denied
        assert bucket.acquire() is False

    def test_refills_over_time(self):
        """After draining, tokens refill based on rate."""
        bucket = TokenBucket(rate_per_second=100.0, burst_size=1)
        bucket.acquire()  # drain

        # Simulate time passing
        bucket.last_refill -= 0.1  # 100ms ago → 10 tokens refilled
        assert bucket.acquire() is True

    def test_wait_time(self):
        """wait_time reports how long until next token available."""
        bucket = TokenBucket(rate_per_second=10.0, burst_size=1)
        bucket.acquire()  # drain

        assert bucket.wait_time > 0
        assert bucket.wait_time <= 0.2  # should be ~0.1s at 10/s


# --------------------------------------------------------------------------
# Retry policy tests
# --------------------------------------------------------------------------


class TestRetryPolicy:
    def test_exponential_backoff(self):
        policy = RetryPolicy(
            backoff_strategy="exponential",
            base_delay_seconds=1.0,
            max_delay_seconds=60.0,
        )
        assert policy.compute_delay(0) == 1.0  # 1 * 2^0
        assert policy.compute_delay(1) == 2.0  # 1 * 2^1
        assert policy.compute_delay(2) == 4.0  # 1 * 2^2
        assert policy.compute_delay(3) == 8.0  # 1 * 2^3

    def test_exponential_capped_at_max(self):
        policy = RetryPolicy(
            backoff_strategy="exponential",
            base_delay_seconds=1.0,
            max_delay_seconds=10.0,
        )
        assert policy.compute_delay(10) == 10.0  # capped

    def test_fixed_backoff(self):
        policy = RetryPolicy(backoff_strategy="fixed", base_delay_seconds=5.0)
        assert policy.compute_delay(0) == 5.0
        assert policy.compute_delay(5) == 5.0

    def test_linear_backoff(self):
        policy = RetryPolicy(backoff_strategy="linear", base_delay_seconds=2.0)
        assert policy.compute_delay(0) == 2.0  # 2 * (0+1)
        assert policy.compute_delay(1) == 4.0  # 2 * (1+1)
        assert policy.compute_delay(2) == 6.0  # 2 * (2+1)

    def test_retryable_errors(self):
        policy = RetryPolicy()  # defaults
        assert policy.is_retryable("timeout") is True
        assert policy.is_retryable("rate_limit") is True
        assert policy.is_retryable("server_error") is True

    def test_non_retryable_errors(self):
        policy = RetryPolicy()
        assert policy.is_retryable("auth_failure") is False
        assert policy.is_retryable("invalid_input") is False
        assert policy.is_retryable("not_found") is False

    def test_unknown_error_not_retryable(self):
        policy = RetryPolicy()
        assert policy.is_retryable("something_weird") is False


# --------------------------------------------------------------------------
# Input validation tests (execute path)
# --------------------------------------------------------------------------


class TestInputValidation:
    async def test_execute_unknown_tool_returns_not_found(self):
        registry = ToolRegistry()

        result = await registry.execute("nonexistent", {"query": "test"})

        assert result.success is False
        assert result.error_type == "not_found"

    async def test_execute_disabled_tool_returns_error(self):
        registry = ToolRegistry()
        reg = await registry.register(make_tool_definition())
        reg.status = ToolStatus.DISABLED

        result = await registry.execute("test_search", {"query": "test"})

        assert result.success is False
        assert result.error_type == "disabled"

    async def test_execute_invalid_input_returns_validation_error(self):
        registry = ToolRegistry()
        await registry.register(make_tool_definition())

        # Missing required "query" field
        result = await registry.execute("test_search", {})

        assert result.success is False
        assert result.error_type == "invalid_input"
        assert "validation failed" in result.error.lower()

    async def test_execute_rate_limited(self):
        """
        Test that the rate limiter blocks requests when the bucket is drained.

        We drain the token bucket directly (rather than via HTTP calls that
        would timeout against a fake endpoint and refill the bucket during
        the 30s connection timeout).
        """
        registry = ToolRegistry()
        defn = make_tool_definition(
            rate_limit=RateLimitConfig(requests_per_minute=60, burst_size=1)
        )
        await registry.register(defn)

        # Drain the bucket directly — simulates a prior successful call
        limiter = registry._rate_limiters["test_search"]
        limiter.acquire()

        # Next execute should be blocked by rate limiter before any HTTP call
        result = await registry.execute("test_search", {"query": "b"})
        assert result.success is False
        assert result.error_type == "rate_limit"


# --------------------------------------------------------------------------
# Schema hash and change detection
# --------------------------------------------------------------------------


class TestSchemaHash:
    def test_same_schema_same_hash(self):
        d1 = make_tool_definition(version="1.0.0")
        d2 = make_tool_definition(version="1.0.0")
        assert d1.schema_hash() == d2.schema_hash()

    def test_different_version_different_hash(self):
        d1 = make_tool_definition(version="1.0.0")
        d2 = make_tool_definition(version="2.0.0")
        assert d1.schema_hash() != d2.schema_hash()

    def test_different_schema_different_hash(self):
        d1 = make_tool_definition()
        d2 = make_tool_definition(
            input_schema={
                "type": "object",
                "properties": {"different": {"type": "integer"}},
                "required": ["different"],
            }
        )
        assert d1.schema_hash() != d2.schema_hash()


# --------------------------------------------------------------------------
# SSRF Protection tests
# --------------------------------------------------------------------------


class TestSSRFProtection:
    """Tests for SSRF URL validation."""

    def test_blocks_localhost(self):
        error = validate_endpoint_url("http://localhost:8080/api")
        assert error is not None
        assert "SSRF blocked" in error

    def test_blocks_internal_ips(self):
        """Private network ranges (10.x, 172.16-31.x, 192.168.x) are blocked."""
        blocked = [
            "http://10.0.0.1/api",
            "http://172.16.0.1/api",
            "http://172.31.255.255/api",
            "http://192.168.1.1/api",
        ]
        for url in blocked:
            error = validate_endpoint_url(url)
            assert error is not None, f"Expected {url} to be blocked"
            assert "SSRF blocked" in error

    def test_blocks_metadata_endpoint(self):
        """Cloud metadata endpoint (169.254.169.254) must be blocked."""
        error = validate_endpoint_url("http://169.254.169.254/latest/meta-data/")
        assert error is not None
        assert "SSRF blocked" in error

    def test_allows_external_urls(self):
        """Public URLs should be allowed."""
        allowed = [
            "https://api.openai.com/v1/chat",
            "https://search-service.prod.internal:8080/search",
            "http://external-tool.example.com/api",
        ]
        for url in allowed:
            error = validate_endpoint_url(url)
            assert error is None, f"Expected {url} to be allowed, got: {error}"

    def test_rejects_non_http_scheme(self):
        """Only http and https schemes are allowed."""
        error = validate_endpoint_url("ftp://server/file")
        assert error is not None
        assert "Invalid URL scheme" in error

    async def test_execute_blocks_ssrf(self):
        """execute() blocks SSRF-vulnerable endpoint URLs at runtime."""
        registry = ToolRegistry()
        defn = make_tool_definition(endpoint_url="http://169.254.169.254/latest/meta-data/")
        await registry.register(defn)

        result = await registry.execute("test_search", {"query": "test"})
        assert result.success is False
        assert result.error_type == "ssrf_blocked"

    def test_endpoint_url_validator_rejects_bad_scheme(self):
        """Field validator on ToolDefinition rejects non-http schemes."""
        with pytest.raises(ValueError, match="Invalid URL scheme"):
            make_tool_definition(endpoint_url="ftp://server/file")
