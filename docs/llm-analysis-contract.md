# Evidence-Grounded LLM Analysis Contract 1.0.0

R4 `retentionEvidence` can be cited in factual Stage A output. It cannot support
hypotheses, change candidates, targets, guardrails, or rollback indicators. A
comparison-capable R4 metric may appear in `metricsToWatch` as MonitorOnly only
when its C-1 runtime comparison authority is explicitly `Allowed`; missing or
denied authority fails closed.

The canonical analysis and response shape remain version `1.0.0`. The semantic validation policy
is version `1.6.0` and the prompt template is version `1.6.0`. Analysis request bundles record these versions plus
metric registry version `1.2.0` and are
accepted only when they exactly match the current implementation; older request bundles are not
silently revalidated under a newer semantic policy.

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
Analysis Brief version `1.0.0` and selection-policy version `1.0.0`, `1.1.0`, or `1.2.0`. C-2 recomputes the semantic digest over
`brief.json` and `evidence.json`, validates the scope hash and every canonical Evidence ID, and
records all four current artifact hashes in the prompt request. The hashes are checked again before
response validation and atomic output. A change after prompt generation fails with
`SOURCE_BRIEF_MUTATED`.

The final analysis manifest records the C-1 portable path and the validated aggregate source bundle
identities, portable paths, and digests. It does not copy raw telemetry identifiers or source rows.

`brief.md` is human-readable but is not duplicated into the structured prompt context. The prompt
uses `brief.json` and every selected factual-eligible `evidence.json` item. Historical-only items
are excluded by their registered lifecycle; active factual-only B-7 and B-8 items are retained.
Evidence is never silently truncated by C-2; an oversized context fails and must be regenerated in
C-1 with a smaller selection budget.

Feedback metric identities have lifecycle `HistoricalOnly`: they remain parseable for finalized
artifact rendering, but current manual, scripted, and Anthropic execution rejects them as support,
counter-evidence, targets, or validation-plan metrics. Provider projections omit those evidence
items and use only the active decision/target metric catalog.

## Evidence-use authority

The consumer distinguishes three uses while preserving the metric registry's eligibility flags:

- `FACTUAL_REFERENCE` requires `evidenceEligible`. Observations, interpretations,
  interpretation limitations, and Evidence Gap context may cite B-7 `NewAttempt` and B-8
  `ObservedAppReturn` facts.
- `DECISION_SUPPORT` requires `decisionEligible`. Hypothesis support/counter-evidence and
  ChangeCandidate support/counter-evidence cannot cite factual-only B-7/B-8 metrics. A factual
  observation in validated Stage A context does not grant decision authority in Stage B.
- `TARGET_GUARDRAIL` requires target and decision eligibility. Change targets, expected
  observables, validation metrics, guardrails, and rollback indicators cannot use B-7/B-8 metrics.

The provider-neutral prompt carries all factual references. The Anthropic alias namespace includes
them without changing canonical IDs; Stage B also receives the exact subset of alias refs permitted
for decision support. The host validator enforces the same field-level authority regardless of
provider. An all-factual brief may return observations and gaps with empty hypothesis, candidate,
and validation-plan sections. Prompt template `1.4.0` and validation policy `1.4.0` introduced this
authority split; the canonical analysis output and response shape remain `1.0.0`. Policy `1.5.0` also distinguishes
structured target identities from prose: digits may occur in source-validated entity and metric
keys, while freeform claims remain numeric-free.

## Reasoning layers

The canonical response keeps the following types separate:

- Observation: direct qualitative selection of one or more Evidence IDs.
- Interpretation: non-causal meaning attached to cited evidence and limitations.
- Hypothesis: falsifiable possible explanation with support, counter-evidence search, assumptions,
  alternatives, gaps, and checks.
- EvidenceGap: a question addressable by a known analyzer or new telemetry.
- ChangeCandidate: a hold, investigation, experiment, balance, UX, or telemetry candidate.
- ValidationPlan: known analyses and metrics used to evaluate an actionable candidate.

