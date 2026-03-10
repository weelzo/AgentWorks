"""
Tests for Phase 5: LLM Gateway

Test layers:
  1. CircuitBreaker — pure state machine, deterministic, no I/O
  2. Provider selection / routing — capability filtering, priority sorting
  3. Gateway integration — mock HTTP, test failover, caching, cost tracking
  4. Provider adapters — verify request/response translation for each provider type
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentworks.llm_gateway import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerState,
    CompletionRequest,
    CompletionResponse,
    LLMGateway,
    LLMProvider,
    ModelConfig,
    ToolCallResponse,
    UsageInfo,
)

# --------------------------------------------------------------------------
# Test helpers / fixtures
# --------------------------------------------------------------------------


def make_model(
    model_id: str = "gpt-4",
    capabilities: list[str] | None = None,
    cost_input: float = 0.03,
    cost_output: float = 0.06,
) -> ModelConfig:
    """Create a test ModelConfig with sensible defaults."""
    return ModelConfig(
        model_id=model_id,
        capabilities=capabilities or ["chat", "function_calling"],
        cost_per_1k_input=cost_input,
        cost_per_1k_output=cost_output,
    )


def make_provider(
    provider_id: str = "openai-1",
    provider_type: str = "openai",
    priority: int = 0,
    models: list[ModelConfig] | None = None,
    enabled: bool = True,
) -> LLMProvider:
    """Create a test LLMProvider."""
    return LLMProvider(
        provider_id=provider_id,
        provider_type=provider_type,
        base_url="https://api.test.com/v1",
        api_key_ref="TEST_API_KEY",
        models=models or [make_model()],
        priority=priority,
        enabled=enabled,
    )


def make_request(
    messages: list[dict] | None = None,
    tools: list[dict] | None = None,
    model_preference: str | None = None,
    required_capabilities: list[str] | None = None,
) -> CompletionRequest:
    """Create a test CompletionRequest."""
    return CompletionRequest(
        messages=messages or [{"role": "user", "content": "Hello"}],
        tools=tools,
        model_preference=model_preference,
        required_capabilities=required_capabilities or ["chat"],
    )


class FakeCacheStore:
    """In-memory cache that implements the same interface as Redis."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._data[key] = value


class FakeSecretStore:
    """In-memory secret store."""

    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets = secrets or {}

    async def get(self, ref: str) -> str:
        return self._secrets.get(ref, "")


# --------------------------------------------------------------------------
# 1. Circuit Breaker Tests
# --------------------------------------------------------------------------


class TestCircuitBreakerInitialState:
    """The circuit breaker starts CLOSED and allows all requests."""

    def test_initial_state_is_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitBreakerState.CLOSED

    def test_closed_allows_requests(self):
        cb = CircuitBreaker()
        assert cb.allow_request() is True


class TestCircuitBreakerClosedToOpen:
    """CLOSED → OPEN when failure threshold or error rate is exceeded."""

    def test_trips_on_failure_count(self):
        """After N consecutive failures, circuit opens."""
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3))
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN

    def test_does_not_trip_below_threshold(self):
        """4 failures with threshold=5, with enough successes to keep error rate low."""
        cb = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=5,
                error_rate_threshold=0.9,
            )
        )
        # Interleave successes to keep error rate under 0.9
        # 4 failures + 5 successes = 4/9 ≈ 0.44 — well under 0.9
        for _ in range(5):
            cb.record_success()
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitBreakerState.CLOSED

    def test_trips_on_error_rate(self):
        """High error rate in the sliding window triggers OPEN."""
        cb = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=50,  # high count threshold — won't trigger
                error_rate_threshold=0.5,
            )
        )
        # 1 success, 3 failures → 75% error rate
        cb.record_success()
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN

    def test_open_rejects_requests(self):
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1))
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN
        assert cb.allow_request() is False


