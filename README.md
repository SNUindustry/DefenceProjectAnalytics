# DefenceProjectAnalytics

DefenceProject telemetry handoff, copied BigQuery schemas, and canonical SQL are the source of truth for this read-only analytics project. Phase B-4 adds aggregate Progression Next-Run analysis while retaining the Phase A foundation and the existing Stage Difficulty, Weapon Performance, and Upgrade Choice reports.

No command in this repository creates or changes cloud resources. Queries are restricted to `SELECT`/`WITH`; service-account keys, telemetry upload secrets, raw bucket access, full local telemetry dumps, dashboards, and balancing recommendations are outside scope.

Defaults are GCP project `bald-ops`, dataset `game_telemetry`, and location `asia-northeast3`. Override them with global CLI options or `DPA_GCP_PROJECT`, `DPA_BIGQUERY_DATASET`, and `DPA_BIGQUERY_LOCATION`.

## 1. ADC authentication

Install the Google Cloud CLI, then create Application Default Credentials (ADC). Browser consent may show multiple Google Auth Library permissions; ADC needs the Google Cloud data access permission. The Cloud SQL permission is not used by this repository.

```powershell
gcloud auth application-default login
gcloud config set project bald-ops
```

`gcloud auth login` alone is not a replacement for client-library ADC. If ADC is absent, the CLI prints a diagnostic containing the command above. The expected least-privilege access is BigQuery job execution on the project and data viewing on the dataset.

## 2. Python installation

The project supports Python 3.11+ and currently runs on Python 3.11.9.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

Runtime dependencies remain `google-cloud-bigquery`, `db-dtypes`, `pandas`, `pyarrow`, `matplotlib`, and `jupyter`; tests use `pytest`. Phase B adds no dependency.

## 3. Contract validation

Parse the copied JSON contracts and validate required live objects and analytics-critical columns. Legacy nullable-mode differences do not fail validation by themselves.

```powershell
defence-analytics validate-contracts
defence-analytics connection-smoke
```

## 4. Stage Overview

The existing API and default CLI JSON are unchanged. `contentVersion` remains optional for an ordinary overview.

```powershell
defence-analytics stage-overview --stage-key stage1 --environment Production
defence-analytics stage-overview --stage-key stage1 --environment Test --content-version 2
```

To opt into the common three-file bundle, provide an output directory. Bundle generation requires `contentVersion`.

```powershell
defence-analytics stage-overview --stage-key stage1 --environment Test --content-version 2 --output-root reports/generated
```

## 5. Stage Difficulty report

All three primary scope values are required and exact-match filters. Test and Production can never be mixed in one report.

```powershell
defence-analytics stage-difficulty `
  --environment Test `
  --stage-key stage1 `
  --content-version 2
```

Optional exact filters are `--app-version`, `--release-id`, `--release-channel`, `--release-type`, and `--development-build true|false`. Partition bounds are `--uploaded-start-utc` (inclusive) and `--uploaded-end-utc` (exclusive), both ISO-8601 values with an offset or `Z`.

The command first dry-runs all seven queries. Their combined estimate must not exceed `--maximum-total-bytes`, which defaults to `1,000,000,000` bytes. No analysis query executes when the cap is exceeded. Executed queries also receive a bytes-billed cap.

The same workflow is available as a Python API:

```python
from defence_project_analytics import (
    StageDifficultyRequest,
    analyze_stage_difficulty,
    generate_stage_difficulty_report,
)

request = StageDifficultyRequest(
    environment="Test",
    stage_key="stage1",
    content_version=2,
)
analysis = analyze_stage_difficulty(request)
generated_path = generate_stage_difficulty_report(request)
```

Reports are written under:

```text
reports/generated/<analysis-type>/<environment>__<stage-slug>__cv-<version>__<scope-hash8>/
```

An existing target fails by default. `--overwrite` only replaces a generated report whose `analysisType` and complete canonical scope match; replacement uses a sibling temporary directory.

## 6. Weapon Performance report

Weapon Performance requires an exact environment, stage, and content version. The command captures one `analysisAsOfUtc` at execution start and applies the same exclusive upload upper bound to all seven queries.

```powershell
defence-analytics weapon-performance `
  --environment Test `
  --stage-key stage1 `
  --content-version 2
```