Observation and Interpretation prose must not assert unsupported causal certainty. Hypotheses are
falsifiable possible explanations but remain subject to the existing default prose policy.
Prompt template `1.6.0` explicitly distinguishes observation, association, and causality across all
observational domains. It gives bounded wording examples for measured differences and associations,
reserves causal possibilities for clearly marked Stage B hypotheses, and permits Stage C plans to
test those hypotheses prospectively without upgrading existing evidence to causal proof. The
validator and semantic policy remain unchanged and authoritative.
`ChangeCandidate.risks` has a field-specific prospective-risk policy: it may describe possible or
modal adverse consequences and uncertainties of the proposed change, but it must not state those
consequences as established or certain. A certainty marker takes precedence when modal and certain
wording are mixed. This causal distinction does not relax numeric-free, length, privacy, or
credential checks.

`EvidenceGap.question` and `EvidenceGap.whyItMatters` use a separate investigation-gap policy.
They may name a possible causal relationship only as an unresolved question, uncertainty, or need
for validation that the supplied evidence cannot settle. Established assertions such as `X caused
Y`, `Y increased because of X`, `원인이다`, or `때문에 감소했다` remain invalid. This is distinct
from prospective risk and does not change citation, numeric-free, privacy, quality, or confidence
rules. Every other prose field continues to use its existing causal mode.

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

The prospective-risk policy accepts wording such as `may increase`, `could reduce`, `악화될 수
있다`, and `원인일 가능성이 있다`. It rejects established or certain wording such as `caused`,
`will cause`, `is the reason`, `원인이다`, `반드시 ...한다`, and `초래한다`. Mixed wording such
as `may definitely cause` or `반드시 악화될 수 있다` is rejected because certainty takes
precedence. The same `UNSUPPORTED_CAUSAL_LANGUAGE` issue code is used.

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

An optional C-2A live semantic acceptance uses one explicit `claude-opus-5`
production-path run after all local gates pass; deterministic structural acceptance does not
require an API call. Another model's semantic variance is diagnostic for
that model and does not relax the canonical response contract or validator. Final acceptance does
not perform semantic regeneration, repair, model fallback, or automatic resume. The actual selected
model continues to be recorded in provider metadata.

The provider preserves the provider-independent/manual prompt package, then builds an
Anthropic-only compact projection for the actual API request. The compact payload factors repeated
bundle/artifact provenance and `(status, warningCodes)` sets into deterministic dictionaries,
removes only reconstructible canonical-identity duplication, and retains every evidence row and
source row key. Single-source briefs factor one source identity; multi-source single-version briefs
use exact per-row source references that reconstruct each source identity without loss. For Anthropic
Stage A/B only, canonical Evidence IDs are replaced by a deterministic
1-based integer alias table. The compact payload and prior-stage projections must inverse to the
exact canonical objects by deep equality. The canonical C-1 artifacts remain immutable and
authoritative. The actual Anthropic request has separate secret-free request, compact-payload, and
alias-table digests.

The seven strict section tools are executed in three sequential stages. Stage A requests
observations, interpretations, and evidence gaps. Stage B requests hypotheses and change
candidates. Stage C requests validation plans and the executive summary. Every tool has
`strict=true`, a required-only object input schema, and `additionalProperties=false`; every stage
has zero optional properties, `anyOf`, and nullable fields. Null canonical values use the existing
explicit flat transport sentinels.

Stage A receives the complete compact C-1 factual Evidence catalog. Stage B reuses the same integer
alias namespace but receives only the exact decision-eligible raw Evidence subset; validated Stage A
observations and interpretations preserve factual-only context without granting it decision authority.
Each Evidence-reference field uses an exact request-specific integer enum; strings,
range coercion, fuzzy matching, and out-of-range aliases are invalid. Validated Stage A output is
reprojected before Stage B, so Anthropic never sees both integer aliases and canonical Evidence IDs.
Stage B context lists `decisionEvidenceRefs` separately; only those refs may support a hypothesis
or change candidate, and the strict-tool schema admits exactly that subset. The host validator
rechecks the same boundary after canonical alias restoration.
Stage C receives no raw Evidence rows, Evidence references, or artifact/quality dictionaries; it
receives the remaining validated A/B context, separate host assessments, brief constraints, and the
exact case-sensitive metric registry through a second deterministic request-local integer alias
table. Stage B and C tool outputs use only integer `metricRef` values. Stage C prior change
candidates are reprojected into the same metric namespace, while canonical metric strings appear
only in the provider context catalog used to select those aliases.
Canonical C-1 Evidence remains factual authority. Prior-stage output is derived analytical context
and cannot substitute for Evidence IDs.

