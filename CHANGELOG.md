# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-03-10

Initial open-source release.

### Added

- **State Machine** — 8-state deterministic lifecycle for agent runs (IDLE, PLANNING, AWAITING_LLM, EXECUTING_TOOL, REFLECTING, COMPLETED, FAILED, SUSPENDED) with 17 transitions, guards, and side effects
- **Execution Engine** — Orchestrates runs through the state machine with 3-tier error handling (retryable, recoverable, fatal), budget guards, and iteration limits
- **Tool Registry** — Self-service tool registration with JSON Schema validation, semver versioning, token-bucket rate limiting, and SSRF protection
- **LLM Gateway** — Multi-provider routing (OpenAI, Anthropic, Azure OpenAI) with per-provider circuit breakers, capability-based selection, response caching, and cost tracking
- **Checkpoint Manager** — Dual-store persistence: Redis for active runs (~0.8ms writes), PostgreSQL for completed runs, with automatic promotion on terminal states
- **Memory Manager** — Token-aware sliding window with vector similarity recall for long conversations
- **Observability** — Full OpenTelemetry integration: distributed traces, Prometheus-compatible metrics, structured JSON logging, per-run cost attribution
- **FastAPI Application** — REST API with authentication (API key with timing-safe comparison), per-key rate limiting, CORS, request body size limits, and production config validation
- **Observability Dashboard** — React 19 SPA with 5 views: Run Timeline, State Machine Visualizer, Cost Dashboard, Conversation Thread, and Run Comparison
- **Error Classifier** — Pattern-matching error taxonomy with configurable retry strategies and recovery hints
- **Docker Support** — Multi-stage Dockerfile (~120MB image), docker-compose.yml with Redis and PostgreSQL, health checks, non-root user
- **Configuration** — YAML-driven config with Pydantic v2 validation, environment variable secret references, and production safety checks
- **389 tests** — Unit, integration, and property-based tests (Hypothesis) with in-memory fakes (no infrastructure required)
