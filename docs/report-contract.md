# Common Analytics Report Contract v1

## Versioning

Every `metadata.json` contains `reportContractVersion: "1.0.0"` and an analysis-specific `analysisVersion`. A breaking field, meaning, denominator, population, or file-layout change requires a major version change. Additive backward-compatible fields require a minor version change; clarifications and renderer fixes may use a patch version.

## Bundle layout

```text
reports/generated/
└─ <analysis-type>/
   └─ <environment>__<stage-slug>__cv-<version>__<scope-hash8>/
      ├─ report.md
      ├─ metrics.json
      ├─ metadata.json
      └─ tables/
         └─ *.csv
```

The eight-character suffix is the beginning of the SHA-256 digest of canonical scope JSON. The scope includes environment, stage, content version, every optional release filter, development-build filter, and UTC upload-time bounds. This makes reports with different effective populations distinct.

Existing targets are not overwritten by default. Explicit overwrite is allowed only when the existing metadata has the same analysis type and exact scope. Installation is performed through a sibling temporary directory.

## JSON

- External field names use `camelCase`.
- Dataclass field order defines canonical key order; scope hashing separately sorts keys.
- Files are UTF-8, indented by two spaces, and end with LF.
- `NaN` and pandas missing values become JSON `null`; positive or negative infinity is rejected.
- UTC timestamps have second precision and end in `Z`.
- Every ratio uses `{ "count", "denominator", "ratio" }`; `ratio` is `null` when the denominator is zero.
- `metrics.json` contains aggregate analysis results. `metadata.json` contains scope, sample, quality, definitions, warnings, generation time, and dry-run estimated bytes.

## CSV

CSV files are the canonical full breakdowns. Headers and sort keys are fixed per analysis. Numeric floats use up to 12 significant digits; files are UTF-8 with LF. A supported table with no rows is emitted as a header-only file. CSV rows contain aggregates only and never player, attempt, run, or upload identifiers.

Stage Difficulty provides:

- death time buckets and phase/floor/wave/zone distributions;
- final death source, enemy, and available environment-effect distributions;
- incoming enemy damage and damage-source distributions;
- Dead/Clear threat summaries;
- aggregate selected player-state distributions.

Weapon Performance provides:

- adoption and final ownership by stable weapon family;
- resume-aware combat contribution with recomputed attempt damage share;
- valid-sample DPS/uptime and invalid-reason aggregates;
- BossStarted-scoped boss performance;
- observational combat-observed and final-owned outcome associations;
- exact final-state and effective-level distributions.

Upgrade Choice provides:

- reconstructed complete candidate exposures and linked pick rates;
- candidate-position and presented-count breakdowns;
- exposure-time snapshot/transition context with a 30-second snapshot limit;
- canonical unordered co-exposure pairs with third-candidate and unresolved selections explicit;
- observational selected versus exposed-not-selected final-attempt associations;
- an explicit partition of exposed-not-selected attempts into alternative-selected and no-selection-only attempts.

Its scope extends the common B-1 scope only for `weaponPerformance` with gameplay cohort bounds and `analysisAsOfUtc`. Existing Stage Overview and Stage Difficulty scope JSON and hashes are unchanged.

`upgradeChoice` uses its own `UpgradeAnalysisScope` with the same gameplay cohort, ingestion, release, and as-of fields. It does not alter the scope JSON or hash of any existing analysis.

`progressionNextRun` uses a stage-less `ProgressionAnalysisScope`. Its path substitutes `all-stages` for the stage slug, while its scope records optional previous/next association-stage filters, progression journey and ingestion bounds, the resolved `analysisAsOfUtc`, and both max-gap values. Existing scope JSON and hashes are unchanged.

`postRunBehavior` uses an optional-stage `PostRunAnalysisScope`. Its scope records anchor outcome,
release, final-run wall-clock and ingestion filters, the resolved `analysisAsOfUtc`, and the
post-run max-gap. Supporting sources use the as-of upper bound so the anchor ingestion interval
does not sever valid post-run linkage. Existing analysis scope JSON and hashes are unchanged.

## Markdown

`report.md` is a human/LLM-readable index of scope, sample, quality, outcome, timing, concentration, causes, damage, threat, state, deterministic statistical signals, caveats, and attached tables. Percentages and seconds display at one decimal place. It must not contain tuning judgments or recommendations.

Signals are emitted only for fixed descriptive thresholds:

