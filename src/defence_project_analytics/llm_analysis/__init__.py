"""Provider-neutral Phase C-2 evidence-grounded analysis API."""

from defence_project_analytics.llm_analysis.api import (
    build_analysis_prompt,
    generate_analysis_prompt,
    generate_validated_analysis,
    run_analysis_with_provider,
    validate_analysis_response,
)
from defence_project_analytics.llm_analysis.errors import (
    AnalysisResponseValidationError,
    LlmAnalysisError,
    PromptContextTooLargeError,
    SourceBriefMutationError,
    SourceBriefValidationError,
)
from defence_project_analytics.llm_analysis.models import (
    AnalysisPromptPackage,
    AnalysisPromptRequest,
    AnalysisProvider,
    ValidatedAnalysis,
)
from defence_project_analytics.llm_analysis.provider_errors import (
    AnalysisProviderError,
    AnthropicAuthenticationError,
    AnthropicConnectionError,
    AnthropicContextTooLargeError,
    AnthropicCredentialError,
    AnthropicMalformedResponseError,
    AnthropicPermissionError,
    AnthropicRateLimitError,
    AnthropicRequestValidationError,
    AnthropicResponseTruncatedError,
    AnthropicServerError,
    AnthropicStructuredOutputUnsupportedError,
    AnthropicTimeoutError,
)
from defence_project_analytics.llm_analysis.providers import (
    DEFAULT_ANTHROPIC_MAX_OUTPUT_TOKENS,
    DEFAULT_ANTHROPIC_MODEL,
    AnthropicAnalysisProvider,
    ScriptedAnalysisProvider,
)

__all__ = [
    "AnalysisPromptPackage",
    "AnalysisPromptRequest",
    "AnalysisProvider",
    "ValidatedAnalysis",
    "ScriptedAnalysisProvider",
    "AnthropicAnalysisProvider",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_ANTHROPIC_MAX_OUTPUT_TOKENS",
    "AnalysisProviderError",
    "AnthropicCredentialError",
    "AnthropicAuthenticationError",
    "AnthropicPermissionError",
    "AnthropicRateLimitError",
    "AnthropicRequestValidationError",
    "AnthropicStructuredOutputUnsupportedError",
    "AnthropicContextTooLargeError",
    "AnthropicTimeoutError",
    "AnthropicConnectionError",
    "AnthropicServerError",
    "AnthropicResponseTruncatedError",
    "AnthropicMalformedResponseError",
    "LlmAnalysisError",
    "SourceBriefValidationError",
    "SourceBriefMutationError",
    "PromptContextTooLargeError",
    "AnalysisResponseValidationError",
    "build_analysis_prompt",
    "generate_analysis_prompt",
    "validate_analysis_response",
    "generate_validated_analysis",
    "run_analysis_with_provider",
]
