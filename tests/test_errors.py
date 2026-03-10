"""
Tests for the Error Classification System (Phase 4).

Covers:
  - ErrorTier enum values
  - Classification priority: FATAL > RETRYABLE > RECOVERABLE
  - Pattern matching on error_type, message, and http_status
  - Recovery hint generation for Tier 2 errors
  - Default-to-recoverable behavior (the key design choice)
"""

import pytest

from agentworks.errors import (
    ClassifiedError,
    ErrorClassifier,
    ErrorTier,
)

# --------------------------------------------------------------------------
# ErrorTier basics
# --------------------------------------------------------------------------


class TestErrorTier:
    def test_tier_values(self):
        assert ErrorTier.RETRYABLE == "retryable"
        assert ErrorTier.RECOVERABLE == "recoverable"
        assert ErrorTier.FATAL == "fatal"

    def test_tier_is_string_enum(self):
        """ErrorTier values can be compared as strings directly."""
        assert ErrorTier.FATAL == "fatal"
        assert ErrorTier.FATAL.value == "fatal"


# --------------------------------------------------------------------------
# Fatal (Tier 3) classification
# --------------------------------------------------------------------------


class TestFatalErrors:
    """Tier 3 errors must NEVER be retried — they're checked first."""

    @pytest.fixture
    def classifier(self):
        return ErrorClassifier()

    def test_auth_401_is_fatal(self, classifier):
        result = classifier.classify("HttpError", "401 Unauthorized")
        assert result.tier == ErrorTier.FATAL
        assert result.error_type == "auth_failure"

    def test_auth_403_is_fatal(self, classifier):
        result = classifier.classify("HttpError", "403 Forbidden")
        assert result.tier == ErrorTier.FATAL

    def test_authentication_error_type_is_fatal(self, classifier):
        result = classifier.classify("AuthenticationError", "Invalid credentials")
        assert result.tier == ErrorTier.FATAL

    def test_invalid_api_key_is_fatal(self, classifier):
        result = classifier.classify("InvalidAPIKey", "Key expired")
        assert result.tier == ErrorTier.FATAL

    def test_budget_exceeded_is_fatal(self, classifier):
        result = classifier.classify("BudgetExceeded", "Run budget of $0.50 exhausted")
        assert result.tier == ErrorTier.FATAL

    def test_budget_exceeded_in_message(self, classifier):
        result = classifier.classify("RuntimeError", "budget_exceeded: limit is $1.00")
        assert result.tier == ErrorTier.FATAL

    def test_safety_violation_is_fatal(self, classifier):
        result = classifier.classify("ContentPolicyViolation", "Response blocked by safety filter")
        assert result.tier == ErrorTier.FATAL

    def test_content_filter_in_message_is_fatal(self, classifier):
        result = classifier.classify("LLMError", "content_filter triggered")
        assert result.tier == ErrorTier.FATAL

    def test_tool_not_found_is_fatal(self, classifier):
        result = classifier.classify("ToolNotFound", "No tool with id 'xyz'")
        assert result.tier == ErrorTier.FATAL

    def test_http_status_401_is_fatal(self, classifier):
        result = classifier.classify("HttpError", "Request failed", http_status=401)
        assert result.tier == ErrorTier.FATAL

    def test_http_status_403_is_fatal(self, classifier):
        result = classifier.classify("HttpError", "Forbidden", http_status=403)
        assert result.tier == ErrorTier.FATAL

    def test_fatal_preserves_tool_id(self, classifier):
        result = classifier.classify(
            "AuthenticationError",
            "Bad key",
            tool_id="customer_lookup",
        )
        assert result.tool_id == "customer_lookup"


# --------------------------------------------------------------------------
# Retryable (Tier 1) classification
# --------------------------------------------------------------------------


class TestRetryableErrors:
    """Tier 1 errors are transient — auto-retry with backoff."""

    @pytest.fixture
    def classifier(self):
        return ErrorClassifier()

    def test_timeout_error_is_retryable(self, classifier):
        result = classifier.classify("TimeoutError", "Request timed out")
        assert result.tier == ErrorTier.RETRYABLE
        assert result.error_type == "timeout"

    def test_read_timeout_is_retryable(self, classifier):
        result = classifier.classify("ReadTimeout", "Read timed out after 30s")
        assert result.tier == ErrorTier.RETRYABLE

    def test_connect_timeout_is_retryable(self, classifier):
        result = classifier.classify("ConnectTimeout", "Connection timed out")
        assert result.tier == ErrorTier.RETRYABLE

    def test_rate_limit_429_is_retryable(self, classifier):
        result = classifier.classify("HttpError", "429 Too Many Requests")
        assert result.tier == ErrorTier.RETRYABLE
        assert result.error_type == "rate_limit"

    def test_rate_limit_error_type_is_retryable(self, classifier):
        result = classifier.classify("RateLimitError", "Slow down")
        assert result.tier == ErrorTier.RETRYABLE

    def test_server_500_is_retryable(self, classifier):
        result = classifier.classify("HttpError", "500 Internal Server Error")
        assert result.tier == ErrorTier.RETRYABLE
        assert result.error_type == "server_error"

    def test_server_502_is_retryable(self, classifier):
        result = classifier.classify("HttpError", "502 Bad Gateway")
        assert result.tier == ErrorTier.RETRYABLE

    def test_server_503_is_retryable(self, classifier):
        result = classifier.classify("HttpError", "503 Service Unavailable")
        assert result.tier == ErrorTier.RETRYABLE

    def test_server_504_is_retryable(self, classifier):
        result = classifier.classify("HttpError", "504 Gateway Timeout")
        assert result.tier == ErrorTier.RETRYABLE

    def test_connection_error_is_retryable(self, classifier):
        result = classifier.classify("ConnectionError", "Connection refused")
        assert result.tier == ErrorTier.RETRYABLE
        assert result.error_type == "connection"

    def test_connection_reset_is_retryable(self, classifier):
        result = classifier.classify("ConnectionReset", "Peer reset")
        assert result.tier == ErrorTier.RETRYABLE

    def test_http_status_429_is_retryable(self, classifier):
        result = classifier.classify("HttpError", "Too many requests", http_status=429)
        assert result.tier == ErrorTier.RETRYABLE

    def test_http_status_503_is_retryable(self, classifier):
        result = classifier.classify("HttpError", "Unavailable", http_status=503)
        assert result.tier == ErrorTier.RETRYABLE


