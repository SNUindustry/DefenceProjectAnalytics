# R4-B Retention Derived Evidence Analysis

## Contract identity

- `analysisType`: `retentionEvidence`
- `analysisVersion`: `1.0.0`
- `policyVersion`: `1.0.0`

R4-B composes factual observations from B-7 NewAttempt return, B-8
ObservedAppReturn, and R3-D app_remove attribution. It does not define a churn
state, a retained/lost state, a composite score, or decision/target authority.

## Restricted inputs

B-7 and B-8 keep their existing aggregate bundles and metric semantics. Their
R4 input is an additive restricted normalized artifact containing the internal
canonical Profile identity, canonical final-attempt anchor time and eligibility,
an optional return occurrence time, source scope, coverage, and immutable source
cut. The adapter SQL selects the final `classified_retention` or `classified`
CTE from the existing source fragment; it does not restate canonicalization,
lifecycle eligibility, or return selection.

R3-D uses `restrictedObservedUninstallEvents` unchanged. Unmapped and ambiguous
events remain source-quality aggregates. Mapped events may enter a Profile
episode. All input manifests and file digests are verified at load, recorded as
source cuts, and checked again immediately before atomic output installation.

## Episode and event semantics

An episode starts immediately after a canonical final-run `anchorEndUtc`. Its
natural end is the next canonical final-run anchor for that Profile or
`analysisAsOfUtc`. A mapped uninstall is attributed once, to the latest
applicable episode before its occurrence time. Repeated removals remain repeated
facts.

The factual ledger keeps every event at or before `analysisAsOfUtc`. An explicit
horizon creates a comparison view; it never deletes later return or uninstall
facts. Ordering uses occurrence timestamps only. Equal timestamps produce the
`SameObservedTime` facet and receive no fabricated ordering.

Valid sequences include return followed by uninstall, uninstall followed by a
later app or gameplay return, and a gameplay return without a lifecycle return.
These are observations, not causal or state transitions.

## Horizon, maturity, and censoring

`horizonDays` and `sourceUploadGraceHours` are supplied together or both omitted.
Maturity requires:

```text
anchorEndUtc + horizon + uploadGrace <= analysisAsOfUtc
and complete source coverage
```

Each source denominator is independent. B-7 mature anchors can differ from B-8
mature lifecycle anchors. An early positive remains factual while the anchor is
immature, but does not enter the mature fixed-horizon denominator. A mature
eligible anchor with no return inside the horizon becomes a
`BoundedAbsenceObservation`. It does not become churn or permanent absence.
Right-censored anchors remain separate and never count as non-return.

When no horizon is configured, R4-B emits the factual sequence summary and null
fixed-horizon metrics.

## Metrics and authority

The registry adds only R4-derived fixed-horizon metrics:

- `gameplayReturn.returnedWithinHorizonCount`
- `gameplayReturn.matureAnchorCount`
- `gameplayReturn.returnedWithinHorizonRate`
- `appReturn.returnedWithinHorizonCount`
- `appReturn.matureAnchorCount`
- `appReturn.returnedWithinHorizonRate`
- `retentionEvidence.rightCensoredCount`
- `retentionEvidence.matureAnchorCount`
- factual mapped, unmapped, and ambiguous observed-uninstall counts

The two return families have static comparison capability. Runtime comparison
remains denied unless the caller supplies a complete R4-A `ComparisonPlan` with
common horizon/grace, compatible scope and source cuts, minimum mature cohorts,
and maximum censoring. Decision, target, guardrail, and rollback authority are
always denied. R4-B defines no uninstall rate because no at-risk app-instance or
Profile denominator exists.

## Output and privacy

The normal atomic bundle contains:

```text
metrics.json
metadata.json
evidence-summary.csv
sequence-summary.csv
maturity-summary.csv
censoring-summary.csv
uninstall-attribution-quality-summary.csv
source-compatibility-summary.csv
report.md
manifest.json
```

It contains no Profile, player, bridge, pseudo, lifecycle, attempt, run, process,
anchor, or episode identifiers. The separate restricted episode artifact retains
the internal Profile and episode identities for validation. Both artifacts have
SHA-256 manifests and record analysis/policy versions and source cuts.

## CLI

`defence-analytics --backend test|production retention-evidence` requires an
explicit logical environment, `--analysis-as-of`, and the three restricted input
paths. `--horizon-days` and `--source-upload-grace-hours` are paired. Optional
`--content-version`, `--release-id`, and `--comparison-plan` constrain scope and
authority. `--dry-run`/`--validation-only` validates and composes in memory
without writing artifacts or creating a BigQuery client.

R4-B performs local normalized-artifact composition and no raw BigQuery rescan.
Test and Production artifacts cannot be combined in one execution.

## R4-C handoff

R4-C can consume the normal aggregate bundle, its manifest, registered summary
metrics, runtime authority resolution, and source-cut metadata. It does not need
the restricted episode artifact. R4-B itself does not register the domain in the
C-1/C-2 production path.