Anthropic does not generate canonical hypothesis, change-candidate, or validation-plan IDs. Stage B
returns ordered semantic rows without `id`; the host assigns `HYP-###` and `CHG-###` from array order.
The model selects plan presence through `includeValidationPlan`, while the host assigns unique
`VAL-###` IDs in candidate order. The unchanged validator still requires plans for actionable action
types and preserves the existing optional-plan semantics for other action types. Stage B gap links
use exact integer `evidenceGapRef` values rather than copied `GAP-###` strings.

The host then builds a request-local `validationPlanRef` table from every validated Stage B candidate
that has a host-assigned plan, preserving candidate order. Stage C must return every exact integer ref
once; the host restores the `(validationPlanId, changeCandidateId)` pair and the unchanged partial/full
validators recheck linkage. Anthropic's provider wire schema omits unsupported array `maxItems`
constraints rather than duplicating exact coverage in the grammar; missing, duplicate, or extra refs
still fail during host reconstruction. Executive-summary selections likewise use exact request-local observation,
hypothesis, evidence-gap, and change-candidate integer refs; canonical IDs are restored only after tool
collection. `analysesToRerun`, `minimumEvidenceRequirements`, comparison plans, and rollback conditions
are strict enums sourced from canonical constants. No sample-size formula is inferred, and the rollback
metric and condition are one required-only `rollbackIndicators` wire item. Stage B expected metric
and direction are likewise one `expectedObservables` item. The production wire therefore has no
semantic pair represented as parallel arrays; host reconstruction restores the unchanged canonical
objects. The legacy monolithic flat helper retains its old parallel-array checks for reference-only
regression and has no production provider caller. These are Anthropic-only transport
constraints; provider-neutral canonical responses keep their existing fields and shape.

The provider verifies that no exact canonical Evidence ID occurs anywhere in the prepared Stage A/B
request, including messages, context, tool descriptions, and schemas. This check runs before token
counting. Tool results are inverse-mapped to canonical IDs before partial validation, and aliases are
never written to canonical analysis output. Metadata records only alias version, count, and digest,
not the alias table.

For Anthropic Stage B, `counterEvidenceRefs` is the sole model-owned counter-evidence output.
`counterEvidenceSearchStatus` is absent from both Stage B tool item schemas. After exact Evidence
alias inversion, the host derives `FoundInSuppliedBrief` for a non-empty list and
`NotIdentifiedInSuppliedBrief` for an empty list, then passes both canonical fields through the
unchanged partial and full validators. This is an authority boundary, not repair of contradictory
model output. Empty means only that the model did not identify counter evidence in the supplied C-1
brief; it does not establish absence elsewhere. Manual and scripted providers continue to submit
both canonical fields directly.

The metric alias table is built from the actual sorted shared registry; its size is never hardcoded.
Target metrics, expected observable metrics, watch metrics, guardrails, and rollback metrics all
round-trip through this table by exact lookup. A Change Target carries zero or one metric, so its
provider field is the required scalar `targetMetricRef`: `0` is the target-only null sentinel and
`1..M` are exact metric aliases. Every genuine multi-metric field remains an array. Unknown, string,
boolean, and out-of-range references fail closed; zero remains invalid outside the nullable target
field. Expected observable directions use the canonical
`Increase`/`Decrease`/`MonitorOnly`/`NoAssumedDirection` enum. Metadata records only the metric
alias version, count, and digest.