class TestCircuitBreakerOpenToHalfOpen:
    """OPEN → HALF_OPEN after recovery_timeout (lazy evaluation)."""

    def test_lazy_transition_after_timeout(self):
        cb = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_seconds=10,
            )
        )
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN

        # Simulate time passing beyond recovery timeout
        with patch("agentworks.llm_gateway.time.monotonic") as mock_time:
            # The last_failure_time was set during record_failure().
            # We need monotonic() to return a value > last_failure_time + 10
            mock_time.return_value = cb._last_failure_time + 11
            assert cb.state == CircuitBreakerState.HALF_OPEN

    def test_stays_open_before_timeout(self):
        cb = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_seconds=60,
            )
        )
        cb.record_failure()
        # monotonic() hasn't advanced enough — stays OPEN
        assert cb.state == CircuitBreakerState.OPEN

    def test_half_open_allows_requests(self):
        cb = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_seconds=10,
            )
        )
        cb.record_failure()
        with patch("agentworks.llm_gateway.time.monotonic") as mock_time:
            mock_time.return_value = cb._last_failure_time + 11
            assert cb.allow_request() is True


class TestCircuitBreakerHalfOpenRecovery:
    """HALF_OPEN → CLOSED after consecutive successes."""

    def _make_half_open(self) -> CircuitBreaker:
        """Helper: create a CB in HALF_OPEN state."""
        cb = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=1,
                half_open_max_requests=2,
            )
        )
        cb.record_failure()
        # Force to HALF_OPEN
        cb._state = CircuitBreakerState.HALF_OPEN
        cb._half_open_successes = 0
        return cb

    def test_recovers_after_consecutive_successes(self):
        cb = self._make_half_open()
        cb.record_success()
        assert cb.state == CircuitBreakerState.HALF_OPEN  # need 2
        cb.record_success()
        assert cb.state == CircuitBreakerState.CLOSED

    def test_failure_during_probe_reopens(self):
        """HALF_OPEN → OPEN on any failure during probe."""
        cb = self._make_half_open()
        cb.record_success()  # 1 of 2
        cb.record_failure()  # probe fails
        assert cb.state == CircuitBreakerState.OPEN

    def test_recovery_resets_failure_count(self):
        cb = self._make_half_open()
        cb.record_success()
        cb.record_success()
        assert cb._failure_count == 0


class TestCircuitBreakerSlidingWindow:
    """Sliding window prunes old timestamps for error rate calculation."""

    def test_old_failures_pruned(self):
        """Failures outside the window don't affect error rate."""
        cb = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=50,
                error_rate_threshold=0.5,
                window_size_seconds=60,
            )
        )
        # Record a failure in the past
        old_time = time.monotonic() - 120  # 2 minutes ago (outside 60s window)
        cb._failure_timestamps.append(old_time)
        cb._failure_count += 1

        # Record a success now — prune will remove old failure
        cb.record_success()
        # Error rate should be 0 (old failure pruned, only 1 success in window)
        assert cb._calculate_error_rate() == 0.0


# --------------------------------------------------------------------------
# 2. Data Model Tests
# --------------------------------------------------------------------------


class TestDataModels:
    """Pydantic model construction and defaults."""

    def test_model_config_defaults(self):
        m = ModelConfig(model_id="test-model")
        assert m.capabilities == ["chat"]
        assert m.context_window == 8192
        assert m.cost_per_1k_input == 0.0

    def test_provider_has_circuit_breaker_config(self):
        p = make_provider()
        assert p.circuit_breaker.failure_threshold == 5

    def test_completion_request_defaults(self):
        r = CompletionRequest(messages=[{"role": "user", "content": "hi"}])
        assert r.required_capabilities == ["chat"]
        assert r.timeout_seconds == 30.0

    def test_completion_response_construction(self):
        r = CompletionResponse(
            content="Hello!", usage=UsageInfo(prompt_tokens=10, completion_tokens=5)
        )
        assert r.content == "Hello!"
        assert r.cached is False

    def test_tool_call_response(self):
        tc = ToolCallResponse(id="call_1", name="search", arguments={"query": "test"})
        assert tc.name == "search"
        assert tc.arguments == {"query": "test"}


# --------------------------------------------------------------------------
# 3. Provider Selection Tests
# --------------------------------------------------------------------------


