"""
Phase 5: LLM Gateway

Multi-provider LLM access with routing, failover, caching, and cost tracking.

Architecture:
  - Provider abstraction: OpenAI, Anthropic, Azure OpenAI, custom (OpenAI-compatible)
  - Circuit breaker per provider: CLOSED → OPEN → HALF_OPEN → CLOSED
  - Capability-based routing with priority ordering and model preference
  - Exact-match response caching (5 min TTL, skipped for tool calls)
  - Per-call cost tracking with team/run attribution via metadata

Why custom instead of LiteLLM:
  1. Circuit breaker granularity (provider-level with error rate windows)
  2. Per-team cost tracking (team_id, project_id, run_id on every call)
  3. Semantic caching interface (exact-match now, embeddings later)
  4. Critical path ownership (~600 lines, any engineer can debug)

Performance targets:
  - Routing overhead: <2ms
  - Cache hit: <5ms
  - Circuit breaker evaluation: <0.1ms
  - Failover to secondary provider: <2s
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from enum import StrEnum
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Circuit Breaker
# --------------------------------------------------------------------------


class CircuitBreakerState(StrEnum):
    """Three states of the circuit breaker."""

    CLOSED = "closed"  # Normal operation — requests flow through
    OPEN = "open"  # Failing — all requests rejected immediately
    HALF_OPEN = "half_open"  # Recovery probe — limited requests allowed


class CircuitBreakerConfig(BaseModel):
    """Tuning knobs for the per-provider circuit breaker."""

    failure_threshold: int = Field(default=5, ge=1, le=50)
    recovery_timeout_seconds: int = Field(default=60, ge=10, le=600)
    half_open_max_requests: int = Field(default=2, ge=1, le=10)
    error_rate_threshold: float = Field(default=0.5, ge=0.1, le=1.0)
    window_size_seconds: int = Field(default=300, ge=30, le=900)


class CircuitBreaker:
    """
    Per-provider circuit breaker with sliding window error rate.

    State transitions:
      CLOSED → OPEN:     failure_count >= threshold OR error_rate >= threshold
      OPEN → HALF_OPEN:  recovery_timeout has elapsed (lazy evaluation)
      HALF_OPEN → CLOSED: half_open_max_requests consecutive successes
      HALF_OPEN → OPEN:   any single failure during probe
    """

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self._config = config or CircuitBreakerConfig()
        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_successes = 0
        self._last_failure_time: float = 0.0
        # Sliding window timestamps for error rate calculation
        self._failure_timestamps: list[float] = []
        self._success_timestamps: list[float] = []

    @property
    def state(self) -> CircuitBreakerState:
        """
        Current state with lazy OPEN → HALF_OPEN evaluation.

        Instead of a background timer, we check if recovery_timeout
        has elapsed every time the state is queried. This avoids
        background threads and is perfectly deterministic.
        """
        if self._state == CircuitBreakerState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._config.recovery_timeout_seconds:
                self._state = CircuitBreakerState.HALF_OPEN
                self._half_open_successes = 0
                logger.info("Circuit breaker transitioning OPEN → HALF_OPEN")
        return self._state

    def allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        current = self.state  # triggers lazy evaluation
        # CLOSED and HALF_OPEN allow requests; OPEN rejects
        return current != CircuitBreakerState.OPEN

    def record_success(self) -> None:
        """Record a successful request."""
        now = time.monotonic()
        self._success_timestamps.append(now)
        self._success_count += 1

        if self._state == CircuitBreakerState.HALF_OPEN:
            self._half_open_successes += 1
            if self._half_open_successes >= self._config.half_open_max_requests:
                self._state = CircuitBreakerState.CLOSED
                self._failure_count = 0
                self._half_open_successes = 0
                logger.info("Circuit breaker recovered: HALF_OPEN → CLOSED")
        elif self._state == CircuitBreakerState.CLOSED:
            # Prune old timestamps
            self._prune_old_timestamps(now)

    def record_failure(self) -> None:
        """Record a failed request."""
        now = time.monotonic()
        self._failure_timestamps.append(now)
        self._failure_count += 1
        self._last_failure_time = now

        if self._state == CircuitBreakerState.HALF_OPEN:
            # Any failure during probe → back to OPEN
            self._state = CircuitBreakerState.OPEN
            self._half_open_successes = 0
            logger.warning("Circuit breaker probe failed: HALF_OPEN → OPEN")
        elif self._state == CircuitBreakerState.CLOSED:
            self._prune_old_timestamps(now)
            # Check both absolute threshold and error rate
            if self._failure_count >= self._config.failure_threshold:
                self._state = CircuitBreakerState.OPEN
                logger.warning("Circuit breaker tripped (failure count): CLOSED → OPEN")
            elif self._calculate_error_rate() >= self._config.error_rate_threshold:
                self._state = CircuitBreakerState.OPEN
                logger.warning(
                    "Circuit breaker tripped (error rate %.1f%%): CLOSED → OPEN",
                    self._calculate_error_rate() * 100,
                )

    def _calculate_error_rate(self) -> float:
        """Error rate over the sliding window."""
        total = len(self._failure_timestamps) + len(self._success_timestamps)
        if total == 0:
            return 0.0
        return len(self._failure_timestamps) / total

    def _prune_old_timestamps(self, now: float) -> None:
        """Remove timestamps outside the sliding window."""
        cutoff = now - self._config.window_size_seconds
        self._failure_timestamps = [t for t in self._failure_timestamps if t >= cutoff]
        self._success_timestamps = [t for t in self._success_timestamps if t >= cutoff]


# --------------------------------------------------------------------------
# Data Models
# --------------------------------------------------------------------------


class ModelConfig(BaseModel):
    """Configuration for a specific LLM model."""

    model_id: str
    display_name: str = ""
    capabilities: list[Literal["chat", "function_calling", "vision", "streaming", "json_mode"]] = (
        Field(default_factory=lambda: ["chat"])  # type: ignore[arg-type]
    )
    context_window: int = Field(default=8192, ge=1024)
    max_output_tokens: int = Field(default=4096, ge=256)
    cost_per_1k_input: float = Field(default=0.0, ge=0.0)
    cost_per_1k_output: float = Field(default=0.0, ge=0.0)
    supports_system_message: bool = True
    supports_tool_choice: bool = True
    default_temperature: float = Field(default=0.1, ge=0.0, le=2.0)


class LLMProvider(BaseModel):
    """Configuration for an LLM provider (e.g., OpenAI, Anthropic)."""

    provider_id: str
    provider_type: Literal["openai", "anthropic", "azure_openai", "custom"]
    display_name: str = ""
    base_url: str
    api_key_ref: str  # reference to secret store, NOT the actual key
    models: list[ModelConfig] = Field(default_factory=list)
    priority: int = Field(default=0, ge=0, le=100)  # lower = higher priority
    weight: float = Field(default=1.0, ge=0.0, le=100.0)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    max_concurrent_requests: int = Field(default=100, ge=1, le=10000)
    enabled: bool = True
    region: str = ""  # for future latency-aware routing
    tags: list[str] = Field(default_factory=list)


class ToolCallResponse(BaseModel):
    """A single tool call from the LLM response."""

    id: str = ""
    name: str = ""
    arguments: dict[str, Any] | str = Field(default_factory=dict)


class UsageInfo(BaseModel):
    """Token usage information from the LLM response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ModelCost(BaseModel):
    """Cost rates for the model used — allows downstream re-calculation."""

    input_per_1k: float = 0.0
    output_per_1k: float = 0.0