# --------------------------------------------------------------------------
# Recoverable (Tier 2) classification — the default
# --------------------------------------------------------------------------


class TestRecoverableErrors:
    """Tier 2: everything that isn't fatal or retryable defaults here."""

    @pytest.fixture
    def classifier(self):
        return ErrorClassifier()

    def test_unknown_error_is_recoverable(self, classifier):
        result = classifier.classify("WeirdError", "Something unexpected happened")
        assert result.tier == ErrorTier.RECOVERABLE

    def test_validation_error_is_recoverable(self, classifier):
        result = classifier.classify("ValidationError", "Input validation failed for field 'query'")
        assert result.tier == ErrorTier.RECOVERABLE

    def test_schema_error_is_recoverable(self, classifier):
        result = classifier.classify("SchemaError", "Output doesn't match schema")
        assert result.tier == ErrorTier.RECOVERABLE

    def test_recoverable_has_recovery_hint(self, classifier):
        result = classifier.classify("SomeError", "Something broke", tool_id="my_tool")
        assert result.tier == ErrorTier.RECOVERABLE
        assert result.recovery_hint is not None
        assert len(result.recovery_hint) > 0

    def test_invalid_input_hint_mentions_schema(self, classifier):
        """Recovery hint for invalid input should guide the LLM to fix input."""
        result = classifier.classify(
            "invalid_input",
            "Field 'count' must be integer",
            tool_id="search_tool",
        )
        assert "input schema" in result.recovery_hint.lower()
        assert "search_tool" in result.recovery_hint

    def test_validation_hint_mentions_schema(self, classifier):
        """Recovery hint for validation errors should reference schema."""
        result = classifier.classify(
            "TypeError",
            "Input validation failed",
            tool_id="email_tool",
        )
        assert (
            "input schema" in result.recovery_hint.lower() or "email_tool" in result.recovery_hint
        )

    def test_schema_error_hint_suggests_different_approach(self, classifier):
        result = classifier.classify(
            "SchemaError",
            "Output mismatch",
            tool_id="data_tool",
        )
        assert "different" in result.recovery_hint.lower()

    def test_generic_hint_includes_tool_id(self, classifier):
        result = classifier.classify(
            "RandomError",
            "Something random",
            tool_id="my_tool",
        )
        assert "my_tool" in result.recovery_hint


# --------------------------------------------------------------------------
# Classification priority
# --------------------------------------------------------------------------


class TestClassificationPriority:
    """Fatal must beat retryable. This prevents retrying auth failures."""

    @pytest.fixture
    def classifier(self):
        return ErrorClassifier()

    def test_fatal_beats_retryable_when_both_match(self, classifier):
        """If an error matches both fatal AND retryable patterns,
        it must be classified as FATAL (never retried)."""
        # "401" matches both auth_failure (fatal) and could be in a
        # retryable message. Fatal must win.
        result = classifier.classify("AuthenticationError", "401 from server, connection timed out")
        assert result.tier == ErrorTier.FATAL

    def test_case_insensitive_matching(self, classifier):
        """Pattern matching is case-insensitive."""
        result = classifier.classify("timeouterror", "request timed out")
        assert result.tier == ErrorTier.RETRYABLE

    def test_partial_match_in_message(self, classifier):
        """Patterns match as substrings in the message."""
        result = classifier.classify(
            "GenericError",
            "The server returned a 503 error during processing",
        )
        assert result.tier == ErrorTier.RETRYABLE


# --------------------------------------------------------------------------
# ClassifiedError model
# --------------------------------------------------------------------------


class TestClassifiedErrorModel:
    def test_defaults(self):
        err = ClassifiedError(
            tier=ErrorTier.RECOVERABLE,
            error_type="test",
            message="test error",
        )
        assert err.retry_count == 0
        assert err.max_retries == 0
        assert err.original_exception is None
        assert err.tool_id is None
        assert err.context == {}

    def test_full_construction(self):
        err = ClassifiedError(
            tier=ErrorTier.RETRYABLE,
            error_type="timeout",
            message="Connection timed out",
            original_exception="TimeoutError",
            tool_id="api_tool",
            retry_count=2,
            max_retries=3,
            recovery_hint="Try again",
            context={"endpoint": "https://api.example.com"},
        )
        assert err.tier == ErrorTier.RETRYABLE
        assert err.context["endpoint"] == "https://api.example.com"
