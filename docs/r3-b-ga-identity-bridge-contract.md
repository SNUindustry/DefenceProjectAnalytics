# R3-B GA Temporal Identity Bridge Contract

Version: `1.0.0`

R3-B records a factual temporal relationship between a GA app-instance identity
and a canonical game profile. It does not establish permanent ownership, a
real-world person, future identity, churn, or uninstall.

## Sources

- GA: `bald-ops.analytics_538301722`, property `538301722`, Android stream
  `14908341790`.
- Custom Production telemetry:
  `bald-ops.game_telemetry.telemetry_app_lifecycle_events` with
  `environment = Production`.
- Test custom telemetry has no GA source. A Test live run fails with
  `GA_SOURCE_UNAVAILABLE_FOR_TEST`; deterministic fixtures remain supported.

The physical telemetry backend and GA project/property/stream are independent,
explicit source dimensions. Selecting `--backend production` never infers or
changes the GA source identity.

The v1 authoritative observation window starts at the confirmed final stream
selection submission, `2026-09-28T11:10:05Z`. Earlier events that happen to be
present in the first daily table are outside the bridge population. The CLI
records this bound explicitly as `--observation-start`.

## Daily and intraday selection

For each requested GA source date, a finalized `events_YYYYMMDD` table is
authoritative when present. Otherwise, `events_intraday_YYYYMMDD` is selected as
provisional. The two tables for one date are never unioned. Queries contain only
the explicitly resolved table names and never scan an unrestricted `events_*`
wildcard.

## Exact observation bridge

Only `app_foreground` is a mapping observation. The exact join is:

```text
GA lifecycle_occurrence_id = custom lifecycleOccurrenceId
```

Nearest timestamps and ingestion order are not identity authority. Valid mapped
observations require the configured stream, Android platform, Production
environment, valid 32-character lowercase hexadecimal bridge, player, and
occurrence identifiers, a conflict-free custom occurrence, canonical profile
attribution, a non-development custom row, and matching return kind,
content version, and release ID.

Missing and invalid rows remain visible as aggregate quality statuses. Conflicts
never choose an arbitrary winner.

## Deduplication and conflicts

The GA event key is event timestamp, event name, app-instance identity, and
lifecycle occurrence ID. Identical payload repetitions collapse. Different
payloads under one event key are `GaConflict`.

Custom lifecycle occurrences follow the source lifecycle contract: exact
payload repetitions excluding upload time collapse, and different payloads
under one environment/occurrence key are `CustomConflict`.

Two distinct canonical profile pairs observed for one app-instance identity at
the same timestamp are `TemporalMappingConflict`. A bridge mapping to multiple
durable player IDs, or a durable player ID mapping to multiple bridges, is a
`DurableIdentityConflict`.

## Temporal intervals

Mapped observations are ordered by observation time. Consecutive observations
of the same durable profile pair reinforce one interval:

```text
validFromUtc = first observation in the interval
validUntilUtc = next different valid mapping observation for the same pseudo ID
```

The final interval is open-ended. A P1 → P2 → P1 sequence produces three
intervals. A reinstall may produce a new pseudo ID for the same durable profile
pair and is not a conflict.

R3-D resolves an `app_remove` by selecting the latest unambiguous interval for
the same pseudo ID whose `validFromUtc` is at or before the uninstall event and
whose `validUntilUtc` is later than the event or null. The outcome is `Mapped`,
`Unmapped`, or `Ambiguous`. Upload order and `app_remove.user_id` are not used.

## Privacy and authority

Raw player, bridge, GA pseudo, and lifecycle occurrence identifiers are allowed
only inside the query and the separately written restricted mapping artifact.
They are forbidden from the normal report bundle, C-1 evidence, C-2 context,
and public analysis.

All R3-B registered metrics are readable factual evidence. Comparison,
decision, and target/guardrail authority are false until a later R4 promotion.