- a concentration share of at least 50% when its denominator reaches the configured death threshold;
- a final lethal enemy share of at least 20% with at least five deaths;
- an incoming enemy damage share of at least 20% across at least five affected runs.

## Privacy and scope

Generated reports contain aggregates only. They do not store `telemetryPlayerId`, `attemptId`, `runId`, or `uploadId`. Queries use ADC and read-only BigQuery access. The contract does not authorize cloud mutation, raw object access, credential creation, or telemetry upload secrets.

Weapon artifacts may contain stable content identifiers (`weaponFamilyId`, `weaponId`, and `weaponType`) but never runtime weapon `instanceId`. `combatObservedSegments` has observation unit `eligibleGameplaySegment`; it must not be renamed or interpreted as run/attempt count. `combatObservedAttempts` is the separately deduplicated final-attempt metric.

`dpsCoverageAmongCombatObservedInstanceSegments` always has:

```json
{
  "count": 2,
  "denominator": 3,
  "ratio": 0.6666666666666666
}
```

The count is combat-observed instance-segments with at least one valid DPS sample. The denominator is positive-damage combat-observed instance-segments. It does not measure all owned, equipped, or used weapons.

Weapon present/absent results are descriptive associations and `outcomeAssociationIsCausal` is always `false`. Combat-observed cohorts are conditioned on positive applied damage; acquisition timing and survivorship can therefore affect both elapsed-time and outcome differences. Elapsed-time differences are not emitted as deterministic signals.

## Upgrade Choice contract

- Canonical candidate identity is `category + upgradeId`; NewWeapon, UpgradeWeapon, Global, Reward, and Evolution remain distinct.
- `upgradeSelectionCount` is segment-local in the pinned telemetry source. Completeness compares it only with deduplicated selection rows having the same environment and gameplay-segment key.
- Pick rate is linked candidate selections divided by complete exposures containing that candidate. A presentation closed without a selection remains in the denominator.
- Pair rows are canonical unordered co-exposures. Overall share uses every complete co-exposure; conditional preference uses only exposures selecting one of the two pair members.
- Context is based on exposure time. A preceding player snapshot older than 30 seconds is stale and is not used for player-state values; transition fallback is approximate.
- `alternativeSelectedAttempts` and `noSelectionOnlyAttempts` are mutually exclusive and sum to `exposedNotSelectedAttempts`. If an attempt has both a no-selection exposure and another exposure selecting an alternative, it belongs to `alternativeSelectedAttempts`.
- Selection/outcome results set `selectionOutcomeAssociationIsCausal` to `false`. Pick-rate, position, context, pairwise, and outcome differences do not imply a tuning conclusion.

Upgrade artifacts may contain stable candidate, category, upgrade, weapon-family, and grant identifiers. They never contain player, attempt, gameplay-segment, upload, exposure, or runtime instance identifiers.

## Progression Next-Run contract

- `progressionObservationUnit` is a deduplicated committed progression event. Known kinds are `WeaponRecipe`, `EvolutionChoice`, `StatReset`, and `EvolutionApplyOnly`; other values are retained as `Unrecognized:<value>`.
- Structural episode boundaries are the nearest previous final attempt and nearest next new gameplay attempt. These are resolved independently of max-gap windows. Max-gap values affect immediate context, engagement, and right-censoring only.
- Both-boundary, previous-only, and next-only events can form bounded episodes. Events with neither boundary remain in activity and receive no episode key, preventing unrelated long-lived player activity from being merged.
- A next new attempt requires gameplay `segmentIndex=1` and a non-resume run-start snapshot. Resume continuations and lifecycle terminal rows are skipped; open-attempt overlap excludes paired results.
- `nextRunObservationSemantics` is fixed to `Observed in telemetry uploaded by analysisAsOfUtc.` An as-of report cannot observe offline or not-yet-uploaded gameplay.
- Right-censored episodes are excluded from mature next-run-rate and no-next denominators. `noNextRunWithinWindow` is an observation-window classification, not churn.
- Primary paired performance requires same-stage, same-content previous and next final attempts, immediate previous/next eligibility, and no open-attempt overlap. Clear rate remains `Clear / (Clear + Dead)`.
- Multi-progression episodes preserve co-occurrence context and do not isolate any one progression. Paired outcomes and elapsed differences are descriptive; both causality flags are `false`.

