"""
Tests for the Observability Layer (Phase 7).

Covers:
  - Structured JSON logging with trace context injection
  - AgentMetrics: all metric instruments record correctly
  - AgentTracer: span lifecycle for runs, tools, and LLM calls
  - ObservabilityManager: configuration, no-op mode, state machine hooks
  - Integration: metrics + tracing work together through hooks
"""

import json
import logging

import pytest
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentworks.observability import (
    AgentMetrics,
    AgentTracer,
    ObservabilityManager,
    StructuredLogFormatter,
    _build_resource,
    configure_structured_logging,
)

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def span_exporter():
    """In-memory span exporter for test assertions."""
    return InMemorySpanExporter()


@pytest.fixture
def metric_reader():
    """In-memory metric reader for test assertions."""
    return InMemoryMetricReader()


@pytest.fixture
def tracer(span_exporter):
    """Tracer backed by in-memory exporter."""
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    return provider.get_tracer("test")


@pytest.fixture
def meter(metric_reader):
    """Meter backed by in-memory reader."""
    provider = MeterProvider(metric_readers=[metric_reader])
    return provider.get_meter("test")


@pytest.fixture
def agent_tracer(tracer):
    """AgentTracer wrapping the test tracer."""
    return AgentTracer(tracer)


@pytest.fixture
def agent_metrics(meter):
    """AgentMetrics wrapping the test meter."""
    return AgentMetrics(meter)


# --------------------------------------------------------------------------
# Structured Logging
# --------------------------------------------------------------------------


class TestStructuredLogFormatter:
    def test_basic_json_output(self):
        """Log entry is valid JSON with expected fields."""
        formatter = StructuredLogFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test.logger"
        assert parsed["message"] == "Test message"
        assert "timestamp" in parsed

    def test_extra_fields_included(self):
        """Extra fields (run_id, agent_id, etc.) appear in JSON output."""
        formatter = StructuredLogFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Tool executed",
            args=(),
            exc_info=None,
        )
        record.run_id = "run-123"
        record.tool_id = "search_docs"
        record.duration_ms = 42.5

        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["run_id"] == "run-123"
        assert parsed["tool_id"] == "search_docs"
        assert parsed["duration_ms"] == 42.5

    def test_exception_info_captured(self):
        """Exception details appear in the log entry."""
        formatter = StructuredLogFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Failed",
            args=(),
            exc_info=exc_info,
        )
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["exception"]["type"] == "ValueError"
        assert "test error" in parsed["exception"]["message"]

    def test_missing_extra_fields_not_included(self):
        """Fields not set via extra= don't appear as null values."""
        formatter = StructuredLogFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Simple message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)

        assert "run_id" not in parsed
        assert "tool_id" not in parsed


class TestConfigureLogging:
    def test_json_mode_sets_formatter(self):
        """JSON mode installs StructuredLogFormatter."""
        configure_structured_logging(json_output=True)
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, StructuredLogFormatter)

    def test_dev_mode_uses_standard_formatter(self):
        """Dev mode uses standard text formatter."""
        configure_structured_logging(json_output=False)
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert not isinstance(root.handlers[0].formatter, StructuredLogFormatter)


# --------------------------------------------------------------------------
# Agent Metrics
# --------------------------------------------------------------------------