class TestProviderSelection:
    """Routing logic: capability filtering, priority, model preference."""

    def test_filters_by_capability(self):
        """Only providers with matching capabilities are selected."""
        chat_model = make_model(capabilities=["chat"])
        vision_model = make_model(model_id="gpt-4-vision", capabilities=["chat", "vision"])
        gw = LLMGateway(
            providers=[
                make_provider(provider_id="basic", models=[chat_model]),
                make_provider(provider_id="vision", models=[vision_model]),
            ]
        )
        req = make_request(required_capabilities=["chat", "vision"])
        candidates = gw._select_providers(req)
        assert len(candidates) == 1
        assert candidates[0][0].provider_id == "vision"

    def test_excludes_disabled_providers(self):
        gw = LLMGateway(
            providers=[
                make_provider(provider_id="active", enabled=True),
                make_provider(provider_id="disabled", enabled=False),
            ]
        )
        req = make_request()
        candidates = gw._select_providers(req)
        ids = [p.provider_id for p, _ in candidates]
        assert "active" in ids
        assert "disabled" not in ids

    def test_sorts_by_priority(self):
        """Lower priority value = higher priority (tried first)."""
        gw = LLMGateway(
            providers=[
                make_provider(provider_id="low", priority=10),
                make_provider(provider_id="high", priority=0),
            ]
        )
        req = make_request()
        candidates = gw._select_providers(req)
        assert candidates[0][0].provider_id == "high"
        assert candidates[1][0].provider_id == "low"

    def test_model_preference_boosts_preferred(self):
        """Preferred model gets original priority, others get +100."""
        model_a = make_model(model_id="gpt-4")
        model_b = make_model(model_id="gpt-3.5")
        gw = LLMGateway(
            providers=[
                make_provider(
                    provider_id="p1",
                    priority=0,
                    models=[model_a, model_b],
                ),
            ]
        )
        req = make_request(model_preference="gpt-3.5")
        candidates = gw._select_providers(req)
        # gpt-3.5 should come first (priority 0), gpt-4 second (priority 100)
        assert candidates[0][1].model_id == "gpt-3.5"
        assert candidates[1][1].model_id == "gpt-4"

    def test_no_matching_providers_returns_empty(self):
        gw = LLMGateway(
            providers=[
                make_provider(models=[make_model(capabilities=["chat"])]),
            ]
        )
        req = make_request(required_capabilities=["streaming"])
        candidates = gw._select_providers(req)
        assert candidates == []


# --------------------------------------------------------------------------
# 4. Gateway Integration Tests (mock HTTP)
# --------------------------------------------------------------------------


