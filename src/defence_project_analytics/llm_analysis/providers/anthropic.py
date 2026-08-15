"""Native Anthropic Messages API transport for the strict C-2 validator."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Callable, Mapping

from defence_project_analytics.llm_analysis.models import (
    ANALYSIS_VERSION,
    AnalysisPromptPackage,
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
    AnthropicRefusalError,
    AnthropicRequestValidationError,
    AnthropicResponseTruncatedError,
    AnthropicServerError,
    AnthropicStructuredOutputUnsupportedError,
    AnthropicTimeoutError,
    AnthropicTokenCountError,
)
from defence_project_analytics.llm_analysis.provider_schema import (
    analysis_response_json_schema,
    structured_output_config,
    structured_output_transport_instruction,
)


DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_ANTHROPIC_MAX_OUTPUT_TOKENS = 20_000
MAX_NONSTREAMING_ANTHROPIC_OUTPUT_TOKENS = 20_000
DEFAULT_ANTHROPIC_TIMEOUT_SECONDS = 600.0
DEFAULT_ANTHROPIC_MAX_TRANSPORT_RETRIES = 1
_DATA_MARKER = "# UNTRUSTED_ANALYTICS_DATA"


def _safe_error_text(error: BaseException) -> str:
    """Return text only for internal classification; never expose it to callers."""

    body = getattr(error, "body", None)
    if isinstance(body, dict):
        nested = body.get("error")
        if isinstance(nested, dict) and isinstance(nested.get("message"), str):
            return nested["message"].casefold()
        if isinstance(body.get("message"), str):
            return body["message"].casefold()
    return str(error).casefold()


def _safe_error_details(error: BaseException, detail: str) -> dict[str, Any]:
    status = getattr(error, "status_code", None)
    body = getattr(error, "body", None)
    error_type = None
    if isinstance(body, dict):
        nested = body.get("error")
        if isinstance(nested, dict) and isinstance(nested.get("type"), str):
            error_type = nested["type"]
        elif isinstance(body.get("type"), str):
            error_type = body["type"]
    category = "providerRequestRejected"
    safe_message = "Anthropic rejected the provider request."
    if "schema is too complex" in detail or "compiled grammar is too large" in detail:
        category = "schemaComplexity"
        safe_message = "The Structured Outputs schema is too complex for compilation."
    elif "compilation timeout" in detail:
        category = "schemaCompilationTimeout"
        safe_message = "The Structured Outputs schema compilation timed out."
    elif "union" in detail and ("limit" in detail or "too many" in detail):
        category = "schemaUnionLimit"
        safe_message = "The Structured Outputs schema exceeded the union-type limit."
    elif "optional" in detail and ("limit" in detail or "too many" in detail):
        category = "schemaOptionalParameterLimit"
        safe_message = "The Structured Outputs schema exceeded the optional-parameter limit."
    elif "does not support structured" in detail or "structured outputs not supported" in detail:
        category = "modelCapability"
        safe_message = "The selected model or endpoint does not support Structured Outputs."
    elif "output_config" in detail and ("invalid" in detail or "unknown" in detail):
        category = "invalidOutputConfig"
        safe_message = "Anthropic rejected the output_config format."
    elif "unsupported" in detail and any(
        keyword.casefold() in detail
        for keyword in (
            "maxItems", "minItems", "maxLength", "minLength", "minimum",
            "maximum", "pattern", "uniqueItems", "additionalProperties",
        )
    ):
        category = "unsupportedSchemaConstraint"
        safe_message = "The Structured Outputs schema contained an unsupported constraint."
    result: dict[str, Any] = {
        "httpStatus": status if isinstance(status, int) else None,
        "anthropicErrorType": error_type,
        "errorCategory": category,
        "safeMessage": safe_message,
    }
    request_id = getattr(error, "request_id", None)
    if isinstance(request_id, str) and re.fullmatch(r"req_[A-Za-z0-9_-]+", request_id):
        result["requestId"] = request_id
    return result


def map_anthropic_error(error: BaseException) -> AnalysisProviderError:
    """Map SDK exceptions without copying request, credential, or raw error text."""

    name = type(error).__name__
    status = getattr(error, "status_code", None)
    detail = _safe_error_text(error)
    details = _safe_error_details(error, detail)
    if name == "AuthenticationError" or status == 401:
        return AnthropicAuthenticationError("Anthropic authentication failed.", details=details)
    if name == "PermissionDeniedError" or status == 403:
        return AnthropicPermissionError("Anthropic permission was denied.", details=details)
    if name == "RateLimitError" or status == 429:
        return AnthropicRateLimitError("Anthropic rate limit was reached.", details=details)
    if name == "APITimeoutError":
        return AnthropicTimeoutError("Anthropic request timed out.", details=details)
    if name == "APIConnectionError":
        return AnthropicConnectionError("Anthropic connection failed.", details=details)
    if status is not None and int(status) >= 500:
        return AnthropicServerError("Anthropic server returned a transient error.", details=details)
    if name in {"BadRequestError", "UnprocessableEntityError"} or status in {400, 413, 422}:
        if any(value in detail for value in (
            "context window", "context length", "prompt is too long", "too many tokens",
        )):
            return AnthropicContextTooLargeError(
                "Anthropic rejected the request because the context was too large.",
                details=details,
            )
        if details["errorCategory"] == "modelCapability":
            return AnthropicStructuredOutputUnsupportedError(
                "The selected Anthropic model or endpoint rejected Structured Outputs.",
                details=details,
            )
        return AnthropicRequestValidationError(
            "Anthropic rejected the request.", details=details
        )
    return AnalysisProviderError("Anthropic SDK transport failed.", details=details)


def _message_parts(prompt: AnalysisPromptPackage) -> tuple[str, list[dict[str, str]]]:
    before, marker, after = prompt.prompt.partition(_DATA_MARKER)
    if not marker:
        raise AnthropicRequestValidationError(
            "The provider-neutral prompt is missing the untrusted-data boundary."
        )
    system = before.rstrip()
    user = marker + after
    return system, [{"role": "user", "content": user}]


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


class AnthropicAnalysisProvider:
    """One token-count preflight followed by one Structured Outputs generation."""

    provider_name = "anthropic"

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_ANTHROPIC_MODEL,
        max_output_tokens: int = DEFAULT_ANTHROPIC_MAX_OUTPUT_TOKENS,
        timeout_seconds: float = DEFAULT_ANTHROPIC_TIMEOUT_SECONDS,
        max_transport_retries: int = DEFAULT_ANTHROPIC_MAX_TRANSPORT_RETRIES,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not model_name.strip():
            raise ValueError("model_name must be nonblank")
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if max_output_tokens > MAX_NONSTREAMING_ANTHROPIC_OUTPUT_TOKENS:
            raise ValueError(
                "max_output_tokens exceeds the safe non-streaming C-2 limit of 20000"
            )
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_transport_retries < 0 or max_transport_retries > 2:
            raise ValueError("max_transport_retries must be between 0 and 2")
        self.model_name = model_name.strip()
        self.max_output_tokens = int(max_output_tokens)
        self.timeout_seconds = float(timeout_seconds)
        self.max_transport_retries = int(max_transport_retries)
        self._injected_client = client
        self._sleep = sleep
        self.last_run_metadata: dict[str, Any] = {}
        self._reset_counters()

    def _reset_counters(self) -> None:
        self._provider_call_count = 0
        self._token_count_call_count = 0
        self._generation_call_count = 0
        self._error_count = 0
        self._retry_count = 0

    def _sync_counter_metadata(self) -> None:
        self.last_run_metadata.update({
            "providerCallCount": self._provider_call_count,
            "providerTokenCountCallCount": self._token_count_call_count,
            "providerGenerationCallCount": self._generation_call_count,
            "providerErrorCount": self._error_count,
            "providerRetryCount": self._retry_count,
            "configuredMaxTransportRetries": self.max_transport_retries,
        })

    def _client(self) -> Any:
        if self._injected_client is not None:
            return self._injected_client
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            raise AnthropicCredentialError(
                "ANTHROPIC_API_KEY is not visible to this process."
            )
        try:
            import anthropic

            # The official SDK reads ANTHROPIC_API_KEY itself. SDK retry is disabled
            # so the bounded retry count below remains observable and deterministic.
            return anthropic.Anthropic(
                max_retries=0,
                timeout=self.timeout_seconds,
            )
        except AnthropicCredentialError:
            raise
        except Exception as error:
            raise map_anthropic_error(error) from None

    def _invoke(self, operation: str, call: Callable[[], Any]) -> Any:
        attempts = 0
        while True:
            self._provider_call_count += 1
            if operation == "count_tokens":
                self._token_count_call_count += 1
            else:
                self._generation_call_count += 1
            self._sync_counter_metadata()
            try:
                result = call()
                self._sync_counter_metadata()
                return result
            except AnalysisProviderError:
                raise
            except Exception as error:
                mapped = map_anthropic_error(error)
                if operation == "count_tokens" and type(mapped) is AnalysisProviderError:
                    mapped = AnthropicTokenCountError(
                        "Anthropic token counting failed."
                    )
                self._error_count += 1
                self._sync_counter_metadata()
                if mapped.retryable and attempts < self.max_transport_retries:
                    attempts += 1
                    self._retry_count += 1
                    self._sync_counter_metadata()
                    self._sleep(min(2.0**attempts, 2.0))
                    continue
                raise mapped from None

    def _parse_response(
        self,
        response: Any,
        prompt: AnalysisPromptPackage,
    ) -> Mapping[str, object]:
        stop_reason = _attr(response, "stop_reason")
        if stop_reason in {"max_tokens", "model_context_window_exceeded"}:
            raise AnthropicResponseTruncatedError(
                "Anthropic stopped before completing the structured response."
            )
        if stop_reason == "refusal":
            raise AnthropicRefusalError("Anthropic declined the analysis request.")
        text_blocks: list[str] = []
        for block in _attr(response, "content", ()) or ():
            if _attr(block, "type") == "text":
                value = _attr(block, "text")
                if isinstance(value, str) and value.strip():
                    text_blocks.append(value)
        if len(text_blocks) != 1:
            raise AnthropicMalformedResponseError(
                "Anthropic returned an empty or ambiguous structured response."
            )
        try:
            value = json.loads(
                text_blocks[0],
                parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
            )
        except (json.JSONDecodeError, ValueError):
            raise AnthropicMalformedResponseError(
                "Anthropic returned malformed structured JSON."
            ) from None
        body_keys = {
            "executiveSummary",
            "observations",
            "interpretations",
            "hypotheses",
            "evidenceGaps",
            "changeCandidates",
            "validationPlans",
        }
        if not isinstance(value, dict) or set(value) != body_keys:
            raise AnthropicMalformedResponseError(
                "Anthropic C-2 analysis body has an invalid shape."
            )
        identity = prompt.source.identity
        direction = (
            "candidateMinusBaseline"
            if identity.mode == "contentVersionCompare"
            else "notApplicable"
        )
        return {
            "analysisVersion": ANALYSIS_VERSION,
            "sourceBriefIdentity": {
                "semanticOutputDigest": identity.semantic_output_digest,
                "scopeHash": identity.scope_hash,
                "mode": identity.mode,
                "baselineContentVersion": identity.baseline_content_version,
                "candidateContentVersion": identity.candidate_content_version,
            },
            "comparisonDirectionAcknowledgement": direction,
            **value,
        }

    def generate(self, prompt: AnalysisPromptPackage) -> Mapping[str, object]:
        self._reset_counters()
        client = self._client()
        system, messages = _message_parts(prompt)
        system = system + "\n\n" + structured_output_transport_instruction()
        output_config = structured_output_config(prompt)
        wire_schema_digest = hashlib.sha256(
            json.dumps(
                output_config["format"]["schema"],
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        contract_schema_digest = hashlib.sha256(
            json.dumps(
                analysis_response_json_schema(prompt),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        request_shape = {
            "model": self.model_name,
            "maxTokens": self.max_output_tokens,
            "system": system,
            "messages": messages,
            "outputConfig": output_config,
        }
        provider_request_digest = hashlib.sha256(
            json.dumps(
                request_shape,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.last_run_metadata = {
            "providerMode": "anthropicApi",
            "providerRequestDigest": provider_request_digest,
            "structuredOutputsUsed": True,
            "structuredOutputSchemaMode": "hostIdentityStructuredBody",
            "responseContractSchemaDigest": contract_schema_digest,
            "anthropicWireSchemaDigest": wire_schema_digest,
            "inputTokenCount": None,
            "actualInputTokens": None,
            "actualOutputTokens": None,
        }
        self._sync_counter_metadata()
        count = self._invoke(
            "count_tokens",
            lambda: client.messages.count_tokens(
                model=self.model_name,
                system=system,
                messages=messages,
                output_config=output_config,
            ),
        )
        input_token_count = _attr(count, "input_tokens")
        if not isinstance(input_token_count, int) or input_token_count < 0:
            raise AnthropicTokenCountError(
                "Anthropic token count response was invalid."
            )
        self.last_run_metadata["inputTokenCount"] = input_token_count
        response = self._invoke(
            "generation",
            lambda: client.messages.create(
                model=self.model_name,
                max_tokens=self.max_output_tokens,
                system=system,
                messages=messages,
                output_config=output_config,
            ),
        )
        parsed = self._parse_response(response, prompt)
        usage = _attr(response, "usage")
        actual_input = _attr(usage, "input_tokens")
        actual_output = _attr(usage, "output_tokens")
        if not isinstance(actual_input, int) or not isinstance(actual_output, int):
            raise AnthropicMalformedResponseError(
                "Anthropic response usage metadata was invalid."
            )
        self.last_run_metadata.update({
            "actualInputTokens": actual_input,
            "actualOutputTokens": actual_output,
        })
        self._sync_counter_metadata()
        return parsed


__all__ = [
    "AnthropicAnalysisProvider",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_ANTHROPIC_MAX_OUTPUT_TOKENS",
    "MAX_NONSTREAMING_ANTHROPIC_OUTPUT_TOKENS",
    "map_anthropic_error",
]
