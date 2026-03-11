"""
Tests for the FastAPI API (Phase 8).

Covers:
  - Health endpoint (GET /api/v1/health)
  - Tool registration (POST /api/v1/tools)
  - Tool listing and filtering (GET /api/v1/tools)
  - Tool detail (GET /api/v1/tools/{tool_id})
  - Tool deletion (DELETE /api/v1/tools/{tool_id})
  - Agent run start (POST /api/v1/runs — engine not configured)
  - Run status (GET /api/v1/runs/{run_id})
  - Admin endpoints (GET /api/v1/admin/config, /admin/providers)
  - Middleware (request ID, response time)
  - Exception handlers (404, 500)
  - Dependency injection overrides

Uses FastAPI TestClient for synchronous testing — no real HTTP server needed.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from agentworks.api import _app_state, app
from agentworks.config import (
    APIRateLimitConfig,
    AuthConfig,
    CORSConfig,
    RuntimeConfig,
    SecurityConfig,
)
from agentworks.tool_registry import ToolRegistry

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def make_tool_payload(
    tool_id: str = "test_search",
    version: str = "1.0.0",
    **overrides: object,
) -> dict:
    """Create a valid tool registration payload."""
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
    return defaults


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def fresh_state():
    """Reset app state before each test to ensure isolation."""
    original_config = _app_state.config
    original_registry = _app_state.tool_registry
    original_engine = _app_state.engine
    original_obs = _app_state.observability

    _app_state.config = RuntimeConfig()
    _app_state.tool_registry = ToolRegistry()
    _app_state.engine = None
    _app_state.checkpoint_mgr = None
    _app_state.llm_gateway = None

    yield _app_state

    _app_state.config = original_config
    _app_state.tool_registry = original_registry
    _app_state.engine = original_engine
    _app_state.observability = original_obs


@pytest.fixture
def client(fresh_state):
    """Test client with fresh state. Bypasses lifespan to avoid OTel setup."""
    return TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------
# Health endpoint
# --------------------------------------------------------------------------


class TestHealth:
    """Tests for GET /api/v1/health."""

    def test_health_degraded_without_engine(self, client: TestClient):
        """Without an engine, status should be 'degraded'."""
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["version"] == "1.0.0"
        assert "uptime_seconds" in body
        assert body["checks"]["runtime"] == "ok"
        assert body["checks"]["engine"] == "not_configured"

    def test_health_includes_tool_count(self, client: TestClient, fresh_state):
        """Health check reports the number of registered tools."""
        resp = client.get("/api/v1/health")
        body = resp.json()
        assert body["checks"]["tools"] == "0 registered"


# --------------------------------------------------------------------------
# Tool registration
# --------------------------------------------------------------------------


class TestToolRegistration:
    """Tests for POST /api/v1/tools."""

    def test_register_tool_success(self, client: TestClient):
        """Register a valid tool and get 201 with tool details."""
        payload = make_tool_payload()
        resp = client.post("/api/v1/tools", json=payload)
        assert resp.status_code == 201
        body = resp.json()
        assert body["tool_id"] == "test_search"
        assert body["version"] == "1.0.0"
        assert body["status"] == "active"
        assert "schema_hash" in body
        assert "registered_at" in body

    def test_register_tool_invalid_schema(self, client: TestClient):
        """Invalid JSON Schema in input_schema should return 422."""
        payload = make_tool_payload()
        payload["input_schema"] = {"type": "not_a_type"}
        resp = client.post("/api/v1/tools", json=payload)
        assert resp.status_code == 422

    def test_register_tool_missing_required(self, client: TestClient):
        """Missing required fields should return 422."""
        resp = client.post("/api/v1/tools", json={"tool_id": "x"})
        assert resp.status_code == 422

    def test_register_duplicate_same_version(self, client: TestClient):
        """Re-registering with the same version should succeed (idempotent)."""
        payload = make_tool_payload()
        resp1 = client.post("/api/v1/tools", json=payload)
        assert resp1.status_code == 201
        resp2 = client.post("/api/v1/tools", json=payload)
        assert resp2.status_code == 201

    def test_register_upgrade_version(self, client: TestClient):
        """Upgrading to a newer version should succeed."""
        client.post("/api/v1/tools", json=make_tool_payload(version="1.0.0"))
        resp = client.post("/api/v1/tools", json=make_tool_payload(version="2.0.0"))
        assert resp.status_code == 201
        assert resp.json()["version"] == "2.0.0"


# --------------------------------------------------------------------------
# Tool listing
# --------------------------------------------------------------------------


class TestToolListing:
    """Tests for GET /api/v1/tools."""

    def test_list_empty(self, client: TestClient):
        """No tools registered returns empty list."""
        resp = client.get("/api/v1/tools")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_after_registration(self, client: TestClient):
        """After registering a tool, it appears in the listing."""
        client.post("/api/v1/tools", json=make_tool_payload())
        resp = client.get("/api/v1/tools")
        assert resp.status_code == 200
        tools = resp.json()
        assert len(tools) == 1
        assert tools[0]["tool_id"] == "test_search"
        assert tools[0]["name"] == "Test Search Tool"
        assert tools[0]["status"] == "active"
        assert tools[0]["owner_team"] == "test-team"
        assert tools[0]["tags"] == ["search", "knowledge"]

    def test_list_multiple_tools(self, client: TestClient):
        """Multiple tools all appear in the listing."""
        client.post("/api/v1/tools", json=make_tool_payload("tool_alpha"))
        client.post("/api/v1/tools", json=make_tool_payload("tool_beta"))
        resp = client.get("/api/v1/tools")
        assert len(resp.json()) == 2


# --------------------------------------------------------------------------
# Tool detail
# --------------------------------------------------------------------------


class TestToolDetail:
    """Tests for GET /api/v1/tools/{tool_id}."""

    def test_get_registered_tool(self, client: TestClient):
        """Get details of a registered tool."""
        client.post("/api/v1/tools", json=make_tool_payload())
        resp = client.get("/api/v1/tools/test_search")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "active"
        assert "definition" in body
        assert body["definition"]["tool_id"] == "test_search"
        assert "stats" in body
        assert body["stats"]["total_calls"] == 0

    def test_get_nonexistent_tool(self, client: TestClient):
        """Getting a nonexistent tool returns 404."""
        resp = client.get("/api/v1/tools/no_such_tool")
        assert resp.status_code == 404


# --------------------------------------------------------------------------
# Tool deletion
# --------------------------------------------------------------------------


class TestToolDeletion:
    """Tests for DELETE /api/v1/tools/{tool_id}."""

    def test_delete_registered_tool(self, client: TestClient):
        """Deleting a registered tool returns 204."""
        client.post("/api/v1/tools", json=make_tool_payload())
        resp = client.delete("/api/v1/tools/test_search")
        assert resp.status_code == 204

        # Verify it's gone
        resp = client.get("/api/v1/tools/test_search")
        assert resp.status_code == 404

    def test_delete_nonexistent_tool(self, client: TestClient):
        """Deleting a nonexistent tool returns 404."""
        resp = client.delete("/api/v1/tools/no_such_tool")
        assert resp.status_code == 404


# --------------------------------------------------------------------------
# Agent runs
# --------------------------------------------------------------------------


class TestRuns:
    """Tests for /api/v1/runs endpoints."""

    def test_start_run_without_engine(self, client: TestClient):
        """Starting a run without an engine returns 503."""
        payload = {
            "message": "What is the weather?",
            "agent_id": "test-agent",
        }
        resp = client.post("/api/v1/runs", json=payload)
        assert resp.status_code == 503
        body = resp.json()
        assert "not configured" in body["error"].lower()

    def test_start_run_validation(self, client: TestClient):
        """Missing required fields returns 422."""
        resp = client.post("/api/v1/runs", json={})
        assert resp.status_code == 422

    def test_start_run_empty_message(self, client: TestClient):
        """Empty message string returns 422."""
        resp = client.post(
            "/api/v1/runs",
            json={"message": "", "agent_id": "test"},
        )
        assert resp.status_code == 422

    def test_get_run_without_checkpoint(self, client: TestClient):
        """Getting a run without checkpoint store returns 503."""
        resp = client.get("/api/v1/runs/some-run-id")
        assert resp.status_code == 503

    def test_resume_run_without_engine(self, client: TestClient):
        """Resuming without an engine returns 503."""
        resp = client.post("/api/v1/runs/some-run-id/resume")
        assert resp.status_code == 503


# --------------------------------------------------------------------------
# Run response enrichment (Phase 0 — Dashboard support)
# --------------------------------------------------------------------------


class TestRunResponseEnrichment:
    """Tests for state_history, token_usage, and timestamps in RunResponse."""

    def test_ctx_to_response_includes_state_history(self):
        """RunResponse includes the full state_history from ExecutionContext."""
        from agentworks.api import _ctx_to_response
        from agentworks.state_machine import ExecutionContext

        ctx = ExecutionContext(agent_id="test-agent")
        ctx.state_history = [
            {
                "from": "idle",
                "to": "planning",
                "trigger": "start",
                "timestamp": "2024-01-01T00:00:00Z",
            },
            {
                "from": "planning",
                "to": "completed",
                "trigger": "has_answer",
                "timestamp": "2024-01-01T00:00:01Z",
            },
        ]
        resp = _ctx_to_response(ctx)
        assert len(resp.state_history) == 2
        assert resp.state_history[0]["from"] == "idle"
        assert resp.state_history[1]["trigger"] == "has_answer"

    def test_ctx_to_response_includes_token_usage(self):
        """RunResponse includes token_usage breakdown."""
        from agentworks.api import _ctx_to_response
        from agentworks.state_machine import ExecutionContext, TokenUsage

        ctx = ExecutionContext(agent_id="test-agent")
        ctx.token_usage = TokenUsage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            estimated_cost_usd=0.002,
        )
        resp = _ctx_to_response(ctx)
        assert resp.token_usage["prompt_tokens"] == 100
        assert resp.token_usage["completion_tokens"] == 50
        assert resp.token_usage["total_tokens"] == 150
        assert resp.token_usage["estimated_cost_usd"] == 0.002

    def test_ctx_to_response_includes_message_timestamps(self):
        """Messages in RunResponse include timestamp (not excluded)."""
        from agentworks.api import _ctx_to_response
        from agentworks.state_machine import ExecutionContext, Message

        ctx = ExecutionContext(agent_id="test-agent")
        ctx.messages.append(Message(role="user", content="Hello"))
        resp = _ctx_to_response(ctx)
        assert len(resp.messages) == 1
        assert "timestamp" in resp.messages[0]

    def test_list_runs_without_checkpoint_returns_empty(self, client: TestClient):
        """Listing runs without checkpoint store returns empty list."""
        resp = client.get("/api/v1/runs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_runs_with_mock_checkpoint(self, client: TestClient, fresh_state):
        """Listing runs with a mock checkpoint manager returns run items."""
        from agentworks.state_machine import AgentState, ExecutionContext

        ctx = ExecutionContext(
            run_id="run-123",
            agent_id="agent-a",
            team_id="team-1",
            current_state=AgentState.COMPLETED,
            iteration_count=3,
        )
        ctx.token_usage.prompt_tokens = 100
        ctx.token_usage.completion_tokens = 50
        ctx.token_usage.total_tokens = 150
        ctx.token_usage.estimated_cost_usd = 0.003

        mock_mgr = AsyncMock()
        mock_mgr.list_active_runs = AsyncMock(return_value=["run-123"])
        mock_mgr.restore = AsyncMock(return_value=ctx.model_dump(mode="json"))
        mock_mgr._cold = None
        fresh_state.checkpoint_mgr = mock_mgr

        resp = client.get("/api/v1/runs")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["run_id"] == "run-123"
        assert items[0]["agent_id"] == "agent-a"
        assert items[0]["outcome"] == "completed"
        assert items[0]["iteration_count"] == 3

    def test_list_runs_filter_by_agent_id(self, client: TestClient, fresh_state):
        """Listing runs filters by agent_id query parameter."""
        from agentworks.state_machine import ExecutionContext

        ctx1 = ExecutionContext(run_id="run-1", agent_id="agent-a")
        ctx2 = ExecutionContext(run_id="run-2", agent_id="agent-b")

        async def mock_restore(run_id):
            return {"run-1": ctx1, "run-2": ctx2}[run_id].model_dump(mode="json")

        mock_mgr = AsyncMock()
        mock_mgr.list_active_runs = AsyncMock(return_value=["run-1", "run-2"])
        mock_mgr.restore = AsyncMock(side_effect=mock_restore)
        mock_mgr._cold = None
        fresh_state.checkpoint_mgr = mock_mgr

        resp = client.get("/api/v1/runs?agent_id=agent-a")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["agent_id"] == "agent-a"

    def test_list_runs_pagination(self, client: TestClient, fresh_state):
        """Listing runs respects limit and offset parameters."""
        from agentworks.state_machine import ExecutionContext

        contexts = {}
        run_ids = []
        for i in range(5):
            rid = f"run-{i}"
            run_ids.append(rid)
            contexts[rid] = ExecutionContext(run_id=rid, agent_id="agent-a")

        async def mock_restore(run_id):
            return contexts[run_id].model_dump(mode="json")

        mock_mgr = AsyncMock()
        mock_mgr.list_active_runs = AsyncMock(return_value=run_ids)
        mock_mgr.restore = AsyncMock(side_effect=mock_restore)
        mock_mgr._cold = None
        fresh_state.checkpoint_mgr = mock_mgr

        resp = client.get("/api/v1/runs?limit=2&offset=1")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 2


# --------------------------------------------------------------------------
# Admin endpoints
# --------------------------------------------------------------------------


class TestAdmin:
    """Tests for /api/v1/admin/* endpoints."""

    def test_get_config_returns_defaults(self, client: TestClient):
        """Admin config endpoint returns the current runtime config."""
        resp = client.get("/api/v1/admin/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["port"] == 8000
        assert body["redis"]["host"] == "localhost"
        assert body["observability"]["service_name"] == "agentworks"

    def test_config_redacts_secrets(self, client: TestClient, fresh_state):
        """Secrets in config are redacted in the response."""
        fresh_state.config = RuntimeConfig(
            redis={"host": "localhost", "password_ref": "my-secret"},
            postgres={"host": "localhost", "password_ref": "pg-secret"},
        )
        resp = client.get("/api/v1/admin/config")
        body = resp.json()
        assert body["redis"]["password_ref"] == "***"
        assert body["postgres"]["password_ref"] == "***"

    def test_providers_empty(self, client: TestClient):
        """Without LLM gateway, providers returns empty list."""
        resp = client.get("/api/v1/admin/providers")
        assert resp.status_code == 200
        assert resp.json() == []


# --------------------------------------------------------------------------
# Middleware
# --------------------------------------------------------------------------


class TestMiddleware:
    """Tests for request context middleware."""

    def test_response_includes_request_id(self, client: TestClient):
        """Every response has an X-Request-ID header."""
        resp = client.get("/api/v1/health")
        assert "X-Request-ID" in resp.headers

    def test_custom_request_id_preserved(self, client: TestClient):
        """Client-supplied X-Request-ID is echoed back."""
        custom_id = "my-custom-request-123"
        resp = client.get(
            "/api/v1/health",
            headers={"X-Request-ID": custom_id},
        )
        assert resp.headers["X-Request-ID"] == custom_id

    def test_response_includes_timing(self, client: TestClient):
        """Every response has an X-Response-Time header."""
        resp = client.get("/api/v1/health")
        assert "X-Response-Time" in resp.headers
        assert resp.headers["X-Response-Time"].endswith("ms")


# --------------------------------------------------------------------------
# Exception handlers
# --------------------------------------------------------------------------


class TestExceptionHandlers:
    """Tests for structured error responses."""

    def test_404_structured_response(self, client: TestClient):
        """404 errors return structured JSON with error field."""
        resp = client.get("/api/v1/tools/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert "error" in body

    def test_422_for_invalid_input(self, client: TestClient):
        """Validation errors return 422."""
        resp = client.post("/api/v1/runs", json={"bad": "data"})
        assert resp.status_code == 422


# --------------------------------------------------------------------------
# Tool health endpoint
# --------------------------------------------------------------------------


class TestToolHealth:
    """Tests for GET /api/v1/health/tools."""

    def test_tool_health_empty(self, client: TestClient):
        """No tools means empty health check list."""
        resp = client.get("/api/v1/health/tools")
        assert resp.status_code == 200
        assert resp.json() == []


# --------------------------------------------------------------------------
# Authentication (Phase 9)
# --------------------------------------------------------------------------


class TestAuthentication:
    """Tests for auth middleware."""

    def test_401_missing_key(self, client: TestClient, fresh_state):
        """With auth enabled but no key, should return 401."""
        fresh_state.config = RuntimeConfig(
            auth=AuthConfig(enabled=True, api_keys=["valid-key"]),
        )
        resp = client.get("/api/v1/tools")
        assert resp.status_code == 401
        assert "Missing API key" in resp.json()["error"]

    def test_403_invalid_key(self, client: TestClient, fresh_state):
        """With wrong key, should return 403."""
        fresh_state.config = RuntimeConfig(
            auth=AuthConfig(enabled=True, api_keys=["valid-key"]),
        )
        resp = client.get(
            "/api/v1/tools",
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 403
        assert "Invalid API key" in resp.json()["error"]

    def test_200_valid_key(self, client: TestClient, fresh_state):
        """With valid key, request proceeds normally."""
        fresh_state.config = RuntimeConfig(
            auth=AuthConfig(enabled=True, api_keys=["valid-key"]),
        )
        resp = client.get(
            "/api/v1/tools",
            headers={"X-API-Key": "valid-key"},
        )
        assert resp.status_code == 200

    def test_health_exempt_from_auth(self, client: TestClient, fresh_state):
        """Health endpoints are exempt from authentication (k8s probes)."""
        fresh_state.config = RuntimeConfig(
            auth=AuthConfig(enabled=True, api_keys=["valid-key"]),
        )
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200

    def test_disabled_by_default(self, client: TestClient):
        """Auth is disabled by default — all requests pass."""
        resp = client.get("/api/v1/tools")
        assert resp.status_code == 200


# --------------------------------------------------------------------------
# Rate Limiting (Phase 9)
# --------------------------------------------------------------------------


class TestRateLimiting:
    """Tests for rate limit middleware."""

    def test_disabled_by_default(self, client: TestClient):
        """Rate limiting is disabled by default."""
        for _ in range(10):
            resp = client.get("/api/v1/health")
            assert resp.status_code == 200

    def test_429_when_exceeded(self, client: TestClient, fresh_state):
        """When rate limit is exceeded, returns 429."""
        import agentworks.api as api_module

        api_module._rate_limit_buckets.clear()

        fresh_state.config = RuntimeConfig(
            rate_limit=APIRateLimitConfig(
                enabled=True,
                requests_per_minute=60,
                burst_size=2,
                per_key=False,
            ),
        )
        # First 2 requests (burst) succeed
        resp1 = client.get("/api/v1/health")
        resp2 = client.get("/api/v1/health")
        assert resp1.status_code == 200
        assert resp2.status_code == 200

        # 3rd request exceeds burst
        resp3 = client.get("/api/v1/health")
        assert resp3.status_code == 429
        assert "Rate limit" in resp3.json()["error"]


# --------------------------------------------------------------------------
# CORS (Phase 9)
# --------------------------------------------------------------------------


class TestCORS:
    """Tests for CORS middleware."""

    def test_allows_configured_origins(self, client: TestClient, fresh_state):
        """Configured origins get Access-Control-Allow-Origin header."""
        fresh_state.config = RuntimeConfig(
            cors=CORSConfig(allow_origins=["https://app.example.com"]),
        )
        resp = client.get(
            "/api/v1/health",
            headers={"Origin": "https://app.example.com"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("Access-Control-Allow-Origin") == "https://app.example.com"

    def test_blocks_unconfigured_origin(self, client: TestClient, fresh_state):
        """Origins not in the allow list don't get CORS headers."""
        fresh_state.config = RuntimeConfig(
            cors=CORSConfig(allow_origins=["https://app.example.com"]),
        )
        resp = client.get(
            "/api/v1/health",
            headers={"Origin": "https://evil.com"},
        )
        assert resp.status_code == 200
        assert "Access-Control-Allow-Origin" not in resp.headers


# --------------------------------------------------------------------------
# Body Size (Phase 9)
# --------------------------------------------------------------------------


class TestBodySize:
    """Tests for body size middleware."""

    def test_413_for_oversized_body(self, client: TestClient, fresh_state):
        """Request exceeding max body size returns 413."""
        fresh_state.config = RuntimeConfig(
            security=SecurityConfig(max_request_body_bytes=1024),
        )
        # Advertise a Content-Length larger than the 1024 limit
        resp = client.post(
            "/api/v1/runs",
            json={"message": "x" * 2000, "agent_id": "test"},
            headers={"Content-Length": "5000"},
        )
        assert resp.status_code == 413
        assert "too large" in resp.json()["error"]


# --------------------------------------------------------------------------
# Lifespan wiring (Phase 9)
# --------------------------------------------------------------------------


class TestLifespanWiring:
    """Tests for lifespan component wiring."""

    def test_engine_wired_with_mocks(self, fresh_state):
        """When engine, checkpoint_mgr are manually set, health shows ok."""
        fresh_state.engine = MagicMock()
        fresh_state.checkpoint_mgr = MagicMock()
        fresh_state.llm_gateway = MagicMock()
        fresh_state.llm_gateway.get_provider_status.return_value = []

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/health")
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"]["engine"] == "ok"

    def test_graceful_without_redis(self, client: TestClient):
        """Without Redis/Postgres, engine is None and health is degraded."""
        resp = client.get("/api/v1/health")
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["checks"]["engine"] == "not_configured"

    def test_resolve_secret_from_env(self, monkeypatch):
        """_resolve_secret reads env: prefixed refs."""
        from agentworks.api import _resolve_secret

        monkeypatch.setenv("MY_SECRET", "hunter2")
        assert _resolve_secret("env:MY_SECRET") == "hunter2"
        assert _resolve_secret("plain-value") == "plain-value"
        assert _resolve_secret("") == ""


# --------------------------------------------------------------------------
# Deep Health Check (Phase 9)
# --------------------------------------------------------------------------


class TestDeepHealthCheck:
    """Tests for deep health check and k8s probes."""

    def test_health_includes_redis_status(self, client: TestClient):
        """Health check reports redis status."""
        resp = client.get("/api/v1/health")
        body = resp.json()
        assert "redis" in body["checks"]

    def test_health_includes_postgres_status(self, client: TestClient):
        """Health check reports postgres status."""
        resp = client.get("/api/v1/health")
        body = resp.json()
        assert "postgres" in body["checks"]

    def test_liveness_always_200(self, client: TestClient):
        """Liveness probe returns 200 regardless of engine state."""
        resp = client.get("/api/v1/health/live")
        assert resp.status_code == 200
        assert resp.json()["status"] == "alive"

    def test_readiness_503_without_engine(self, client: TestClient):
        """Readiness probe returns 503 without engine."""
        resp = client.get("/api/v1/health/ready")
        assert resp.status_code == 503
        assert resp.json()["status"] == "not_ready"

    def test_readiness_200_with_engine(self, fresh_state):
        """Readiness probe returns 200 when engine is wired."""
        fresh_state.engine = MagicMock()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/health/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"


# --------------------------------------------------------------------------
# Graceful Shutdown (Phase 9)
# --------------------------------------------------------------------------


class TestGracefulShutdown:
    """Tests for graceful shutdown behavior."""

    def test_shutdown_rejects_new_runs(self, client: TestClient, fresh_state):
        """When shutting down, new runs are rejected with 503."""
        fresh_state._shutting_down = True
        resp = client.post(
            "/api/v1/runs",
            json={"message": "test", "agent_id": "agent-1"},
        )
        assert resp.status_code == 503
        assert "shutting down" in resp.json()["error"].lower()


# --------------------------------------------------------------------------
# Tool scoping (tool_ids parameter)
# --------------------------------------------------------------------------


class TestToolIds:
    """Tests for the tool_ids parameter on run requests."""

    def test_run_request_accepts_tool_ids(self, client: TestClient):
        """RunRequest accepts an optional tool_ids list."""
        from agentworks.api import RunRequest

        req = RunRequest(
            message="test task",
            agent_id="test-agent",
            tool_ids=["web_search", "read_file"],
        )
        assert req.tool_ids == ["web_search", "read_file"]

    def test_run_request_tool_ids_default_none(self, client: TestClient):
        """tool_ids defaults to None (all tools available)."""
        from agentworks.api import RunRequest

        req = RunRequest(message="test task", agent_id="test-agent")
        assert req.tool_ids is None

    def test_run_request_tool_ids_empty_list(self, client: TestClient):
        """An empty tool_ids list is valid (agent has no tools)."""
        from agentworks.api import RunRequest

        req = RunRequest(
            message="test task",
            agent_id="test-agent",
            tool_ids=[],
        )
        assert req.tool_ids == []

    def test_tool_ids_passed_to_context(self):
        """tool_ids from RunRequest is stored in ExecutionContext."""
        from agentworks.state_machine import ExecutionContext

        ctx = ExecutionContext(
            agent_id="test",
            tool_ids=["search", "write"],
        )
        assert ctx.tool_ids == ["search", "write"]

    def test_tool_ids_default_none_in_context(self):
        """ExecutionContext.tool_ids defaults to None."""
        from agentworks.state_machine import ExecutionContext

        ctx = ExecutionContext(agent_id="test")
        assert ctx.tool_ids is None

    def test_tool_ids_survives_serialization(self):
        """tool_ids is preserved through JSON serialization (checkpointing)."""
        from agentworks.state_machine import ExecutionContext

        ctx = ExecutionContext(
            agent_id="test",
            tool_ids=["search", "billing"],
        )
        data = ctx.model_dump(mode="json")
        restored = ExecutionContext.model_validate(data)
        assert restored.tool_ids == ["search", "billing"]

    def test_tool_ids_none_survives_serialization(self):
        """tool_ids=None is preserved through serialization."""
        from agentworks.state_machine import ExecutionContext

        ctx = ExecutionContext(agent_id="test")
        data = ctx.model_dump(mode="json")
        restored = ExecutionContext.model_validate(data)
        assert restored.tool_ids is None