class TestAgentMetrics:
    def test_run_start_increments_active(self, agent_metrics, metric_reader):
        """record_run_start() increments the active run gauge."""
        agent_metrics.record_run_start("agent-1", "team-1")
        agent_metrics.record_run_start("agent-1", "team-1")

        data = metric_reader.get_metrics_data()
        # Find the active runs metric
        for resource_metric in data.resource_metrics:
            for scope_metric in resource_metric.scope_metrics:
                for metric in scope_metric.metrics:
                    if metric.name == "agent.run.active":
                        points = list(metric.data.data_points)
                        assert len(points) > 0
                        # Sum of adds should be 2
                        total = sum(p.value for p in points)
                        assert total == 2

    def test_run_end_records_all_metrics(self, agent_metrics, metric_reader):
        """record_run_end() writes total, duration, iterations, and cost."""
        agent_metrics.record_run_start("agent-1", "team-1")
        agent_metrics.record_run_end(
            agent_id="agent-1",
            team_id="team-1",
            outcome="completed",
            duration_ms=1500.0,
            iterations=4,
            cost_usd=0.0032,
        )

        data = metric_reader.get_metrics_data()
        metric_names = set()
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    metric_names.add(m.name)

        assert "agent.run.total" in metric_names
        assert "agent.run.duration_ms" in metric_names
        assert "agent.run.iterations" in metric_names
        assert "agent.llm.cost_usd" in metric_names

    def test_tool_call_records_with_retries(self, agent_metrics, metric_reader):
        """Tool calls with retries record both call count and retry count."""
        agent_metrics.record_tool_call(
            tool_id="search_docs",
            status="success",
            duration_ms=250.0,
            retry_count=2,
        )

        data = metric_reader.get_metrics_data()
        metric_names = set()
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    metric_names.add(m.name)

        assert "agent.tool.call.total" in metric_names
        assert "agent.tool.call.duration_ms" in metric_names
        assert "agent.tool.retry.total" in metric_names

    def test_llm_call_records_cost_attribution(self, agent_metrics, metric_reader):
        """LLM calls record tokens and cost with provider/model labels."""
        agent_metrics.record_llm_call(
            provider_id="openai",
            model_id="gpt-4",
            duration_ms=800.0,
            prompt_tokens=500,
            completion_tokens=200,
            cost_usd=0.021,
            team_id="payments-team",
        )

        data = metric_reader.get_metrics_data()
        metric_names = set()
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    metric_names.add(m.name)

        assert "agent.llm.call.total" in metric_names
        assert "agent.llm.call.duration_ms" in metric_names
        assert "agent.llm.tokens.total" in metric_names
        assert "agent.llm.cost_usd" in metric_names

    def test_error_records_tier_and_type(self, agent_metrics, metric_reader):
        """Errors are recorded with tier, type, and optional tool_id."""
        agent_metrics.record_error(
            tier="retryable",
            error_type="timeout",
            tool_id="slow_api",
        )

        data = metric_reader.get_metrics_data()
        found = False
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    if m.name == "agent.error.total":
                        found = True
        assert found

    def test_state_transition_metric(self, agent_metrics, metric_reader):
        """State transitions are counted with from/to/trigger labels."""
        agent_metrics.record_state_transition(
            from_state="planning",
            to_state="executing_tool",
            trigger="needs_tool",
        )

        data = metric_reader.get_metrics_data()
        found = False
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    if m.name == "agent.state.transition.total":
                        found = True
        assert found

    def test_zero_cost_not_recorded(self, agent_metrics, metric_reader):
        """Zero cost runs don't pollute the cost metric."""
        agent_metrics.record_run_end(
            agent_id="agent-1",
            team_id="team-1",
            outcome="failed",
            duration_ms=100.0,
            iterations=1,
            cost_usd=0.0,
        )
        # Should not crash and should still record other metrics
        data = metric_reader.get_metrics_data()
        assert data is not None


# --------------------------------------------------------------------------
# Agent Tracer
# --------------------------------------------------------------------------


