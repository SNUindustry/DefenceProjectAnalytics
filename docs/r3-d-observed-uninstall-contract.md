# R3-D Observed Uninstall Contract

Version: `1.0.0`

R3-D records a factual GA `app_remove` event and, when possible, attributes it to the
canonical Profile observed by the R3-B temporal identity bridge. An observation is an event,
not a current uninstall state, permanent churn, abandonment, or proof of dissatisfaction.

## Sources and availability

- GA project/property/stream: `bald-ops` / `538301722` / `14908341790`.
- GA dataset: `bald-ops.analytics_538301722` in `asia-northeast3`.
- R3-B dependency: `gaIdentityBridge` `1.0.0`, backed by Production canonical lifecycle
  telemetry.
- The authoritative observation window begins at `2026-09-28T11:10:05Z`, when final GA
  stream selection was confirmed. No historical export backfill is assumed.

For one source date, finalized `events_YYYYMMDD` replaces
`events_intraday_YYYYMMDD`. Intraday is used only while daily is absent and is marked
provisional. Queries resolve explicit table names, apply date bounds and dry-run cost checks,
and never scan an unrestricted wildcard. Test has no GA source and fails with
`GA_SOURCE_UNAVAILABLE_FOR_TEST`.

## Event normalization

Only `event_name = 'app_remove'` is included. `event_timestamp` is normalized to a UTC instant;
`event_date` is source partition metadata and has no temporal authority.

The deterministic event key is:

```text
event_timestamp
event_name
user_pseudo_id
event_bundle_sequence_id
batch_event_index
batch_ordering_id
```

The remaining used GA context, including the original occurrence timestamp, server offset,
app version, stream, platform, exported user identity, and event parameters, forms the payload.
Identical payload repetitions collapse into one logical event. Multiple payload variants for one
event key produce `GaDuplicateConflict`; no variant is selected. Daily/intraday overlap is already
removed by source selection.

The exported GA user identity may be null and is never attribution authority.

## Temporal attribution

The only primary attribution input is:

```text
app_remove.user_pseudo_id + app_remove.event_timestamp
```

R3-D consumes normalized R3-B observations for the same explicit source window. It considers
bridge observations for the same app-instance identity at or before the event timestamp. A single
unambiguous mapping at exactly the same microsecond is eligible. Future observations are never
used.

Among eligible bridge points, the latest timestamp is authoritative:

- one canonical pair at the latest point: `Mapped`;
- no prior bridge point: `Unmapped / UnmappedNoPriorMapping`;
- missing app-instance identity: `Unmapped / UnmappedMissingPseudoId`;
- a conflict at the latest point or multiple canonical pairs there:
  `Ambiguous / TemporalMappingConflict`;
- a conflicting GA uninstall event: `Ambiguous / GaDuplicateConflict`;
- an invalid timestamp, stream, platform, or malformed app-instance identity:
  `Invalid / InvalidGaEvent`.

R3-D never falls back from a latest conflicting point to an older clean mapping. BigQuery
ingestion order, table write time, file order, bundle generation order, nearest timestamp, and
the exported GA user identity have no authority.

Sequential P1 → P2 and P1 → P2 → P1 mappings are normal account-switch timelines. The latest
prior point wins. A reinstall can create pseudo B for the same durable Profile after pseudo A's
uninstall. The earlier uninstall remains a factual event for pseudo A. Multiple uninstall events
may map to one Profile.

## Mapping age

For mapped events:

```text
mappingAgeSeconds = eventTimestampUtc - bridgeObservedAtUtc
```

The value uses timestamp microsecond precision and is nonnegative. Version 1.0.0 has no maximum
age threshold: five seconds, one day, and sixty days remain eligible if they are the latest
unambiguous prior observations. Age is provenance and quality information only.

## Artifacts and privacy

The normal bundle contains aggregate metrics and these tables:

```text
source-summary.csv
attribution-summary.csv
mapping-age-summary.csv
```

Raw app-instance, player, bridge, exported user, and lifecycle occurrence identifiers are
forbidden from the normal bundle, C-1 evidence, and C-2 context. Restricted normalized events are
written separately below `reports/restricted/observed-uninstall-events/<scope-hash>/` with an
SHA-256 manifest. Mapped rows retain the canonical player identity internally for R4; other rows
do not invent one.

Both normal and restricted artifacts are immutable, scope-addressed, atomically installed, and
manifested. Aggregate distinct counts do not expose the underlying identifiers; this repository
has no additional small-cell suppression convention for internal structural reports.

## Metrics and authority

Stable metrics include source event counts, mapped/unmapped/ambiguous attribution counts and
rates, specific failure reasons, distinct mapped Profile and app-instance counts, and mapping-age
P50/P95. All registered R3-D metrics are readable factual evidence. Comparison, decision, and
target/guardrail authority are false.

R3-D does not add churn, lost-player, retention-failure, permanent-uninstall, negative scoring,
or causal metrics. It does not modify B-7 NewAttempt, B-8 ObservedAppReturn, or R3-B bridge
semantics.

## R4 handoff

R4 may consume the restricted normalized event contract:

```text
eventTimestampUtc
attributionStatus and attributionReason
canonical telemetry profile identity when Mapped
bridgeObservedAtUtc
mappingAgeSeconds
sourceFinalizationState
```

Raw GA app-instance and bridge identifiers remain infrastructure and are not normal decision
inputs. R4 must separately decide whether and how an observed uninstall contributes to a later
interpretation. R3-D supplies only the observation and its temporal attribution.
