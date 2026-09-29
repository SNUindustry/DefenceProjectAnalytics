# B-8 Observed App Return v1.0.0

B-8 measures the first valid lifecycle observation after a canonical final attempt
for the same backend, environment, and durable telemetry player identity. Its
`returnDefinition` is `ObservedAppReturn`; B-7 remains `NewAttempt`.
Neither result establishes human intent, churn, or uninstall.

Run with an explicit backend and as-of cutoff:

```powershell
defence-analytics --backend test validate-b8-contracts
defence-analytics --backend test observed-app-return --environment Test --content-version 129 --as-of-utc 2026-09-22T12:00:00Z --dry-run
defence-analytics --backend test observed-app-return --environment Test --content-version 129 --as-of-utc 2026-09-22T12:00:00Z
```

The selected anchor uses B-7's canonical `isAttemptFinal` ordering. B-8 further
requires a valid same-player lifecycle occurrence at or before the anchor end;
otherwise the anchor is ineligible rather than censored. A ColdStart must be a
new observed process after the anchor. A ForegroundResume must have valid
background timing, and both its pause and resume must be after the anchor.
There is no minimum background duration. Conflicting occurrence payloads are
excluded as a group. Only `occurredAtUtc` orders returns; upload time governs
as-of availability.

Production requires `isDevelopmentBuild IS FALSE` for anchors and lifecycle
rows. Test results are QA/Test observations. With no observed return, an
eligible anchor is right-censored, not classified as churn. Optional
`--threshold-days` requires `--source-upload-grace-hours`; grace delays
the mature no-observation classification but does not prove source completion.

The aggregate bundle contains metadata, metrics, overall/outcome/stage
summaries, latency and quality summaries, a factual report, and an SHA-256
manifest. Raw player, run, process, and occurrence identifiers never appear
in the bundle or C-1 evidence. C-1 accepts B-8 as an optional factual
domain; comparison, decision, and target authority remain disabled.