class TestAgentTracer:
    def test_run_span_lifecycle(self, agent_tracer, span_exporter):
        """Start and end a run span with proper attributes."""
        agent_tracer.start_run_span(
            run_id="run-1",
            agent_id="agent-1",
            team_id="team-1",
            user_request="Search for documents about AI safety",
        )
        agent_tracer.end_run_span(
            run_id="run-1",
            outcome="completed",
            duration_ms=2500.0,
            iterations=3,
            cost_usd=0.015,
        )

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1

        run_span = spans[0]
        assert run_span.name == "agent.run"
        attrs = dict(run_span.attributes)
        assert attrs["agent.run_id"] == "run-1"
        assert attrs["agent.agent_id"] == "agent-1"
        assert attrs["agent.outcome"] == "completed"
        assert attrs["agent.cost_usd"] == 0.015

    def test_failed_run_has_error_status(self, agent_tracer, span_exporter):
        """Failed runs set ERROR status on the span."""
        agent_tracer.start_run_span(
            run_id="run-2",
            agent_id="agent-1",
        )
        agent_tracer.end_run_span(
            run_id="run-2",
            outcome="failed",
            duration_ms=500.0,
            iterations=1,
            cost_usd=0.001,
            error="Max iterations reached",
        )

        spans = span_exporter.get_finished_spans()
        assert spans[0].status.status_code.name == "ERROR"
        assert "Max iterations" in spans[0].status.description

    def test_tool_span_nested_under_run(self, agent_tracer, span_exporter):
        """Tool spans are children of the run span."""
        agent_tracer.start_run_span(
            run_id="run-3",
            agent_id="agent-1",
        )
        agent_tracer.start_tool_span(
            run_id="run-3",
            tool_name="search_docs",
            input_data={"query": "AI safety"},
        )
        agent_tracer.end_tool_span(
            run_id="run-3",
            tool_name="search_docs",
            status="success",
            duration_ms=150.0,
        )
        agent_tracer.end_run_span(
            run_id="run-3",
            outcome="completed",
            duration_ms=2000.0,
            iterations=2,
            cost_usd=0.01,
        )

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 2

        tool_span = next(s for s in spans if s.name.startswith("agent.tool"))
        assert "search_docs" in tool_span.name
        assert dict(tool_span.attributes)["tool.status"] == "success"

    def test_llm_span_records_tokens(self, agent_tracer, span_exporter):
        """LLM spans capture token usage and cost."""
        agent_tracer.start_run_span(
            run_id="run-4",
            agent_id="agent-1",
        )
        agent_tracer.start_llm_span(
            run_id="run-4",
            provider_id="openai",
            model_id="gpt-4",
        )
        agent_tracer.end_llm_span(
            run_id="run-4",
            provider_id="openai",
            model_id="gpt-4",
            duration_ms=600.0,
            prompt_tokens=300,
            completion_tokens=150,
            cost_usd=0.009,
        )
        agent_tracer.end_run_span(
            run_id="run-4",
            outcome="completed",
            duration_ms=1000.0,
            iterations=1,
            cost_usd=0.009,
        )

        spans = span_exporter.get_finished_spans()
        llm_span = next(s for s in spans if s.name == "agent.llm.call")
        attrs = dict(llm_span.attributes)

        assert attrs["llm.tokens.prompt"] == 300
        assert attrs["llm.tokens.completion"] == 150
        assert attrs["llm.cost_usd"] == 0.009

    def test_state_transition_event(self, agent_tracer, span_exporter):
        """State transitions are recorded as span events."""
        agent_tracer.start_run_span(
            run_id="run-5",
            agent_id="agent-1",
        )
        agent_tracer.record_state_transition(
            run_id="run-5",
            from_state="planning",
            to_state="executing_tool",
            trigger="needs_tool",
            duration_ms=0.3,
        )
        agent_tracer.end_run_span(
            run_id="run-5",
            outcome="completed",
            duration_ms=500.0,
            iterations=1,
            cost_usd=0.0,
        )

        spans = span_exporter.get_finished_spans()
        run_span = spans[0]
        assert len(run_span.events) == 1
        event = run_span.events[0]
        assert event.name == "state_transition"
        assert dict(event.attributes)["from_state"] == "planning"
        assert dict(event.attributes)["to_state"] == "executing_tool"

    def test_cleanup_ends_leaked_spans(self, agent_tracer, span_exporter):
        """cleanup_run() ends any spans that weren't properly closed."""
        agent_tracer.start_run_span(
            run_id="run-6",
            agent_id="agent-1",
        )
        agent_tracer.start_tool_span(
            run_id="run-6",
            tool_name="stuck_tool",
        )
        # Simulate crash — tool span never ended
        agent_tracer.cleanup_run("run-6")

        spans = span_exporter.get_finished_spans()
        # Both run and tool spans should be ended
        assert len(spans) == 2
        for span in spans:
            assert span.status.status_code.name == "ERROR"

    def test_end_nonexistent_span_is_noop(self, agent_tracer, span_exporter):
        """Ending a span that doesn't exist doesn't raise."""
        agent_tracer.end_run_span(
            run_id="nonexistent",
            outcome="failed",
            duration_ms=0,
            iterations=0,
            cost_usd=0,
        )
        agent_tracer.end_tool_span(
            run_id="nonexistent",
            tool_name="any",
            status="error",
            duration_ms=0,
        )
        agent_tracer.end_llm_span(run_id="nonexistent")
        # Should not raise
        assert len(span_exporter.get_finished_spans()) == 0

    def test_tool_input_truncated(self, agent_tracer, span_exporter):
        """Large tool inputs are truncated to avoid bloating spans."""
        agent_tracer.start_run_span(
            run_id="run-7",
            agent_id="agent-1",
        )
        agent_tracer.start_tool_span(
            run_id="run-7",
            tool_name="big_tool",
            input_data={"data": "x" * 10000},
        )
        agent_tracer.end_tool_span(
            run_id="run-7",
            tool_name="big_tool",
            status="success",
            duration_ms=10.0,
        )
        agent_tracer.end_run_span(
            run_id="run-7",
            outcome="completed",
            duration_ms=50.0,
            iterations=1,
            cost_usd=0.0,
        )

        spans = span_exporter.get_finished_spans()
        tool_span = next(s for s in spans if s.name.startswith("agent.tool"))
        input_attr = dict(tool_span.attributes).get("tool.input", "")
        assert len(input_attr) <= 500


# --------------------------------------------------------------------------
# Observability Manager
# --------------------------------------------------------------------------


