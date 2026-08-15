"""Secret-safe provider transport errors for C-2A."""

from __future__ import annotations

from typing import Any, Mapping


class AnalysisProviderError(RuntimeError):
    """Base error that exposes only a stable code and sanitized message."""

    code = "PROVIDER_ERROR"
    retryable = False

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message or self.code)
        self.details = dict(details or {})


class AnthropicCredentialError(AnalysisProviderError):
    code = "ANTHROPIC_CREDENTIAL_MISSING"


class AnthropicAuthenticationError(AnalysisProviderError):
    code = "ANTHROPIC_AUTHENTICATION_ERROR"


class AnthropicPermissionError(AnalysisProviderError):
    code = "ANTHROPIC_PERMISSION_ERROR"


class AnthropicRateLimitError(AnalysisProviderError):
    code = "ANTHROPIC_RATE_LIMIT_ERROR"
    retryable = True


class AnthropicRequestValidationError(AnalysisProviderError):
    code = "ANTHROPIC_REQUEST_VALIDATION_ERROR"


class AnthropicStructuredOutputUnsupportedError(AnalysisProviderError):
    code = "ANTHROPIC_STRUCTURED_OUTPUT_UNSUPPORTED"


class AnthropicContextTooLargeError(AnalysisProviderError):
    code = "ANTHROPIC_CONTEXT_TOO_LARGE"


class AnthropicTimeoutError(AnalysisProviderError):
    code = "ANTHROPIC_TIMEOUT"
    retryable = True


class AnthropicConnectionError(AnalysisProviderError):
    code = "ANTHROPIC_CONNECTION_ERROR"
    retryable = True


class AnthropicServerError(AnalysisProviderError):
    code = "ANTHROPIC_SERVER_ERROR"
    retryable = True


class AnthropicResponseTruncatedError(AnalysisProviderError):
    code = "ANTHROPIC_RESPONSE_TRUNCATED"


class AnthropicMalformedResponseError(AnalysisProviderError):
    code = "ANTHROPIC_MALFORMED_RESPONSE"


class AnthropicRefusalError(AnalysisProviderError):
    code = "ANTHROPIC_RESPONSE_REFUSED"


class AnthropicTokenCountError(AnalysisProviderError):
    code = "ANTHROPIC_TOKEN_COUNT_ERROR"


__all__ = [name for name in globals() if name.endswith("Error")]