class TestGatewayComplete:
    """End-to-end gateway.complete() with mocked provider calls."""

    @pytest.mark.asyncio
    async def test_successful_call(self):
        """Basic successful completion through the gateway."""
        gw = LLMGateway(providers=[make_provider()])

        mock_response = CompletionResponse(
            content="Hello!",
            usage=UsageInfo(prompt_tokens=10, completion_tokens=5),
        )

        with patch.object(gw, "_call_provider", return_value=mock_response):
            result = await gw.complete(make_request())

        assert result.content == "Hello!"
        assert result.provider_id == "openai-1"
        assert result.model_id == "gpt-4"
        assert result.model_cost.input_per_1k == 0.03
        assert result.model_cost.output_per_1k == 0.06
        assert result.usage.total_tokens == 15

    @pytest.mark.asyncio
    async def test_kwargs_interface(self):
        """Engine calls complete(messages=..., tools=..., metadata=...)."""
        gw = LLMGateway(providers=[make_provider()])

        mock_response = CompletionResponse(content="Hi")
        with patch.object(gw, "_call_provider", return_value=mock_response):
            result = await gw.complete(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                metadata={"run_id": "run-1"},
            )
        assert result.content == "Hi"

    @pytest.mark.asyncio
    async def test_failover_to_secondary_provider(self):
        """First provider fails, second succeeds."""
        gw = LLMGateway(
            providers=[
                make_provider(provider_id="primary", priority=0),
                make_provider(provider_id="backup", priority=10),
            ]
        )

        call_count = 0

        async def mock_call(provider, model, request):
            nonlocal call_count
            call_count += 1
            if provider.provider_id == "primary":
                raise ConnectionError("Primary down")
            return CompletionResponse(content="From backup")

        with patch.object(gw, "_call_provider", side_effect=mock_call):
            result = await gw.complete(make_request())

        assert result.content == "From backup"
        assert result.provider_id == "backup"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_all_providers_fail_raises(self):
        """When every provider fails, raise RuntimeError."""
        gw = LLMGateway(providers=[make_provider()])

        with (
            patch.object(gw, "_call_provider", side_effect=ConnectionError("Down")),
            pytest.raises(RuntimeError, match="All providers failed"),
        ):
            await gw.complete(make_request())

    @pytest.mark.asyncio
    async def test_no_capable_providers_raises(self):
        """When no providers match capabilities, raise RuntimeError."""
        gw = LLMGateway(
            providers=[
                make_provider(models=[make_model(capabilities=["chat"])]),
            ]
        )
        req = make_request(required_capabilities=["vision"])
        with pytest.raises(RuntimeError, match="No providers available"):
            await gw.complete(req)

    @pytest.mark.asyncio
    async def test_circuit_breaker_skips_open_provider(self):
        """A provider with OPEN circuit breaker is skipped."""
        gw = LLMGateway(
            providers=[
                make_provider(provider_id="broken", priority=0),
                make_provider(provider_id="healthy", priority=10),
            ]
        )
        # Trip the circuit breaker on "broken"
        cb = gw._circuit_breakers["broken"]
        for _ in range(5):
            cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN

        mock_response = CompletionResponse(content="Healthy")
        with patch.object(gw, "_call_provider", return_value=mock_response):
            result = await gw.complete(make_request())

        assert result.provider_id == "healthy"

    @pytest.mark.asyncio
    async def test_failed_call_records_circuit_breaker_failure(self):
        """A failed provider call records failure in its circuit breaker."""
        gw = LLMGateway(
            providers=[
                make_provider(provider_id="flaky"),
                make_provider(provider_id="backup"),
            ]
        )
        cb = gw._circuit_breakers["flaky"]
        initial_failures = cb._failure_count

        async def mock_call(provider, model, request):
            if provider.provider_id == "flaky":
                raise TimeoutError("Timeout")
            return CompletionResponse(content="OK")

        with patch.object(gw, "_call_provider", side_effect=mock_call):
            await gw.complete(make_request())

        assert cb._failure_count == initial_failures + 1

    @pytest.mark.asyncio
    async def test_latency_tracked(self):
        """Response includes latency_ms."""
        gw = LLMGateway(providers=[make_provider()])
        mock_response = CompletionResponse(content="Fast")
        with patch.object(gw, "_call_provider", return_value=mock_response):
            result = await gw.complete(make_request())
        assert result.latency_ms >= 0


# --------------------------------------------------------------------------
# 5. Caching Tests
# --------------------------------------------------------------------------


class TestCaching:
    """Response caching with FakeCacheStore."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_response(self):
        """Cached response is returned without calling provider."""
        cache = FakeCacheStore()
        gw = LLMGateway(providers=[make_provider()], cache_store=cache)

        # Seed the cache
        req = make_request()
        cached_response = CompletionResponse(content="Cached!")
        key = gw._make_cache_key(req)
        await cache.setex(key, 300, cached_response.model_dump_json())

        # Should hit cache — _call_provider should NOT be called
        with patch.object(gw, "_call_provider") as mock_call:
            result = await gw.complete(req)
            mock_call.assert_not_called()

        assert result.content == "Cached!"
        assert result.cached is True

    @pytest.mark.asyncio
    async def test_cache_miss_calls_provider_and_caches(self):
        """On cache miss, call provider and store result."""
        cache = FakeCacheStore()
        gw = LLMGateway(providers=[make_provider()], cache_store=cache)

        mock_response = CompletionResponse(
            content="Fresh",
            usage=UsageInfo(prompt_tokens=5, completion_tokens=3),
        )
        with patch.object(gw, "_call_provider", return_value=mock_response):
            result = await gw.complete(make_request())

        assert result.content == "Fresh"
        assert result.cached is False

        # Verify it was stored in cache
        req = make_request()
        key = gw._make_cache_key(req)
        assert await cache.get(key) is not None

    @pytest.mark.asyncio
    async def test_cache_skipped_when_tools_present(self):
        """Don't cache requests that include tools (non-deterministic)."""
        cache = FakeCacheStore()
        gw = LLMGateway(providers=[make_provider()], cache_store=cache)

        req = make_request(tools=[{"function": {"name": "search"}}])
        mock_response = CompletionResponse(content="Used tool")
        with patch.object(gw, "_call_provider", return_value=mock_response):
            result = await gw.complete(req)

        assert result.content == "Used tool"
        # Verify nothing was cached
        key = gw._make_cache_key(req)
        assert await cache.get(key) is None

    @pytest.mark.asyncio
    async def test_cache_skipped_when_response_has_tool_calls(self):
        """Don't cache responses that contain tool calls."""
        cache = FakeCacheStore()
        gw = LLMGateway(providers=[make_provider()], cache_store=cache)

        mock_response = CompletionResponse(
            content="Calling tool",
            tool_calls=[ToolCallResponse(id="c1", name="search", arguments={})],
        )
        with patch.object(gw, "_call_provider", return_value=mock_response):
            await gw.complete(make_request())

        # Cache should be empty
        key = gw._make_cache_key(make_request())
        assert await cache.get(key) is None

    @pytest.mark.asyncio
    async def test_no_cache_store_works_fine(self):
        """Gateway works without a cache store (cache_store=None)."""
        gw = LLMGateway(providers=[make_provider()], cache_store=None)
        mock_response = CompletionResponse(content="No cache")
        with patch.object(gw, "_call_provider", return_value=mock_response):
            result = await gw.complete(make_request())
        assert result.content == "No cache"