Reproduce a fixed snapshot and optionally constrain gameplay and ingestion time independently:

```powershell
defence-analytics weapon-performance `
  --environment Test `
  --stage-key stage1 `
  --content-version 2 `
  --segment-ended-start-utc 2026-08-01T00:00:00Z `
  --uploaded-end-utc 2026-08-15T00:00:00Z `
  --as-of-utc 2026-08-14T00:00:00Z
```

`segment-ended-*` selects the terminal gameplay cohort; `uploaded-*` limits ingestion partitions. Optional app/release/development filters use the same names and exact-match behavior as Stage Difficulty. The command dry-runs every query first, uses the shared 1GB total default, and prints only the report path, scope, family count, detail-eligible attempts, warning codes, and estimated bytes.

Python API:

```python
from defence_project_analytics import (
    WeaponPerformanceRequest,
    analyze_weapon_performance,
    generate_weapon_performance_report,
)

request = WeaponPerformanceRequest(
    environment="Test",
    stage_key="stage1",
    content_version=2,
)
analysis = analyze_weapon_performance(request)
generated_path = generate_weapon_performance_report(request)
```

### Weapon populations and interpretation

- Attempt-level combat metrics use only fully covered attempts: every gameplay segment must match the requested content/release scope and be telemetry-complete.
- `combatObservedSegments` counts eligible gameplay segments where the weapon produced positive applied damage. `combatObservedAttempts` deduplicates those observations by final attempt. Neither means every ownership or input event was observed.
- Final ownership uses only the final gameplay run and never falls back to an interrupted segment.
- Attempt damage share is recomputed from summed damage across all eligible resume segments; segment shares are not averaged.
- `dpsCoverageAmongCombatObservedInstanceSegments` is valid-sample coverage only among positive-damage instance-segments. It is not coverage of every owned, equipped, or used weapon.
- Boss metrics use only gameplay segments with a `BossStarted` transition.
- Present/absent outcome associations are observational. Combat-observed cohorts are conditioned on positive applied damage, so elapsed differences are especially sensitive to acquisition timing and survivorship.

Existing report targets fail by default. `--overwrite` is accepted only for the same generated analysis type and exact scope, including `analysisAsOfUtc`.

## 7. Upgrade Choice report

Upgrade Choice uses the same required environment, stage, content version, exact release filters, gameplay-time bounds, ingestion-time bounds, and reproducible as-of snapshot as Weapon Performance.

```powershell
defence-analytics upgrade-choice `
  --environment Test `
  --stage-key stage1 `
  --content-version 2
```

Use `--as-of-utc` to reproduce a fixed snapshot. The command dry-runs all six aggregate queries before executing any of them; their combined default cap is 1GB.

Python API:

```python
from defence_project_analytics import (
    UpgradeChoiceRequest,
    analyze_upgrade_choice,
    generate_upgrade_choice_report,
)

