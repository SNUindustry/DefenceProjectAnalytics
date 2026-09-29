# R3-A → B-8 handoff

Status: READY. Date: 2026-09-22. Unity evidence and final deployment verdict are maintained in
`../DefenceProject/docs/r3-a-final-closure-report.md`.

## Source contract

Read `game_telemetry.telemetry_app_lifecycle_events` in the explicitly selected
physical backend: `bald-ops-test` / `Test` or `bald-ops` / `Production`, both
`asia-northeast3`. Do not union the two populations implicitly. The game content
release channel is independent of the custom telemetry environment; Development
device acceptance used Production content 129 with Test telemetry.

Long-term raw correlation uses `telemetryPlayerId`. `retentionBridgeId` remains
in canonical Save/Cloud and GA `user_id`; it is not a custom lifecycle field.
Account UID, receipt, purchase token and transport owner correlation are not
analytics payloads. Preserve the output restrictions in
[r3-a-source-boundary.md](r3-a-source-boundary.md).

| Raw field | Meaning |
|---|---|
| `returnKind=ColdStart` | New observed application process |
| `returnKind=ForegroundResume` | Observed pause(true) → pause(false) |
| `appProcessSessionId` | Process correlation, changes on restart |
| `lifecycleOccurrenceId` | Stable occurrence key, retained through delivery retry |
| `occurredAtUtc` | Observation time, not ingestion time |
| `previousBackgroundAtUtc`, `backgroundDurationSeconds`, `timingQuality` | Background timing facts and quality |
| `telemetryPlayerId` | Durable canonical profile's custom telemetry identity |
| `environment`, `contentVersion` and release fields | Separate telemetry routing and content cohort facts |
| `capturedBeforeProfileAdmission`, `profileAttributionStatus` | Capture/admission context |
| `analyticsMeasurementAvailable`, `bridgeApplied` | GA availability facts; false in Test policy |

Run-start source `telemetry_run_start_snapshot` adds nullable
`appProcessSessionId` and `latestForegroundOccurrenceId`. Match within the same
environment and durable player identity. Historical nulls mean unavailable
linkage; do not invent a join from ingestion order. A new process can inherit a
durable queued event from an old process; preserve each event's captured IDs.
An account transition can change the admitted player within one process.

## Interpretation and validation

Focus regain alone produces no resume. Neither lifecycle kind proves a meaningful
human return. B-8 owns app-return eligibility, deduplication, windowing, and
classification. B-7 continues to define return as `NewAttempt` and must not change
implicitly. Deduplicate lifecycle observations by environment and occurrence ID;
retain detection of conflicting identity/payloads rather than hiding conflicts.

Use observation timestamps for sequencing and ingestion timestamps for delivery
latency. The device acceptance covered cold start, multiple resumes, restart,
focus-only neutrality, durable delayed delivery, run linkage, account switch and
failed-transition reconciliation with archive/BQ assertions. Owner generations
are enforced locally and in authenticated transport, not exposed as BQ columns.

IAP `correlationHash` identifies a server settlement observation domain. One
`ServerRewardCommitted` per operation must remain distinct from `Pending`,
`RedeliveryObserved`, `ConsumePending`, and `ConsumeCompleted`. Lobby batch IDs
are not gameplay run IDs. Box presentation replay does not represent a new grant.

Exclude the acceptance QA Development-build cohort from normal Production player
populations (`isDevelopmentBuild = true`; exact smoke occurrence is retained only
in the Unity ignored evidence). Production smoke used the real device and normal
Profile login on APK 446. No synthetic Production event was uploaded. Test QA
purchases are free license-test orders. The first pre-fix IAP cohort had incorrect
observation time; it remains in Test as failure evidence and must not be treated
as the corrected acceptance cohort.

Deployed revisions: Test `uploadtelemetry-00003-jih`; Production
`uploadtelemetry-00010-dav`. Both use dedicated telemetry runtime identities;
Production lifecycle schema and nullable run-start linkage fields are present.
Device/archive/BQ isolation checks passed in both directions.

## Delivery boundary

The handoff supplies raw facts and verified source behavior. This document does
not implement B-8 classification, change existing report contract versions, or
authorize raw identifiers in generated aggregate reports.