Progression CSVs contain aggregate activity, exact state transitions, episode composition, kind co-occurrence, next-run engagement, outcome transitions, paired elapsed summaries, and data quality. Stable progression kind/target/state values are allowed; player, attempt, run, event, transaction, and batch identifiers are forbidden.

## Post-Run Behavior contract

- A post-run window is anchored by one row from `telemetry_attempt_outcomes_v1`. It ends at the earlier of the next new gameplay attempt or the configured max-gap. Resume continuations and lifecycle terminal rows are not next attempts.
- `shopPresentedWindows` counts mature windows with an observed Shop tab or Shop section presentation. `shopUserNavigatedWindows` counts only `TabViewed(tab='Shop', navigationSource='User')`. Shop section presentation never implies user navigation or a direct section click.
- Initial and programmatic navigation remain observed presentation but are excluded from user-navigation and high-level user-action metrics.
- `observedAttemptSuccessRate` has observation unit `observedCommerceAttemptOperation`: its count is observed commerce Attempts linked to Succeeded and its denominator is all observed commerce Attempts.
- `committedSuccessWindowRate` has observation unit `maturePostRunWindow`: its count is mature windows with at least one durable commerce Succeeded Result and its denominator is the relevant mature-window cohort.
- A durable Succeeded Result without an observed best-effort Attempt remains committed presence. Attempt availability does not change the durable Result fact and is reported separately in data quality.
- `Duplicate` is not a new committed success. Fun Feedback Uranium rewards, progression spends, RandomBox spends, and other system rewards are excluded from commerce.
- Feedback cohorts are `NotExposed`, `ExposedNoResponse`, `PositiveResponse`, and `NegativeResponse`; feedback eligibility/cadence and response self-selection make comparisons non-causal.
- Right-censored windows are excluded from mature absence and no-next denominators. Next-run observation is based on telemetry uploaded by `analysisAsOfUtc`; no-next is not churn.
- Lobby, shop, IAP, and transaction Attempt telemetry is best-effort. Event absence does not prove behavior absence.
- Action transitions are aggregate consecutive high-level user-action pairs, not player journeys, sessions, clickstream paths, or a Markov model.

Post-run CSVs contain aggregate navigation, feedback, shop/offer, commerce, progression, next-run,
first-action, transition, and quality breakdowns. Player, attempt, run, event, operation,
presentation, batch, and upload identifiers are forbidden.

## ContentVersion Comparison contract

`contentVersionCompare` uses `analysisVersion: "1.0.0"` and keeps the baseline/candidate order
provided by the caller. All deltas are candidate minus baseline. Its scope contains both versions,
the selected domains, common filters, resolved cutoff, and all window definitions. The path uses
`cv-<baseline>-vs-cv-<candidate>` and therefore also preserves comparison direction.

Source analyses are assembled in memory only. B-6 does not write source report bundles or
intermediate artifacts; the final comparison bundle is the only filesystem write. Metadata embeds
an aggregate-only manifest for every source side with its type/version, scope, sample, quality,
definitions, warnings, cohort profile, and snapshot mode.

Snapshot modes are explicit:

- Stage Difficulty uses `uploadedAtUtcUpperBound`. It is an ingestion cutoff and does not provide a BigQuery historical system-time snapshot.
- Weapon, Upgrade, Progression, and Post-Run use `analysisAsOfUtcParameter`. This is also a telemetry-row filter contract, not `FOR SYSTEM_TIME AS OF`.

Each comparison row separates `status` from `warningCodes`. Status is the coarse comparability
state `Comparable`, `Limited`, `Unavailable`, or `Incompatible`. Warning codes are independent and
all applicable codes are preserved in stable priority order; sample, coverage, source, missing,
and compatibility warnings do not replace one another.

Ratio comparison preserves count, denominator, and ratio for both sides. Percentage-point delta is
`100 * (candidateRatio - baselineRatio)`. Relative delta is null when the baseline is zero or
missing. Scalar delta is candidate minus baseline. Missing never means zero: open content identities
observed on only one side remain null on the other side and are not labeled as added or removed.

Comparison CSV files are long-form aggregate tables for Stage, Weapon, Upgrade, Progression, and
Post-Run, plus comparison data quality. Stable content identifiers are allowed, but raw player,
attempt, run, event, operation, batch, upload, presentation, exposure, or runtime instance IDs are
forbidden. The generator emits only descriptive direction and never assigns a tuning judgment.
