# Phase R3-A source boundary

R3-A introduces a raw `telemetry_app_lifecycle_events` source and two nullable
run-start fields: `appProcessSessionId` and
`latestForegroundOccurrenceId`. These are ingestion/linkage fields, not report
identifiers.

`ColdStart` and `ForegroundResume` are Unity runtime lifecycle observations.
They do not establish `AppReturnObserved`, retention, revisit, session intent,
or uninstall state. B-7 Run Retention continues to use `NewAttempt` and does
not read the R3-A lifecycle table. B-8 will own any future app-return
classification.

```text
returnDefinition = NewAttempt
```

Privacy boundaries:

- `retentionBridgeId` exists only in the canonical save/cloud profile and as
  Firebase Analytics `user_id`; it is prohibited from custom telemetry.
- `telemetryPlayerId`, `appProcessSessionId`, and `lifecycleOccurrenceId` may
  exist in the raw BigQuery layer according to the R3-A schema.
- The process and occurrence identifiers must not be emitted by aggregate
  tables, generated reports, diagnostics, C-1 evidence, or C-2 context.

R3-A does not change the B-7, C-1, C-2, or C-2A contract versions and does not
grant comparison, evidence-selection, decision, or target authority to app
lifecycle fields.