request = UpgradeChoiceRequest(
    environment="Test",
    stage_key="stage1",
    content_version=2,
)
analysis = analyze_upgrade_choice(request)
generated_path = generate_upgrade_choice_report(request)
```

### Upgrade populations and interpretation

- An exposure is one displayed upgrade presentation; a candidate exposure is one candidate within it. Candidate row counts are never used as exposure counts.
- A primary pick-rate denominator contains only complete, non-truncated presentations from choice-eligible gameplay segments. A valid no-selection presentation remains in the denominator.
- `run_end upgradeSelectionCount` is segment-local. It is compared only with deduplicated selection rows from the same gameplay-segment key, never with attempt-cumulative or other resume-segment rows.
- A fully choice-covered attempt requires every gameplay segment to match scope, be telemetry-complete, report no upgrade overflow, and pass selection-count/linkage validation.
- Head-to-head rows are unordered co-exposure pairs. Multi-candidate screens keep selections of a third candidate in `otherSelectedCount`.
- The exposed-not-selected outcome cohort is partitioned into `alternativeSelectedAttempts` and `noSelectionOnlyAttempts`. They are mutually exclusive, mixed attempts use the former, and the two counts sum to the primary cohort.
- Choice context uses the exposure time. Preceding player snapshots older than 30 seconds are marked stale; transition fallback is explicitly approximate.
- Pick-rate, pairwise, and outcome differences are descriptive and non-causal.

## 8. Progression Next-Run report

Progression Next-Run requires environment and content version. It has no stage-wide population filter: optional previous/next stage values constrain association and paired results only.

```powershell
defence-analytics progression-next-run `
  --environment Test `
  --content-version 4 `
  --as-of-utc 2026-08-15T00:00:00Z
```

Optional exact progression filters are `--progression-kind`, app/release fields, and `--development-build`. `--progression-start-utc`/`--progression-end-utc` constrain progression journey time; `--uploaded-start-utc`/`--uploaded-end-utc` constrain only primary progression ingestion. Supporting attempt and gameplay sources use the shared as-of upper bound so valid structural links are not lost.

The default previous and next immediate windows are 30 minutes and can be changed independently. Python uses the same contract:

```python
from defence_project_analytics import (
    ProgressionNextRunRequest,
    analyze_progression_next_run,
    generate_progression_next_run_report,
)

request = ProgressionNextRunRequest(environment="Test", content_version=4)
analysis = analyze_progression_next_run(request)
generated_path = generate_progression_next_run_report(request)
```

### Progression boundaries and interpretation

- The nearest previous final attempt and nearest next new attempt are structural boundaries. They are resolved before and independently of the configured max-gap windows.
- Max-gap values affect previous-context eligibility, immediate next-run engagement, and right-censoring; changing them does not redefine structural episode membership for the same as-of snapshot.
- A next run is a new gameplay attempt with `segmentIndex=1`. Resume continuations and lifecycle terminal rows are not next runs.
- Events with neither structural boundary remain in progression activity but are not merged into player-long episodes and are excluded from episode, co-occurrence, engagement, and paired analysis.
- `noNextRunWithinWindow` means that no qualifying next new attempt was observed in telemetry uploaded by `analysisAsOfUtc`. Offline or not-yet-uploaded gameplay may appear in a later snapshot; this metric is not churn.
- Recent episodes without an observed next run are right-censored until the configured window matures and are excluded from the next-run-rate denominator.
- Primary paired results require same-stage, same-content final attempts, eligible immediate links, and no open/resume overlap. All results are descriptive and non-causal.

The seven queries are dry-run together before execution and use the shared 1GB default cost gate. Reports use the stage-less path `reports/generated/progression-next-run/<environment>__all-stages__cv-<version>__<hash8>/`.

## 9. Post-Run Behavior report

Post-Run Behavior anchors one analytical window at each final attempt and observes feedback,
lobby presentation, user navigation, shop/offer activity, economy events, progression, and the
next new gameplay attempt. It does not reconstruct an application session.

```powershell
defence-analytics post-run-behavior `
  --environment Test `
  --content-version 4 `
  --as-of-utc 2026-08-15T00:00:00Z
```

Optional anchor filters include `--stage-key`, `--final-outcome`, app/release fields,
run-ended bounds, and ingestion bounds. Supporting activity uses the resolved as-of snapshot
rather than the anchor ingestion interval. The seven aggregate queries share a 1GB default
dry-run gate.

```python
from defence_project_analytics import (
    PostRunBehaviorRequest,
    analyze_post_run_behavior,
    generate_post_run_behavior_report,
)

