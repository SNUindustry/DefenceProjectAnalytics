# Evidence-Grounded LLM Analysis Contract 1.0.0

Phase C-2 consumes exactly one validated Phase C-1 Analysis Brief bundle. It does not query
BigQuery, rerun an analyzer, retrieve raw telemetry, or modify game content. The manual operational
workflow is provider-neutral:

```text
analysis-prompt -> external LLM JSON -> analysis-validate
```

`AnalysisProvider` remains the transport boundary. Phase C-2A adds one native Anthropic transport;
no OpenAI-compatible layer or other provider adapter is used.

## Input and integrity

The source bundle must contain `brief.md`, `brief.json`, `evidence.json`, and `manifest.json` with
Analysis Brief and selection-policy version `1.0.0`. C-2 recomputes the semantic digest over
`brief.json` and `evidence.json`, validates the scope hash and every canonical Evidence ID, and
records all four current artifact hashes in the prompt request. The hashes are checked again before
response validation and atomic output. A change after prompt generation fails with
`SOURCE_BRIEF_MUTATED`.

`brief.md` is human-readable but is not duplicated into the structured prompt context. The prompt
uses `brief.json` and all selected `evidence.json` items. Evidence is never silently truncated by
C-2; an oversized context fails and must be regenerated in C-1 with a smaller selection budget.

## Reasoning layers

The canonical response keeps the following types separate:

- Observation: direct qualitative selection of one or more Evidence IDs.
- Interpretation: non-causal meaning attached to cited evidence and limitations.
- Hypothesis: falsifiable possible explanation with support, counter-evidence search, assumptions,
  alternatives, gaps, and checks.
- EvidenceGap: a question addressable by a known analyzer or new telemetry.
- ChangeCandidate: a hold, investigation, experiment, balance, UX, or telemetry candidate.
- ValidationPlan: known analyses and metrics used to evaluate an actionable candidate.

Observations and the executive summary do not accept factual numeric fields or numeric freeform
claims. Exact scalar, ratio, baseline, candidate, denominator, and delta values are looked up from
the cited Evidence IDs by the deterministic renderer. C-2 does not recalculate analytics metrics.

## Counter evidence

`FoundInSuppliedBrief` requires cited counter evidence. `NotIdentifiedInSuppliedBrief` means only
that no contradictory evidence was identified in the selected C-1 brief. It does not claim that
contradictory evidence is absent from omitted, unavailable, external, or future data.

Evidence strength is scoped to `suppliedC1Brief`. `NotIdentifiedInSuppliedBrief` never increases
strength; it merely avoids a counter-evidence cap. A found counter item caps strength at Moderate.
Limited C-1 evidence cannot become Strong.

## Actionability and human authority

Actionability is calculated locally:

```text
Hold
Investigate
ExperimentCandidate
HumanReviewCandidate
```

There is no production-ready or automatically executable state. `HumanReviewCandidate` means only
that a human may review a controlled experiment candidate. It does not approve a Unity change,
commit, deployment, release, or rollout. The corresponding overall assessment is
`HumanReviewCandidateAvailable`, with the same limitation.

An exact percentage tuning candidate must be finite, positive, explicitly heuristic, use
`magnitudeBasis="HeuristicExperimentCandidate"`, and link to a Validation Plan. Its magnitude is not
an evidence-derived optimum and its actionability is capped at ExperimentCandidate.

## Validation and language safety

The local validator rejects unknown fields, invalid enums, duplicate output IDs, unknown Evidence
IDs, invented entity or metric targets, non-finite values, unsupported causal certainty, standalone
numeric prose claims, invented sample thresholds, missing validation links, raw telemetry IDs, and
credential-like content. Invalid responses produce no final artifact and are not automatically
repaired.

The prompt explicitly treats evidence labels, values, warnings, and content IDs as untrusted data,
not instructions. This is a contract boundary, not a claim that unit tests can prove model-level
prompt-injection resistance.

## Artifacts and identity

Prompt request:

```text
reports/generated/analysis-requests/<scope>__AR-<hash12>/
  prompt.md
  request.json
  manifest.json
```

Validated analysis:

```text
reports/generated/llm-analysis/<scope>__AX-<hash16>/
  analysis.md
  analysis.json
  prompt.md
  manifest.json
```

`analysis.json` is canonical. `analysis.md` is rendered deterministically from it and the C-1
evidence catalog. The execution identity includes prompt, provider descriptor, and normalized
analysis digests. Different semantic responses create different executions. Raw provider responses
and credentials are not stored.

## Anthropic provider transport

The Anthropic adapter uses the official Python SDK and its environment-based
`ANTHROPIC_API_KEY` configuration. The key is never passed as a CLI argument, copied into a prompt,
serialized into a manifest, or included in an exception. The provider uses `claude-opus-5` by
default and accepts an explicit model override.

The provider maps the existing semantic prompt into a top-level Anthropic `system` value and one
`user` message containing the untrusted evidence boundary. This representation does not replace or
rehash the provider-independent `promptDigest`; a separate secret-free provider request digest is
recorded.

The request uses native Structured Outputs through `output_config.format` with
`type="json_schema"`. The wire schema contains the seven model-generated analysis sections as
structured object/array fields and enforces their required fields, enums, nested rollback-indicator
shape, and `additionalProperties=false`. It is an explicit provider-compatible projection of the
canonical C-2 schema. Constraints that Anthropic Structured Outputs does not enforce, including
`maxItems` and string-length limits, are removed from the wire schema and retained as descriptions;
the unchanged local validator remains the hard enforcement authority for all collection and text
limits. Source identity, analysis version, and
comparison direction are host-controlled: they are projected from the validated C-1 prompt package
and cannot be supplied or modified by the model. The full contract schema digest and the explicit
`hostIdentityStructuredBody` mode, plus the transformed wire-schema digest, are recorded in
metadata. Token counting and generation receive the same transformed wire schema. Structured output is therefore a
transport boundary, not the final trust boundary: field shape, Evidence IDs, metrics, entities,
comparison direction, Limited/Insufficient gating, counter-evidence semantics, causal wording,
tuning policy, privacy, and validation-plan linkage are checked locally. Unsupported Structured
Outputs fail explicitly and never fall back to unconstrained text.

Anthropic documents that its SDK removes unsupported JSON Schema constraints, transfers the
constraint guidance into descriptions, and validates the original schema locally. This adapter
implements that boundary explicitly rather than depending on a private SDK helper. A generation
that exceeds a canonical cardinality can therefore pass the provider grammar but fails closed in
the local C-2 validator; it is never truncated, repaired, or regenerated.

Before generation, `messages.count_tokens` receives the same model, system/user messages, and
output schema. The provider does not truncate C-1 evidence when the request is too large. It records
`inputTokenCount` separately from the generation response's `actualInputTokens` and
`actualOutputTokens` because the API values may differ.

Only transient connection, timeout, rate-limit, and server errors are eligible for a bounded
transport retry. The SDK's internal retry is disabled so retry counts are not duplicated. Request,
schema, credential, local validation, malformed response, refusal, and truncation failures are not
repaired. A `max_tokens` or context-window stop is reported as a truncated-response provider error;
partial JSON is never sent to the C-2 validator.

Anthropic response bodies are parsed as exactly one JSON object. Provider transport metadata in the
final manifest is limited to model, token counts, call/retry/error counts, Structured Outputs use,
and digests. No raw provider response, request ID, authorization value, or price estimate is saved.

References: [Anthropic Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs),
[Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting),
[Python SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python), and
[API errors](https://platform.claude.com/docs/en/api/errors).