class CompletionRequest(BaseModel):
    """Request to the LLM gateway."""

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = "auto"
    model_preference: str | None = None
    required_capabilities: list[str] = Field(default_factory=lambda: ["chat"])
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    cache_key: str | None = None
    timeout_seconds: float = 30.0


class CompletionResponse(BaseModel):
    """Response from the LLM gateway."""

    content: str | None = None
    tool_calls: list[ToolCallResponse] = Field(default_factory=list)
    usage: UsageInfo = Field(default_factory=UsageInfo)
    model_cost: ModelCost = Field(default_factory=ModelCost)
    model_id: str = ""
    provider_id: str = ""
    latency_ms: float = 0.0
    cached: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# LLM Gateway
# --------------------------------------------------------------------------


class LLMGateway:
    """
    Multi-provider LLM gateway with routing, failover, and cost tracking.

    Routing strategy (in order):
      1. If model_preference is set and available, use it
      2. Filter providers by required_capabilities
      3. Exclude providers with OPEN circuit breakers
      4. Sort by priority (lower = higher priority)
      5. Try providers in order until one succeeds

    Caching:
      - Exact match on (messages + tools + model + temperature)
      - TTL: 5 minutes
      - Skipped when tools are present (non-deterministic)
      - Skipped when response contains tool_calls
    """

    CACHE_TTL_SECONDS = 300  # 5 minutes

    def __init__(
        self,
        providers: list[LLMProvider],
        cache_store: Any | None = None,
        secret_store: Any | None = None,
    ) -> None:
        self._providers = providers
        self._cache = cache_store
        self._secrets = secret_store
        self._circuit_breakers: dict[str, CircuitBreaker] = {
            p.provider_id: CircuitBreaker(p.circuit_breaker) for p in providers
        }
        self._http_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-initialize the HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=60.0)
        return self._http_client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def complete(
        self,
        request: CompletionRequest | None = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        """
        Send a completion request through the gateway.

        Accepts either a CompletionRequest object or keyword arguments
        that match the ExecutionEngine's calling convention:
          await gateway.complete(messages=[...], tools=[...], metadata={...})
        """
        if request is None:
            request = CompletionRequest(**kwargs)

        # Step 1: Check cache (skip if tools are present)
        if self._cache is not None and not request.tools:
            cached = await self._check_cache(request)
            if cached is not None:
                return cached

        # Step 2: Select providers in priority order
        candidates = self._select_providers(request)
        if not candidates:
            raise RuntimeError(
                f"No providers available for capabilities: {request.required_capabilities}"
            )

        # Step 3: Try each provider with circuit breaker gating
        last_error: str = ""
        for provider, model in candidates:
            cb = self._circuit_breakers[provider.provider_id]

            if not cb.allow_request():
                logger.debug(
                    "Skipping provider %s (circuit breaker OPEN)",
                    provider.provider_id,
                )
                continue

            try:
                start = time.monotonic()
                response = await self._call_provider(provider, model, request)
                elapsed_ms = (time.monotonic() - start) * 1000

                # Record success
                cb.record_success()

                # Attach metadata
                response.latency_ms = elapsed_ms
                response.provider_id = provider.provider_id
                response.model_id = model.model_id
                response.model_cost = ModelCost(
                    input_per_1k=model.cost_per_1k_input,
                    output_per_1k=model.cost_per_1k_output,
                )
                response.usage.total_tokens = (
                    response.usage.prompt_tokens + response.usage.completion_tokens
                )

                # Cost logging
                call_cost = (
                    response.usage.prompt_tokens / 1000 * model.cost_per_1k_input
                    + response.usage.completion_tokens / 1000 * model.cost_per_1k_output
                )
                logger.info(
                    "LLM call: provider=%s model=%s latency=%.0fms tokens=%d cost=$%.4f",
                    provider.provider_id,
                    model.model_id,
                    elapsed_ms,
                    response.usage.total_tokens,
                    call_cost,
                )

                # Cache the response (skip if tool calls present)
                if self._cache is not None and not request.tools and not response.tool_calls:
                    await self._store_cache(request, response)

                return response

            except Exception as e:
                cb.record_failure()
                last_error = f"{provider.provider_id}: {e!s}"
                logger.warning("Provider %s failed: %s", provider.provider_id, e)
                continue

        raise RuntimeError(f"All providers failed. Last error: {last_error}")

    # ------------------------------------------------------------------
    # Provider selection / routing
    # ------------------------------------------------------------------

    def _select_providers(
        self, request: CompletionRequest
    ) -> list[tuple[LLMProvider, ModelConfig]]:
        """
        Select and prioritize providers for the request.

        Returns (provider, model) tuples sorted by effective priority.
        """
        candidates: list[tuple[LLMProvider, ModelConfig, int]] = []

        for provider in self._providers:
            if not provider.enabled:
                continue

            for model in provider.models:
                # Check required capabilities
                if not all(cap in model.capabilities for cap in request.required_capabilities):
                    continue

                # Calculate effective priority
                effective_priority = provider.priority
                if request.model_preference and model.model_id != request.model_preference:
                    # Non-preferred models are deprioritized but not excluded
                    effective_priority += 100

                candidates.append((provider, model, effective_priority))

        # Sort by effective priority (lower = higher priority)
        candidates.sort(key=lambda c: c[2])
        return [(p, m) for p, m, _ in candidates]

    # ------------------------------------------------------------------
    # Provider-specific adapters
    # ------------------------------------------------------------------

    async def _call_provider(
        self,
        provider: LLMProvider,
        model: ModelConfig,
        request: CompletionRequest,
    ) -> CompletionResponse:
        """Dispatch to the provider-specific adapter."""
        api_key = await self._get_api_key(provider.api_key_ref)

        if provider.provider_type == "openai":
            return await self._call_openai(provider, model, request, api_key)
        elif provider.provider_type == "anthropic":
            return await self._call_anthropic(provider, model, request, api_key)
        elif provider.provider_type == "azure_openai":
            return await self._call_azure_openai(provider, model, request, api_key)
        else:
            # "custom" — assume OpenAI-compatible
            return await self._call_openai(provider, model, request, api_key)

    @staticmethod
    def _format_openai_messages(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Re-format messages for the OpenAI Chat Completions API.

        The engine stores tool calls in a flat format ({id, name, arguments})
        but OpenAI requires the nested format:
          {id, type: "function", function: {name, arguments_as_json_string}}
        Also strips internal fields like 'timestamp' that OpenAI rejects.
        """
        formatted = []
        for msg in messages:
            out: dict[str, Any] = {"role": msg["role"]}

            if msg.get("content") is not None:
                out["content"] = msg["content"]
            elif msg["role"] == "assistant":
                # OpenAI requires content to be null (not missing) for
                # assistant messages with tool_calls
                out["content"] = None

            # Assistant messages with tool_calls → nest into OpenAI format
            if msg.get("tool_calls"):
                openai_tcs = []
                for tc in msg["tool_calls"]:
                    # Already in OpenAI format (has "function" key)?
                    if "function" in tc:
                        openai_tcs.append(tc)
                    else:
                        args = tc.get("arguments", {})
                        if not isinstance(args, str):
                            args = json.dumps(args)
                        openai_tcs.append(
                            {
                                "id": tc.get("id", ""),
                                "type": "function",
                                "function": {
                                    "name": tc.get("name", ""),
                                    "arguments": args,
                                },
                            }
                        )
                out["tool_calls"] = openai_tcs

            # Tool result messages
            if msg.get("tool_call_id"):
                out["tool_call_id"] = msg["tool_call_id"]
            if msg["role"] == "tool" and msg.get("name"):
                out["name"] = msg["name"]

            formatted.append(out)
        return formatted

    async def _call_openai(
        self,
        provider: LLMProvider,
        model: ModelConfig,
        request: CompletionRequest,
        api_key: str,
    ) -> CompletionResponse:
        """Call the OpenAI Chat Completions API."""
        client = await self._get_client()
        url = f"{provider.base_url.rstrip('/')}/chat/completions"

        body: dict[str, Any] = {
            "model": model.model_id,
            "messages": self._format_openai_messages(request.messages),
            "temperature": request.temperature or model.default_temperature,
        }
        if request.tools:
            body["tools"] = request.tools
            if request.tool_choice is not None:
                body["tool_choice"] = request.tool_choice
        if request.max_tokens:
            body["max_tokens"] = request.max_tokens

        resp = await client.post(
            url,
            json=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=request.timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()

        # Parse response
        choice = data["choices"][0]["message"]
        tool_calls = []
        if choice.get("tool_calls"):
            for tc in choice["tool_calls"]:
                args = tc["function"].get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                tool_calls.append(
                    ToolCallResponse(
                        id=tc.get("id", ""),
                        name=tc["function"]["name"],
                        arguments=args,
                    )
                )

        usage = data.get("usage", {})
        return CompletionResponse(
            content=choice.get("content"),
            tool_calls=tool_calls,
            usage=UsageInfo(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            ),
        )

    async def _call_anthropic(
        self,
        provider: LLMProvider,
        model: ModelConfig,
        request: CompletionRequest,
        api_key: str,
    ) -> CompletionResponse:
        """
        Call the Anthropic Messages API.

        Key translation differences from OpenAI:
          - System messages extracted and passed as top-level 'system' param
          - Tools use 'input_schema' instead of 'parameters'
          - Response content is a list of blocks (text, tool_use)
          - Token fields: input_tokens/output_tokens (not prompt/completion)
        """
        client = await self._get_client()
        url = f"{provider.base_url.rstrip('/')}/messages"

        # Extract system messages (Anthropic doesn't accept them inline)
        system_parts = []
        non_system_messages = []
        for msg in request.messages:
            if msg.get("role") == "system":
                system_parts.append(msg.get("content", ""))
            else:
                non_system_messages.append(msg)

        body: dict[str, Any] = {
            "model": model.model_id,
            "messages": non_system_messages,
            "temperature": request.temperature or model.default_temperature,
            "max_tokens": request.max_tokens or model.max_output_tokens,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)

        # Convert OpenAI tool format to Anthropic format
        if request.tools:
            anthropic_tools = []
            for tool in request.tools:
                fn = tool.get("function", {})
                anthropic_tools.append(
                    {
                        "name": fn.get("name", ""),
                        "description": fn.get("description", ""),
                        "input_schema": fn.get("parameters", {}),
                    }
                )
            body["tools"] = anthropic_tools

        resp = await client.post(
            url,
            json=body,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            timeout=request.timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()

        # Parse content blocks
        content_text = None
        tool_calls = []
        for block in data.get("content", []):
            if block["type"] == "text":
                content_text = (content_text or "") + block.get("text", "")
            elif block["type"] == "tool_use":
                tool_calls.append(
                    ToolCallResponse(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=block.get("input", {}),
                    )
                )

        usage = data.get("usage", {})
        return CompletionResponse(
            content=content_text,
            tool_calls=tool_calls,
            usage=UsageInfo(
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
            ),
        )

    async def _call_azure_openai(
        self,
        provider: LLMProvider,
        model: ModelConfig,
        request: CompletionRequest,
        api_key: str,
    ) -> CompletionResponse:
        """
        Call the Azure OpenAI API.

        Differences from standard OpenAI:
          - URL: {base_url}/openai/deployments/{model_id}/chat/completions
          - Auth header: api-key (not Authorization: Bearer)
          - Query param: api-version=2024-02-01
          - Response format is identical to standard OpenAI
        """
        client = await self._get_client()
        url = (
            f"{provider.base_url.rstrip('/')}/openai/deployments/{model.model_id}/chat/completions"
        )

        body: dict[str, Any] = {
            "messages": request.messages,
            "temperature": request.temperature or model.default_temperature,
        }
        if request.tools:
            body["tools"] = request.tools
            if request.tool_choice is not None:
                body["tool_choice"] = request.tool_choice
        if request.max_tokens:
            body["max_tokens"] = request.max_tokens

        resp = await client.post(
            url,
            json=body,
            headers={
                "api-key": api_key,
                "Content-Type": "application/json",
            },
            params={"api-version": "2024-02-01"},
            timeout=request.timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()

        # Parse — same format as OpenAI
        choice = data["choices"][0]["message"]
        tool_calls = []
        if choice.get("tool_calls"):
            for tc in choice["tool_calls"]:
                args = tc["function"].get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                tool_calls.append(
                    ToolCallResponse(
                        id=tc.get("id", ""),
                        name=tc["function"]["name"],
                        arguments=args,
                    )
                )

        usage = data.get("usage", {})
        return CompletionResponse(
            content=choice.get("content"),
            tool_calls=tool_calls,
            usage=UsageInfo(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            ),
        )

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------

    def _make_cache_key(self, request: CompletionRequest) -> str:
        """Generate a deterministic cache key from the request."""
        key_data = {
            "messages": str(request.messages),
            "tools": str(request.tools),
            "model": request.model_preference or "default",
            "temperature": request.temperature,
        }
        raw = str(key_data).encode()
        return f"llm:cache:{hashlib.sha256(raw).hexdigest()}"

    async def _check_cache(self, request: CompletionRequest) -> CompletionResponse | None:
        """Check for a cached response."""
        if self._cache is None:
            return None
        key = self._make_cache_key(request)
        data = await self._cache.get(key)
        if data is None:
            return None
        if isinstance(data, bytes):
            data = data.decode()
        response = CompletionResponse.model_validate_json(data)
        response.cached = True
        logger.debug("Cache hit for key %s", key[:24])
        return response

    async def _store_cache(self, request: CompletionRequest, response: CompletionResponse) -> None:
        """Store a response in the cache."""
        if self._cache is None:
            return
        key = self._make_cache_key(request)
        await self._cache.setex(key, self.CACHE_TTL_SECONDS, response.model_dump_json())
        logger.debug("Cached response for key %s", key[:24])

    # ------------------------------------------------------------------
    # Secret management
    # ------------------------------------------------------------------

    async def _get_api_key(self, ref: str) -> str:
        """
        Resolve an API key reference.

        If a secret store is configured, delegate to it.
        Otherwise, treat the ref as an environment variable name.
        """
        if self._secrets is not None:
            result: str = await self._secrets.get(ref)
            return result
        return str(os.environ.get(ref, ref))

    # ------------------------------------------------------------------
    # Admin / status
    # ------------------------------------------------------------------

    def get_provider_status(self) -> list[dict[str, Any]]:
        """Get status of all providers (for monitoring/admin)."""
        return [
            {
                "provider_id": p.provider_id,
                "provider_type": p.provider_type,
                "enabled": p.enabled,
                "circuit_breaker_state": (self._circuit_breakers[p.provider_id].state.value),
                "models": [m.model_id for m in p.models],
                "priority": p.priority,
            }
            for p in self._providers
        ]
