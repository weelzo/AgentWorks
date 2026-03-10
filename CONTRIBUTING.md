# Contributing to AgentWorks

Thank you for your interest in contributing! This guide will help you get started.

## Development Setup

**Prerequisites:** Python 3.12+, [uv](https://docs.astral.sh/uv/)

```bash
# Clone the repository
git clone https://github.com/your-org/agentworks.git
cd agentworks

# Install dependencies (including dev tools)
uv sync --all-extras

# Verify everything works
uv run pytest tests/ -v
```

All 389 tests should pass in under 2 seconds.

## Project Structure

```
src/agentworks/
  state_machine.py   # Custom state machine (IDLE → PLANNING → EXECUTING → ...)
  tool_registry.py   # Self-service tool registration with rate limiting & SSRF protection
  engine.py          # Execution engine — orchestrates runs through the state machine
  llm_gateway.py     # Multi-provider LLM abstraction (OpenAI, Anthropic)
  memory.py          # Token-aware conversation memory management
  checkpoint.py      # Dual-store checkpointing (Redis hot / Postgres cold)
  config.py          # Pydantic v2 configuration with production validation
  api.py             # FastAPI application with security middleware
  observability.py   # OpenTelemetry tracing, metrics, structured logging
  errors.py          # Error taxonomy
```

## Running Tests

```bash
# Full test suite
uv run pytest tests/ -v

# Single module
uv run pytest tests/test_engine.py -v

# With coverage
uv run pytest tests/ --cov=agentworks --cov-report=term-missing
```

Tests use in-memory fakes (no Redis/Postgres required). Integration tests in
`tests/test_integration.py` wire all components together with fake stores.

## Code Quality

We use strict tooling. Run these before submitting a PR:

```bash
# Linting
uv run ruff check src/ tests/

# Auto-fix lint issues
uv run ruff check src/ tests/ --fix

# Type checking
uv run mypy src/

# Format check
uv run ruff format --check src/ tests/
```

CI runs all of these automatically on every PR.

## Making Changes

1. **Fork and branch.** Create a feature branch from `main`.
2. **Write tests first.** Every behavioral change needs a test. We use `pytest-asyncio` for async tests and `hypothesis` for property-based testing of the state machine.
3. **Keep changes focused.** One logical change per PR. If you're fixing a bug and notice an unrelated issue, open a separate PR.
4. **Follow existing patterns.** The codebase is consistent — match the style you see.

## Pull Request Process

1. Ensure all tests pass and linting is clean.
2. Update docstrings if you changed public APIs.
3. Write a clear PR description explaining *what* and *why*.
4. Link any related issues.

## Architecture Decisions

Before making significant architectural changes, please open an issue to discuss
the approach. The [architecture document](ARCHITECTURE.md) explains
the reasoning behind key design decisions.

## Good First Issues

Look for issues labeled [`good first issue`](../../labels/good%20first%20issue) —
these are scoped, well-documented tasks ideal for new contributors.

## Reporting Bugs

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md) and include:
- Steps to reproduce
- Expected vs. actual behavior
- Python version and OS
- Relevant configuration (redact secrets)

## Code of Conduct

Be respectful, constructive, and inclusive. We're all here to build something useful.