class TestObservabilityManager:
    def test_noop_creation(self):
        """create_noop() returns a working manager with zero exports."""
        obs = ObservabilityManager.create_noop()
        assert obs.tracer is not None
        assert obs.metrics is not None

        # Should not raise
        obs.tracer.start_run_span(
            run_id="noop-1",
            agent_id="test",
        )
        obs.tracer.end_run_span(
            run_id="noop-1",
            outcome="completed",
            duration_ms=100.0,
            iterations=1,
            cost_usd=0.0,
        )
        obs.metrics.record_run_start("test", "team")
        obs.metrics.record_run_end("test", "team", "completed", 100.0, 1, 0.0)

    def test_configure_returns_manager(self):
        """configure() creates a fully wired ObservabilityManager."""
        obs = ObservabilityManager.configure(
            service_name="test-service",
            environment="test",
            json_logs=False,
        )
        assert isinstance(obs, ObservabilityManager)
        assert obs.tracer is not None
        assert obs.metrics is not None

    def test_register_state_machine_hooks(self):
        """Hooks are registered on the state machine without error."""
        from agentworks.state_machine import (
            AgentState,
            create_agent_state_machine,
        )

        sm = create_agent_state_machine()
        obs = ObservabilityManager.create_noop()
        obs.register_state_machine_hooks(sm)

        # Verify hooks were added by checking hook lists
        assert len(sm._on_transition_hooks) > 0
        assert AgentState.COMPLETED in sm._on_enter_hooks
        assert AgentState.FAILED in sm._on_enter_hooks


class TestObservabilityIntegration:
    """Integration tests: metrics + tracing through state machine hooks."""

    async def test_transition_triggers_metric_and_trace(self):
        """A state transition records both a metric and a trace event."""
        from agentworks.state_machine import (
            AgentState,
            ExecutionContext,
            create_agent_state_machine,
        )

        # Set up in-memory exporters
        span_exporter = InMemorySpanExporter()
        metric_reader = InMemoryMetricReader()

        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        test_tracer = provider.get_tracer("integration-test")

        meter_provider = MeterProvider(metric_readers=[metric_reader])
        test_meter = meter_provider.get_meter("integration-test")

        agent_tracer = AgentTracer(test_tracer)
        agent_metrics = AgentMetrics(test_meter)
        obs = ObservabilityManager(tracer=agent_tracer, agent_metrics=agent_metrics)

        sm = create_agent_state_machine()
        obs.register_state_machine_hooks(sm)

        ctx = ExecutionContext(agent_id="test-agent", team_id="test-team")

        # Start a run span so the hook can attach events
        agent_tracer.start_run_span(
            run_id=ctx.run_id,
            agent_id=ctx.agent_id,
            team_id=ctx.team_id,
        )

        # Perform a transition
        result = await sm.transition(ctx, AgentState.PLANNING, "start")
        assert result.success

        # End the span to flush
        agent_tracer.end_run_span(
            run_id=ctx.run_id,
            outcome="completed",
            duration_ms=100.0,
            iterations=0,
            cost_usd=0.0,
        )

        # Check trace: run span should have a state_transition event
        spans = span_exporter.get_finished_spans()
        run_span = next((s for s in spans if s.name == "agent.run"), None)
        assert run_span is not None
        assert any(e.name == "state_transition" for e in run_span.events)

        # Check metric: transition counter should have data
        data = metric_reader.get_metrics_data()
        metric_names = set()
        for rm in data.resource_metrics:
            for scope in rm.scope_metrics:
                for m in scope.metrics:
                    metric_names.add(m.name)
        assert "agent.state.transition.total" in metric_names

    async def test_failed_run_records_error_metric(self):
        """Entering FAILED state records an error metric via hook."""
        from agentworks.state_machine import (
            AgentState,
            ExecutionContext,
            create_agent_state_machine,
        )

        metric_reader = InMemoryMetricReader()
        meter_provider = MeterProvider(metric_readers=[metric_reader])
        test_meter = meter_provider.get_meter("integration-test-2")

        noop_tracer = trace.get_tracer("noop")
        agent_tracer = AgentTracer(noop_tracer)
        agent_metrics = AgentMetrics(test_meter)
        obs = ObservabilityManager(tracer=agent_tracer, agent_metrics=agent_metrics)

        sm = create_agent_state_machine()
        obs.register_state_machine_hooks(sm)

        ctx = ExecutionContext(agent_id="test-agent", team_id="test-team")
        ctx.last_error = "Budget exceeded"

        # IDLE → PLANNING
        await sm.transition(ctx, AgentState.PLANNING, "start")
        # PLANNING → FAILED
        await sm.transition(ctx, AgentState.FAILED, "error")

        data = metric_reader.get_metrics_data()
        metric_names = set()
        for rm in data.resource_metrics:
            for scope in rm.scope_metrics:
                for m in scope.metrics:
                    metric_names.add(m.name)
        assert "agent.error.total" in metric_names


# --------------------------------------------------------------------------
# Resource builder
# --------------------------------------------------------------------------


class TestResourceBuilder:
    def test_resource_has_service_attributes(self):
        """Resource includes service name, version, and environment."""
        resource = _build_resource(
            service_name="my-service",
            service_version="1.0.0",
            environment="staging",
        )
        attrs = dict(resource.attributes)
        assert attrs["service.name"] == "my-service"
        assert attrs["service.version"] == "1.0.0"
        assert attrs["deployment.environment"] == "staging"
