# R4-A Retention Evidence Policy Contract

Policy name: `RetentionEvidencePolicy`
Policy version: `1.0.0`
Schema version: `1.0.0`

R4-A is an executable policy contract. It defines evidence meaning, provenance,
maturity, and maximum authority. It does not query telemetry, calculate a new
retention population, register derived metrics, or change C-1/C-2 behavior.

## Core model

The source domains retain independent meanings:

- B-7 `NewAttempt` is a direct observation of a new gameplay attempt after a
  canonical final attempt.
- B-8 `ObservedAppReturn` is a direct lifecycle observation of an eligible
  `ColdStart` or `ForegroundResume` after a canonical final attempt.
- R3-D `ObservedUninstall` is a GA app-instance `app_remove` observation. A
  mapped observation says that the app instance was associated with a canonical
  Profile at that time.

These facts do not form one boolean state or one percentage. R4-A defines no
`retentionHealthScore`, `retentionScore`, or `combinedRetentionRate`.

The policy permits factual evidence and a future, runtime-resolved comparison
capability for specific R4 derived fixed-horizon facts. Decision, target,
guardrail, and rollback authority are denied for every R4-A fact.

## Evidence classes

`EvidenceClass` is categorical and has no ordering or numeric strength.

| Class | Meaning |
|---|---|
| `DirectBehaviorObservation` | A new gameplay attempt was observed. |
| `DirectLifecycleObservation` | An eligible lifecycle return was observed. |
| `DirectRemovalObservation` | An app-instance removal event was observed. |
| `BoundedAbsenceObservation` | No qualifying event was observed in an explicit, mature horizon. |
| `CensoredObservation` | The observation window is insufficient. |
| `SourceQualityObservation` | Attribution, conflict, or source-quality information. |

`SubjectLevel` is one of `Anchor`, `Profile`, `AppInstance`, or `Source`.
Unmapped and ambiguous uninstall observations are factual only at `Source`
level. They do not become Profile facts.

## Runtime authority resolution

Each dimension resolves to `Allowed` or `Denied`. A denied dimension always
contains stable reason codes. Runtime output never retains a `Conditional`
state.

```text
factual
comparison
decision
target
guardrail
rollback
```

Missing or unknown input fails closed. Comparison is denied for a missing
horizon or grace, an absent or invalid source digest, unknown compatibility,
insufficient maturity, incomplete source coverage, provisional input, an
unsupported fact, or an unmet cohort sample/censoring constraint.

The static metric registry remains the maximum source authority. R4-A does not
change the current B-7, B-8, R3-B, or R3-D registry entries. The only facts that
can resolve comparison authority in this policy are future R4-derived,
fixed-horizon B-7/B-8 facts. Their source metrics remain factual-only.

## Authority matrix

`DerivedMatureFixedHorizonOnly` means R4-B must create a new derived fact and
pass every runtime gate. It does not grant comparison authority to the existing
source metric ID.

| Fact | Subject | Factual | Comparison capability | Decision | Target | Guardrail | Rollback |
|---|---|---|---|---|---|---|---|
| B-7 Returned | Anchor | Allowed | DerivedMatureFixedHorizonOnly | Denied | Denied | Denied | Denied |
| B-7 mature NoObservedReturn | Anchor | Allowed | DerivedMatureFixedHorizonOnly | Denied | Denied | Denied | Denied |
| B-7 RightCensored | Anchor | Allowed | Denied | Denied | Denied | Denied | Denied |
| B-8 Returned | Anchor | Allowed | DerivedMatureFixedHorizonOnly | Denied | Denied | Denied | Denied |
| B-8 mature NoObservedReturn | Anchor | Allowed | DerivedMatureFixedHorizonOnly | Denied | Denied | Denied | Denied |
| B-8 RightCensored | Anchor | Allowed | Denied | Denied | Denied | Denied | Denied |
| R3-D finalized mapped ObservedUninstall | Profile | Allowed | Denied | Denied | Denied | Denied | Denied |
| R3-D Unmapped | Source | Allowed | Denied | Denied | Denied | Denied | Denied |
| R3-D Ambiguous | Source | Allowed | Denied | Denied | Denied | Denied | Denied |
| R3-D provisional ObservedUninstall | AppInstance | Allowed, provisional | Denied | Denied | Denied | Denied | Denied |
| No app_remove observed in covered source | Source | Allowed | Denied | Denied | Denied | Denied | Denied |

R3-D `mappedRate` remains an attribution coverage rate. It is not an uninstall
incidence rate. R4-A therefore grants no R3-D comparison capability.

## Horizon and maturity

Factual-only mode permits both horizon fields to be null. Comparison requires
the complete pair:

```text
horizonDays > 0
sourceUploadGraceHours >= 0
```

