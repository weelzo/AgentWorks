"""
Phase 4: Error Classification System

Three tiers of errors, each with a distinct recovery strategy.
This classification drives the execution engine's error handling
and is the primary reason success rate went from 85% to 99.2%.

Classification priority: FATAL > RETRYABLE > RECOVERABLE
  - If an error matches a fatal pattern, it is ALWAYS fatal (never retried)
  - If it matches a retryable pattern, it is retried transparently
  - Everything else is recoverable (fed back to the LLM with a hint)

The "everything else is recoverable" default is the key design choice.
Most LLM "errors" are actually the agent making a correctable mistake
(wrong tool, bad input, misunderstood schema). Giving the LLM feedback
about what went wrong lets it self-correct in the next iteration.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ErrorTier(StrEnum):
    """
    Tier 1 (RETRYABLE): Transient failures. Auto-retry with backoff.
      Examples: network timeout, HTTP 429, HTTP 503, connection reset
      Strategy: Retry up to N times with exponential backoff.
      The agent does not see these errors unless all retries are exhausted.

    Tier 2 (RECOVERABLE): The agent made a mistake it can fix.
      Examples: Invalid tool input, schema mismatch, wrong tool selection
      Strategy: Feed the error back to the LLM and ask it to self-correct.

    Tier 3 (FATAL): Unrecoverable. Stop the run.
      Examples: Authentication failure, budget exceeded, safety violation
      Strategy: Transition to FAILED state immediately.
    """

    RETRYABLE = "retryable"
    RECOVERABLE = "recoverable"
    FATAL = "fatal"


class ClassifiedError(BaseModel):
    """An error with its classification and recovery metadata."""

    tier: ErrorTier
    error_type: str
    message: str
    original_exception: str | None = None
    tool_id: str | None = None
    retry_count: int = 0
    max_retries: int = 0
    recovery_hint: str | None = None  # guidance for the LLM (Tier 2)
    context: dict[str, Any] = Field(default_factory=dict)


class ErrorClassifier:
    """
    Classifies errors into tiers based on error type, HTTP status,
    and tool-specific rules.

    Classification priority: FATAL checked first, then RETRYABLE.
    Everything else defaults to RECOVERABLE with a recovery hint.
    """

    TIER_1_PATTERNS: dict[str, list[str]] = {
        "timeout": ["TimeoutError", "ReadTimeout", "ConnectTimeout"],
        "rate_limit": ["429", "RateLimitError", "rate_limit"],
        "server_error": ["500", "502", "503", "504", "ServerError"],
        "connection": [
            "ConnectionError",
            "ConnectionReset",
            "ConnectionRefused",
        ],
    }

    TIER_3_PATTERNS: dict[str, list[str]] = {
        "auth_failure": ["401", "403", "AuthenticationError", "InvalidAPIKey"],
        "budget_exceeded": ["budget_exceeded", "BudgetExceeded"],
        "iteration_limit": ["max_iterations", "IterationLimit"],
        "safety_violation": [
            "content_filter",
            "safety",
            "ContentPolicyViolation",
        ],
        "tool_not_found": ["not_found", "ToolNotFound"],
    }

    def classify(
        self,
        error_type: str,
        message: str,
        http_status: int | None = None,
        tool_id: str | None = None,
    ) -> ClassifiedError:
        """
        Classify an error into a tier.

        Priority: Tier 3 (fatal) > Tier 1 (retryable) > Tier 2 (recoverable)
        """
        # Check Tier 3 first — fatal errors should never be retried
        for category, patterns in self.TIER_3_PATTERNS.items():
            if self._matches(error_type, message, http_status, patterns):
                return ClassifiedError(
                    tier=ErrorTier.FATAL,
                    error_type=category,
                    message=message,
                    tool_id=tool_id,
                )

        # Check Tier 1 — retryable errors
        for category, patterns in self.TIER_1_PATTERNS.items():
            if self._matches(error_type, message, http_status, patterns):
                return ClassifiedError(
                    tier=ErrorTier.RETRYABLE,
                    error_type=category,
                    message=message,
                    tool_id=tool_id,
                )

        # Default: Tier 2 — recoverable, let the LLM decide
        recovery_hint = self._generate_recovery_hint(error_type, message, tool_id)
        return ClassifiedError(
            tier=ErrorTier.RECOVERABLE,
            error_type=error_type,
            message=message,
            tool_id=tool_id,
            recovery_hint=recovery_hint,
        )

    def _matches(
        self,
        error_type: str,
        message: str,
        http_status: int | None,
        patterns: list[str],
    ) -> bool:
        """Check if the error matches any of the given patterns."""
        for p in patterns:
            if p.lower() in error_type.lower() or p.lower() in message.lower():
                return True
            if http_status and str(http_status) == p:
                return True
        return False

    def _generate_recovery_hint(self, error_type: str, message: str, tool_id: str | None) -> str:
        """Generate a hint for the LLM to help it self-correct."""
        if "invalid_input" in error_type.lower() or "validation" in message.lower():
            return (
                f"The input you provided to tool '{tool_id}' was invalid. "
                f"Error: {message}. Please review the tool's input schema "
                f"and try again with corrected parameters."
            )
        if "schema" in error_type.lower():
            return (
                f"The tool '{tool_id}' returned data in an unexpected format. "
                f"This may indicate you used the wrong tool. "
                f"Consider trying a different approach."
            )
        return (
            f"Tool '{tool_id}' returned an error: {message}. "
            f"You may want to try a different approach or use a different tool."
        )
