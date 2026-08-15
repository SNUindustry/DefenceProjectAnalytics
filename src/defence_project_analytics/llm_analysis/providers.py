"""Provider protocol helpers; no network transport is implemented in C-2 v1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from defence_project_analytics.llm_analysis.models import AnalysisPromptPackage


@dataclass(frozen=True, slots=True)
class ScriptedAnalysisProvider:
    """Deterministic test provider that returns one predefined response."""

    response: Mapping[str, object]
    expected_prompt_digest: str | None = None
    provider_name: str = "scripted"
    model_name: str | None = "fixture"

    def generate(self, prompt: AnalysisPromptPackage) -> Mapping[str, object]:
        if (
            self.expected_prompt_digest is not None
            and prompt.prompt_digest != self.expected_prompt_digest
        ):
            raise ValueError("Scripted provider received an unexpected prompt digest")
        return self.response