A partial pair is invalid for maturity and resolves comparison to `Denied`.
There is no default horizon.

An anchor is mature only when:

```text
anchorEndUtc + horizon + sourceUploadGrace <= analysisAsOfUtc
```

and complete source coverage is explicitly true. Right-censored anchors never
become negative/non-return comparison observations.

A comparison plan must explicitly supply:

- a common horizon and upload grace;
- `minimumMatureAnchorsPerCohort`;
- `maximumCensoringRate`;
- observed mature-anchor counts and censoring rates for both cohorts;
- backend, environment, content/release, horizon, grace, and source-cut
  compatibility decisions;
- immutable baseline and candidate source cuts.

No numeric defaults exist. Missing constraints deny comparison authority.

## Source finalization and source cuts

`SourceFinalizationState` is `Final` or `Provisional`.

- GA daily is `Final` under the R3-B/R3-D source-selection contract.
- GA intraday is `Provisional`.
- Provisional observations remain factual but cannot be compared.

A source cut contains an artifact identity, a lowercase SHA-256 digest,
finalization state, and analysis as-of time. `analysisAsOfUtc` alone is not an
immutable snapshot. Two cuts with the same as-of and different digests are
different cuts. Missing, malformed, or provisional cuts deny comparison.

`Final` describes the selected immutable artifact contract. It does not invent
global source-complete-through authority for custom telemetry.

## Event ledger and comparison horizon

The factual ledger is immutable and is not truncated by a comparison horizon.
The horizon creates a view only.

```text
FinalRun at T0
horizon = 7 days
ObservedUninstall at T0 + 20 days
```

The uninstall is absent from the 7-day comparison view and remains present in
the factual ledger. R4-B must never delete or rewrite the event as unobserved.
Upload grace affects maturity; it does not extend the event occurrence window.

## Censoring and absence

`NoObservedReturn` means no qualifying event was observed in the stated source
cut and horizon. It does not mean a return never occurred. `RightCensored`
means the horizon cannot yet be evaluated and cannot be used as a negative
outcome.

## Sequence semantics

R4-A performs no sequence aggregation. It records that these relations are not
telemetry conflicts:

- ObservedUninstall then later return;
- return then ObservedUninstall;
- B-7 NewAttempt without a B-8 lifecycle return;
- equal occurrence times from different sources.

B-7 Returned does not imply B-8 Returned. A new attempt can begin while the app
remains continuously foregrounded. Equal occurrence timestamps receive no
ingestion-order tie break.

## Uninstall limitations

An app_remove observation does not establish permanent player loss, account
deletion, user intent, dissatisfaction, purchase refusal, or current install
state. A later return does not erase the earlier event. Latest-prior temporal
attribution does not make a Profile the permanent owner of an app instance, and
P2 attribution does not prove P2 caused the removal.

The policy prohibits these interpretations:

- app_remove as a permanent-loss state;
- NoObservedReturn as a permanent-loss state;
- RightCensored as non-return;
- later return deleting removal history;
- latest Profile permanently owning later events;
- P2 attribution proving causation;
- R3-D `mappedRate` as uninstall incidence.

R4 v1 identifiers and states do not use `Churned`, `Lost`, `Retained`, or
`Abandoned`. Restriction prose may explain that an observation does not imply
churn.

## Serialization and validation

`policy_contract()` returns the versioned contract. `serialize_policy_contract()`
uses deterministic sorted-key JSON. Enum/value mismatches are rejected or fail
closed. Run the local validator with:

```powershell
defence-analytics validate-r4a-contracts
```

The validator does not load ADC or create a BigQuery client. It checks policy
identity/version, enum surfaces, matrix invariants, closed authority dimensions,
identifier restrictions, censoring and provisional rules, and unknown-fact
fail-closed behavior.

## R4-B handoff

R4-B consumes these public R4-A contracts:

```text
EvidenceClass
SubjectLevel
RetentionEvidenceFactKind
SourceFinalizationState
HorizonContract
SourceCut
ComparisonPlan
AuthorityContext
AuthorityResolution
resolve_authority(...)
RetentionEvidenceEvent
RetentionEventLedger
```

R4-B must provide immutable normalized B-7/B-8 input artifacts because their
normal bundles are aggregate-only. It may consume the existing restricted R3-D
normalized event artifact. Raw identities remain internal/restricted and never
appear in normal R4 output.

R4-B must call `resolve_authority` after computing maturity and source
compatibility. It must not infer comparison permission from evidence class,
source domain, or signal direction alone. It must register any future derived
fixed-horizon metric under a new metric ID; R4-A reserves no registry entry.

## R4-C boundary

R4-A does not add `retentionEvidence` to C-1 or C-2, change provider prompts, or
alter decision/target validation. R4-C will own that integration after R4-B has
stable derived output and privacy contracts.
