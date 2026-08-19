# Analysis Brief Contract 1.0.0

Phase C-1 is a local-only compiler for existing aggregate analytics reports. It accepts explicit
generated bundle paths and never invokes BigQuery, ADC, a source analyzer, a raw bucket, or an LLM.
Selection policy `1.1.0` separates metric readability from evidence eligibility. Feedback metrics
in historical Post-Run or Comparison bundles are recognized and hashed with their source bundle,
but are never emitted as new C-1 Evidence or provider-facing feedback-only warnings.

## Input modes

- `singleVersion` accepts one unique bundle per available B-1 through B-5 analysis. Environment,
  contentVersion, common exact filters, and stage-dependent stage scopes must be compatible.
- `contentVersionCompare` accepts exactly one B-6 bundle and preserves its baseline/candidate,
  delta, status, warning, and observed-only semantics without recomputation.
- Partial single-version input is valid and is reported as partial domain coverage. No latest-report
  discovery is performed.

`metadata.json`, `metrics.json`, and required aggregate CSVs are machine sources. `report.md` must
exist and is hashed but is never parsed as evidence.

## Evidence identity and provenance

Canonical identity consists of mode, source analysis type, domain, metric family, metric, entity,
and dimension fields. Duplicate canonical identity is an adapter/contract error and is checked
before hashing. Different identities producing the same 12-hex SHA-256 prefix are reported
separately as a hash collision.

Evidence IDs use `EV-<domain>-<metric>-<hash12>`. Values and row ordinals are not identity inputs.
Provenance records a portable bundle identity, bundle digest, bundle-relative artifact, artifact
digest, and stable aggregate row key. Absolute paths and raw telemetry identifiers are not emitted.

## Snapshot compatibility

- Stage Difficulty records `uploadedAtUtcUpperBound`, or `unboundedIngestionAtGeneration` when no
  explicit cutoff exists.
- Weapon, Upgrade, Progression, and Post-Run record `analysisAsOfUtcParameter`.
- A guarantee-mode difference is reported independently and does not alone make a brief Limited.
- An explicit cutoff difference over 60 seconds or a bounded/unbounded source mixture is limiting.
- None of these modes represents a BigQuery historical system-time snapshot.

## Selection and budget

Mandatory scope, sample, quality, coverage, and warnings precede evidence and do not consume an
evidence item slot. Selection then uses B-6 top changes, domain core facts, supporting context,
additional comparable entities, and additional Limited entities.

```text
maxBriefCharacters = 48000
maxEvidenceItems = 120
maxEvidenceItemsPerDomain = 30
coreEvidenceMinimumPerDomain = 3
```

Up to three observed core facts are reserved for every included domain irrespective of Limited or
low-sample status. Limited evidence always carries its status, warning codes, and source sample. An
unavailable fact is never synthesized to fill quota. Items are omitted whole, and budget omission
is distinct from source unavailability.

## Output roles

- `brief.md`: compact LLM-readable scope, constraints, quality, and selected facts.
- `brief.json`: structured summary and Evidence ID references.
- `evidence.json`: canonical selected factual values and provenance.
- `manifest.json`: source hashes, snapshot compatibility, policy, selection counts, and local-only
  execution assertions.

Output is deterministic for the same source bytes, request, and policy except for manifest
`generatedAtUtc`. Source hashes are checked again immediately before atomic installation.

## Interpretation boundary

C-1 preserves zero versus missing, warnings, coverage, comparability, and observation units. It does
not calculate cross-domain relationships, causal effects, value judgments, or balance actions. A
later C-2 consumer should attach an Evidence ID to every data-backed factual claim and keep
observations, interpretations, hypotheses, and proposed actions separate.

Phase C-2 consumes this contract through the provider-neutral workflow documented in
[`llm-analysis-contract.md`](llm-analysis-contract.md). C-2 must preserve C-1 evidence values,
warnings, missingness, and comparison direction rather than recomputing them.