Anthropic Change Target authority is target-type specific. For `EvidenceMetric` and
`EvidenceEntity`, the exact structured identity is authoritative and reconstruction always emits
canonical `description=null`; the provider-supplied structured-target
`requiresGameDesignContext` boolean is preserved. For `Conceptual`, the optional qualitative
description remains provider-owned and is subject to the unchanged numeric, causal-language,
length, and privacy rules, while the host derives `requiresGameDesignContext=true` from the
existing canonical invariant. The required-only wire fields are named
`conceptualTargetDescription` and `structuredTargetRequiresGameDesignContext`; each is ignored
outside the target type for which it is authoritative. This provider-specific normalization does
not narrow the provider-neutral schema, which still permits an optional description and either
boolean value for structured targets. Stage C receives the already-normalized canonical target and
omits the redundant structured-target description annotation.

Warning references use one deterministic authority derived from the same model-visible C-1 input:
critical/global warnings in the compact brief, domain/source warnings under
`sourceStatusByDomain`, and warning codes attached to selected Evidence rows. Anthropic Stage A/B
project this sorted authority into one request-local integer namespace `1..W`; strict-tool output
uses `limitationWarningRefs`, and prior-stage warning fields are reprojected into the same namespace.
Canonical labels appear once in `warningCatalog` for meaning, but output authority is the exact
integer enum. The host inverse-maps refs before the unchanged partial and full validators. Unknown,
string, boolean, out-of-range, case-corrected, and fuzzy references remain invalid. An empty warning
authority creates no dummy warning and permits only an empty warning-ref array. Metadata stores the
canonical authority digest and alias version/count/digest, never the mapping itself.

Within each stage the model must call every expected tool exactly once. Parallel calls are enabled
and tool order has no semantic meaning. Since Anthropic's `tool_choice=any` does not guarantee the
complete stage set, the host rejects missing, duplicate, unknown, and additional tool calls. Tool
inputs are the sole response authority; assistant text is ignored, counted, and never stored. The
host sends no `tool_result`, repair request, follow-up completion call, or automatic stage resume.

The collector validates only exact tool names and minimum wrapper shape. It does not duplicate
canonical cardinality, enum, Evidence ID, metric/entity, causal, numeric, gating, or cross-reference
rules. Each collected section is reconstructed with exact case-sensitive metric lookup and passed
through validator primitives shared with the full C-2 validator. An A failure prevents B and C; a B
failure prevents C. Validated canonical sections are merged only after C succeeds, then host-controlled
identity/version/direction are injected and the unchanged full `validate_response()` runs as final
authority. Host assessments are context only and are never inserted into the raw canonical response.
No stage infers, repairs, truncates, case-corrects, or defaults an invalid response.

Stage A treats an Evidence Gap as an unanswered analytical question, not an established explanation
of an outcome. It may frame a possible causal relationship as unresolved while describing what the
supplied evidence cannot establish and what additional analysis or observation is needed. Its
field-specific policy continues to reject established causal claims.

Before each generation, `messages.count_tokens` receives that stage's same model, system/user
messages, tools, and tool choice. A stage count of 60,000 input tokens or higher fails the C-2A gate
without that generation or any later stage call. The provider does not truncate C-1 evidence when a
request is too large. It records aggregate and per-stage
`inputTokenCount` separately from the generation response's `actualInputTokens` and
`actualOutputTokens` because the API values may differ.

Only token-preflight transient connection, timeout, rate-limit, and server errors are eligible for a
bounded retry. Generation is never replayed. The SDK's internal retry is disabled so retry counts
are not duplicated. Request, schema, credential, local validation, malformed response, refusal, and
truncation failures are not repaired. A `max_tokens` or context-window stop is reported as a
truncated-response provider error; partial JSON is never sent to the C-2 validator.

The former all-seven strict-tool request, serialized-string envelope, and monolithic flat provider
schema remain reference-only and are not production fallbacks. Shared flat section reconstruction
remains the transport primitive. On normal unretried success there are three token-count calls and
three generation calls. No partial stage artifact is written; only a fully validated analysis uses
the existing atomic writer. Manifest metadata is limited to safe per-stage schema profiles, tool
counts, token/call/retry/error counts, and digests. No raw provider response, tool arguments,
assistant text, request ID, authorization value, or price estimate is saved.

References: [Anthropic strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use),
[parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use),
[Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting),
[Python SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python), and
[API errors](https://platform.claude.com/docs/en/api/errors).
