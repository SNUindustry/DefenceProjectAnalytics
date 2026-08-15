"""Stable errors for the provider-neutral C-2 analysis layer."""

from __future__ import annotations


class LlmAnalysisError(RuntimeError):
    """Base class for C-2 input, policy, and artifact errors."""


class SourceBriefValidationError(LlmAnalysisError):
    """The supplied C-1 bundle is not internally consistent."""


class SourceBriefMutationError(LlmAnalysisError):
    """The C-1 bundle changed after the prompt package was created."""


class PromptContextTooLargeError(LlmAnalysisError):
    """The complete C-1 selected evidence does not fit the configured cap."""


class AnalysisResponseValidationError(LlmAnalysisError):
    """A provider response failed strict local validation."""

    def __init__(self, issues: tuple[dict[str, str], ...]):
        self.issues = issues
        summary = "; ".join(
            f"{item['code']} at {item['path']}: {item['message']}"
            for item in issues
        )
        super().__init__(summary or "Analysis response is invalid")