request = PostRunBehaviorRequest(environment="Test", content_version=4)
analysis = analyze_post_run_behavior(request)
generated_path = generate_post_run_behavior_report(request)
```

### Post-run interpretation

- The window is half-open from final `segmentEndedAtUtc` to the earlier of the next new attempt or the configured 30-minute cap. Resume and lifecycle-terminal rows do not close it.
- Recent windows without an observed next attempt are right-censored and excluded from mature absence and next-run denominators.
- `shopPresentedWindows` records a Shop tab or section presentation. `shopUserNavigatedWindows` requires `TabViewed(tab='Shop', navigationSource='User')`; a section presentation alone never establishes user navigation or a direct section click.
- `observedAttemptSuccessRate` is linked Succeeded results divided by observed best-effort commerce Attempt operations.
- `committedSuccessWindowRate` is durable Succeeded-result presence divided by relevant mature windows. A missing best-effort Attempt does not change the durable Result fact.
- Fun Feedback Uranium rewards and progression spends are excluded from both commerce metrics.
- Initial and programmatic navigation are presentations, not user intent. Best-effort event absence does not prove behavior absence.
- No next new attempt observed within the window is not churn; offline or later-uploaded gameplay may appear in a later as-of snapshot.

## 10. ContentVersion comparison

ContentVersion Comparison executes the existing aggregate analyses for an ordered baseline and
candidate, keeps those source results in memory, and writes only the final comparison bundle.
It never creates intermediate Stage, Weapon, Upgrade, Progression, or Post-Run report directories.

```powershell
defence-analytics content-version-compare `
  --environment Test `
  --baseline-content-version 1 `
  --candidate-content-version 4 `
  --stage-key stage1 `
  --as-of-utc 2026-08-15T00:00:00Z
```

With a stage, all five domains run by default. Without a stage, the default is the stage-less
Progression and Post-Run domains; explicitly selecting Stage, Weapon, or Upgrade requires
`--stage-key`. Use `--domains stage,weapon,post-run` to select a subset. All source queries and
the cohort profile are dry-run before any actual query. Their total shares a 1GB default cap.

```python
from defence_project_analytics import (
    ContentVersionCompareRequest,
    analyze_content_version_comparison,
    generate_content_version_comparison_report,
)

request = ContentVersionCompareRequest(
    environment="Test",
    baseline_content_version=1,
    candidate_content_version=4,
    stage_key="stage1",
)
analysis = analyze_content_version_comparison(request)
generated_path = generate_content_version_comparison_report(request)
```

### Comparison interpretation

- Every delta is `candidate - baseline`; version numbers are not automatically sorted.
- Ratio rows preserve both counts, both denominators, and both ratios. Percentage-point and relative changes are separate, and relative change is null when the baseline is zero.
- `0` means an observed zero. A missing or one-side-only open content identity stays null and is labeled as observed only on the available side.
- Each metric has one coarse status (`Comparable`, `Limited`, `Unavailable`, or `Incompatible`) and an independently ordered list of all applicable warning codes. Low sample, coverage difference, and source warnings can coexist.
- Stage Difficulty uses `uploadedAtUtc` as an ingestion upper bound. This is not a BigQuery historical system-time snapshot. The other domains use their `analysisAsOfUtc` telemetry-row parameter; metadata records these snapshot modes separately.
- ContentVersion differences are observational. Counts, coverage, release identity, and observation times must be considered before interpreting a delta.

The output path is
`reports/generated/content-version-compare/<environment>__<stage-or-all-stages>__cv-<baseline>-vs-cv-<candidate>__<hash8>/`.

## 11. Population, resume, and completeness

- Final attempts come only from `telemetry_attempt_outcomes_v1`; raw `telemetry_run_summary` rows are not counted as attempts.
- Clear rate is `Clear / (Clear + Dead)`; Abandon is reported separately.
- Death metrics use final attempts with `gameplayOutcome='Dead'`.
- Candidate detail includes every gameplay segment belonging to a selected attempt, using null-safe player ID equality.
- Run rows are deduplicated by `environment + runId`, and child rows by `environment + runId + rowIndex`.
- Detail requires both matching content/release scope and `telemetryComplete=TRUE` on `environment + runId + uploadId`.
- Explicit incomplete, missing legacy status, and mixed-scope resume segments are separately counted and excluded.
- Incoming damage and threat include all eligible resume segments once. Final lethal cause and player state use only the final Dead run.
- Lethal-hit events can precede revival and are never treated as final death counts.

