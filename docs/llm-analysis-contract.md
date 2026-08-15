# Evidence-Grounded LLM Analysis Contract 1.0.0

Phase C-2 consumes exactly one validated Phase C-1 Analysis Brief bundle. It does not query
BigQuery, rerun an analyzer, retrieve raw telemetry, or modify game content. The default operational
workflow is provider-neutral:

```text
analysis-prompt -> external LLM JSON -> analysis-validate
```

No OpenAI, Anthropic, HTTP, or credential adapter is included in v1. `AnalysisProvider` is a Python
protocol for future transports and deterministic tests.

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