# --------------------------------------------------------------------------
# 6. Secret Management Tests
# --------------------------------------------------------------------------


class TestSecretManagement:
    """API key resolution: secret store → env var → literal fallback."""

    @pytest.mark.asyncio
    async def test_secret_store_takes_precedence(self):
        secrets = FakeSecretStore({"OPENAI_KEY": "sk-secret-123"})
        gw = LLMGateway(
            providers=[make_provider(provider_id="p1")],
            secret_store=secrets,
        )
        key = await gw._get_api_key("OPENAI_KEY")
        assert key == "sk-secret-123"

    @pytest.mark.asyncio
    async def test_env_var_fallback(self, monkeypatch):
        """Without a secret store, use env var."""
        monkeypatch.setenv("MY_API_KEY", "sk-from-env")
        gw = LLMGateway(providers=[])
        key = await gw._get_api_key("MY_API_KEY")
        assert key == "sk-from-env"

    @pytest.mark.asyncio
    async def test_literal_fallback(self):
        """When neither secret store nor env var exists, return ref as-is."""
        gw = LLMGateway(providers=[])
        key = await gw._get_api_key("not-an-env-var")
        assert key == "not-an-env-var"


# --------------------------------------------------------------------------
# 7. Provider Adapter Tests (OpenAI, Anthropic, Azure)
# --------------------------------------------------------------------------


def _make_openai_response(
    content: str = "Hello",
    tool_calls: list | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> dict:
    """Build a fake OpenAI API response body."""
    message: dict[str, Any] = {"content": content, "role": "assistant"}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


def _make_anthropic_response(
    content_blocks: list[dict] | None = None,
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> dict:
    """Build a fake Anthropic API response body."""
    if content_blocks is None:
        content_blocks = [{"type": "text", "text": "Hello"}]
    return {
        "content": content_blocks,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }


class TestOpenAIAdapter:
    """Verify OpenAI request format and response parsing."""

    @pytest.mark.asyncio
    async def test_basic_completion(self):
        gw = LLMGateway(providers=[make_provider()])
        provider = make_provider()
        model = make_model()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _make_openai_response("Hi there")

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        gw._http_client = mock_client

        result = await gw._call_openai(provider, model, make_request(), api_key="sk-test")
        assert result.content == "Hi there"
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 5

    @pytest.mark.asyncio
    async def test_tool_call_parsing(self):
        gw = LLMGateway(providers=[make_provider()])

        openai_resp = _make_openai_response(
            content=None,
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "search",
                        "arguments": '{"query": "test"}',
                    },
                }
            ],
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = openai_resp

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        gw._http_client = mock_client

        result = await gw._call_openai(make_provider(), make_model(), make_request(), "sk-test")
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "search"
        assert result.tool_calls[0].arguments == {"query": "test"}

    @pytest.mark.asyncio
    async def test_malformed_json_arguments(self):
        """Arguments that aren't valid JSON get wrapped in {raw: ...}."""
        gw = LLMGateway(providers=[make_provider()])

        openai_resp = _make_openai_response(
            content=None,
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "search",
                        "arguments": "not-valid-json",
                    },
                }
            ],
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = openai_resp

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        gw._http_client = mock_client

        result = await gw._call_openai(make_provider(), make_model(), make_request(), "sk-test")
        assert result.tool_calls[0].arguments == {"raw": "not-valid-json"}

    @pytest.mark.asyncio
    async def test_request_body_includes_tools(self):
        """When tools are in the request, they appear in the POST body."""
        gw = LLMGateway(providers=[make_provider()])

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _make_openai_response()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        gw._http_client = mock_client

        tools = [{"type": "function", "function": {"name": "search"}}]
        req = make_request(tools=tools)
        await gw._call_openai(make_provider(), make_model(), req, "sk-test")

        # Check the body that was sent
        call_args = mock_client.post.call_args
        body = call_args.kwargs["json"]
        assert "tools" in body
        assert body["tool_choice"] == "auto"