## 12. Warning interpretation

Warnings do not fail report generation. Stage Difficulty keeps its existing thresholds. Weapon reports identify detail and DPS coverage issues. Upgrade reports identify incomplete/truncated presentations, same-segment selection-count mismatches, unlinked selections, approximate context, low candidate/pair samples, and observational association bias. Progression reports distinguish low event/episode/pair samples, unbounded activity-only events, missing immediate context, open/resume exclusions, right-censoring, cross-content exclusions, and multi-progression confounding. Treat affected metrics as descriptive aggregates with the exact denominator recorded in metadata and CSV.

## 13. LLM handoff

The report contract is documented in [docs/report-contract.md](docs/report-contract.md). The recommended files to provide to an LLM are:

```text
report.md
metrics.json
metadata.json
tables/deaths_by_enemy.csv
tables/deaths_by_phase.csv
tables/incoming_damage_by_enemy.csv
tables/threat_by_outcome.csv
```

All generated files contain aggregates only: no telemetry player, attempt, run, or upload identifiers. Markdown contains deterministic facts and caveats, not balancing conclusions.

For Weapon Performance, provide:

```text
report.md
metrics.json
metadata.json
tables/weapon_adoption.csv
tables/weapon_combat_performance.csv
tables/weapon_dps.csv
tables/weapon_outcome_association.csv
```

Stable content identifiers such as `weaponFamilyId`, `weaponId`, and `weaponType` are allowed. Runtime weapon instance IDs are not written.

For Upgrade Choice, provide:

```text
report.md
metrics.json
metadata.json
tables/upgrade_candidate_exposure.csv
tables/upgrade_head_to_head.csv
tables/upgrade_outcome_association.csv
tables/upgrade_context.csv
```

Stable upgrade/category/content identifiers are allowed. Player, attempt, gameplay-segment, upload, exposure, and runtime instance identifiers are never written.

For Progression Next-Run, provide:

```text
report.md
metrics.json
metadata.json
tables/progression_activity.csv
tables/progression_episode_composition.csv
tables/progression_next_run.csv
tables/progression_outcome_transitions.csv
```

Progression artifacts contain aggregate stable kind/target/state dimensions only. They never contain player, attempt, run, event, transaction, or batch identifiers.

For Post-Run Behavior, provide:

```text
report.md
metrics.json
metadata.json
tables/post_run_navigation.csv
tables/post_run_feedback_behavior.csv
tables/post_run_shop_funnel.csv
tables/post_run_commerce.csv
tables/post_run_progression.csv
tables/post_run_next_run.csv
```

Post-run artifacts distinguish presentation from user navigation and observed Attempt resolution
from durable committed-result presence. They contain aggregate dimensions only and never raw
player, attempt, run, event, operation, presentation, batch, or upload identifiers.

For ContentVersion Comparison, provide:

```text
report.md
metrics.json
metadata.json
tables/comparison_data_quality.csv
tables/comparison_stage.csv (when relevant)
tables/comparison_weapon.csv (when relevant)
tables/comparison_upgrade.csv (when relevant)
tables/comparison_progression.csv (when relevant)
tables/comparison_post_run.csv (when relevant)
```

The comparison report keeps source definitions, samples, quality, warnings, and snapshot modes in
metadata. It provides descriptive directions only and does not assign positive or negative value
to a change.

## Tests and build

```powershell
python -m pytest
python -m build
```

See [docs/handoff/README.md](docs/handoff/README.md) for the source telemetry, privacy, join, completeness, and release-identity contracts.