class TestAnthropicAdapter:
    """Verify Anthropic request translation and response parsing."""

    @pytest.mark.asyncio
    async def test_system_message_extraction(self):
        """System messages are extracted to top-level 'system' param."""
        gw = LLMGateway(providers=[make_provider(provider_type="anthropic")])

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _make_anthropic_response()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        gw._http_client = mock_client

        provider = make_provider(provider_type="anthropic")
        req = make_request(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi"},
            ]
        )

        await gw._call_anthropic(provider, make_model(), req, "sk-ant-test")

        body = mock_client.post.call_args.kwargs["json"]
        assert body["system"] == "You are helpful."
        # Non-system messages only
        assert len(body["messages"]) == 1
        assert body["messages"][0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_tool_format_translation(self):
        """OpenAI tool format is translated to Anthropic format."""
        gw = LLMGateway(providers=[make_provider(provider_type="anthropic")])

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _make_anthropic_response()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        gw._http_client = mock_client

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "Search the web",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        req = make_request(tools=openai_tools)

        await gw._call_anthropic(
            make_provider(provider_type="anthropic"), make_model(), req, "sk-ant"
        )

        body = mock_client.post.call_args.kwargs["json"]
        assert body["tools"][0]["name"] == "search"
        assert "input_schema" in body["tools"][0]
        assert "parameters" not in body["tools"][0]

    @pytest.mark.asyncio
    async def test_content_block_parsing(self):
        """Multiple content blocks (text + tool_use) are parsed correctly."""
        gw = LLMGateway(providers=[make_provider(provider_type="anthropic")])

        anthropic_resp = _make_anthropic_response(
            content_blocks=[
                {"type": "text", "text": "Let me search for that."},
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "search",
                    "input": {"query": "test"},
                },
            ]
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = anthropic_resp

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        gw._http_client = mock_client

        result = await gw._call_anthropic(
            make_provider(provider_type="anthropic"), make_model(), make_request(), "sk-ant"
        )
        assert result.content == "Let me search for that."
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "search"
        assert result.tool_calls[0].arguments == {"query": "test"}

    @pytest.mark.asyncio
    async def test_anthropic_auth_headers(self):
        """Anthropic uses x-api-key header (not Bearer auth)."""
        gw = LLMGateway(providers=[make_provider(provider_type="anthropic")])

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _make_anthropic_response()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        gw._http_client = mock_client

        await gw._call_anthropic(
            make_provider(provider_type="anthropic"), make_model(), make_request(), "sk-ant-key"
        )

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["x-api-key"] == "sk-ant-key"
        assert "Authorization" not in headers

    @pytest.mark.asyncio
    async def test_anthropic_token_field_mapping(self):
        """Anthropic uses input_tokens/output_tokens, not prompt/completion."""
        gw = LLMGateway(providers=[make_provider(provider_type="anthropic")])

        anthropic_resp = _make_anthropic_response(input_tokens=42, output_tokens=18)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = anthropic_resp

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        gw._http_client = mock_client

        result = await gw._call_anthropic(
            make_provider(provider_type="anthropic"), make_model(), make_request(), "sk-ant"
        )
        assert result.usage.prompt_tokens == 42
        assert result.usage.completion_tokens == 18


class TestAzureOpenAIAdapter:
    """Verify Azure-specific URL format and auth."""

    @pytest.mark.asyncio
    async def test_deployment_url_format(self):
        """Azure uses /openai/deployments/{model}/chat/completions."""
        gw = LLMGateway(providers=[make_provider(provider_type="azure_openai")])

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _make_openai_response()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        gw._http_client = mock_client

        provider = make_provider(
            provider_type="azure_openai",
        )
        provider.base_url = "https://my-resource.openai.azure.com"
        model = make_model(model_id="gpt-4-deployment")

        await gw._call_azure_openai(provider, model, make_request(), "az-key")

        url = mock_client.post.call_args.args[0]
        assert "/openai/deployments/gpt-4-deployment/chat/completions" in url

    @pytest.mark.asyncio
    async def test_azure_auth_header(self):
        """Azure uses api-key header (not Bearer)."""
        gw = LLMGateway(providers=[make_provider(provider_type="azure_openai")])

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _make_openai_response()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        gw._http_client = mock_client

        await gw._call_azure_openai(
            make_provider(provider_type="azure_openai"), make_model(), make_request(), "az-key-123"
        )

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["api-key"] == "az-key-123"
        assert "Authorization" not in headers

    @pytest.mark.asyncio
    async def test_azure_api_version_param(self):
        """Azure includes api-version query parameter."""
        gw = LLMGateway(providers=[make_provider(provider_type="azure_openai")])

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _make_openai_response()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        gw._http_client = mock_client

        await gw._call_azure_openai(
            make_provider(provider_type="azure_openai"), make_model(), make_request(), "az-key"
        )

        params = mock_client.post.call_args.kwargs["params"]
        assert params["api-version"] == "2024-02-01"


# --------------------------------------------------------------------------
# 8. Provider Status Tests
# --------------------------------------------------------------------------


class TestProviderStatus:
    """Admin status endpoint."""

    def test_returns_all_providers(self):
        gw = LLMGateway(
            providers=[
                make_provider(provider_id="p1"),
                make_provider(provider_id="p2"),
            ]
        )
        status = gw.get_provider_status()
        assert len(status) == 2
        assert status[0]["provider_id"] == "p1"
        assert status[1]["provider_id"] == "p2"

    def test_includes_circuit_breaker_state(self):
        gw = LLMGateway(providers=[make_provider(provider_id="p1")])
        status = gw.get_provider_status()
        assert status[0]["circuit_breaker_state"] == "closed"

    def test_reflects_tripped_circuit_breaker(self):
        gw = LLMGateway(
            providers=[
                make_provider(
                    provider_id="broken",
                    models=[make_model()],
                )
            ]
        )
        cb = gw._circuit_breakers["broken"]
        for _ in range(5):
            cb.record_failure()
        status = gw.get_provider_status()
        assert status[0]["circuit_breaker_state"] == "open"


# --------------------------------------------------------------------------
# 9. Cache Key Determinism
# --------------------------------------------------------------------------


class TestCacheKey:
    """Cache key is deterministic and content-based."""

    def test_same_request_same_key(self):
        gw = LLMGateway(providers=[])
        req = make_request()
        assert gw._make_cache_key(req) == gw._make_cache_key(req)

    def test_different_messages_different_key(self):
        gw = LLMGateway(providers=[])
        req1 = make_request(messages=[{"role": "user", "content": "Hello"}])
        req2 = make_request(messages=[{"role": "user", "content": "World"}])
        assert gw._make_cache_key(req1) != gw._make_cache_key(req2)

    def test_key_starts_with_prefix(self):
        gw = LLMGateway(providers=[])
        key = gw._make_cache_key(make_request())
        assert key.startswith("llm:cache:")


# --------------------------------------------------------------------------
# 10. HTTP Client Lifecycle
# --------------------------------------------------------------------------


class TestHTTPClientLifecycle:
    """Lazy init and cleanup of httpx.AsyncClient."""

    @pytest.mark.asyncio
    async def test_lazy_init(self):
        gw = LLMGateway(providers=[])
        assert gw._http_client is None
        client = await gw._get_client()
        assert client is not None
        assert gw._http_client is client

    @pytest.mark.asyncio
    async def test_close_cleans_up(self):
        gw = LLMGateway(providers=[])
        await gw._get_client()
        assert gw._http_client is not None
        await gw.close()
        assert gw._http_client is None
