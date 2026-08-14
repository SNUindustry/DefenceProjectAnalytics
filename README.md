# DefenceProjectAnalytics

DefenceProject telemetry handoff, copied BigQuery schemas, and canonical SQL are the source of truth for this read-only analytics project. Phase B-1 adds a versioned aggregate report contract and Stage Difficulty / Death Analysis while retaining the Phase A foundation.

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

Runtime dependencies remain `google-cloud-bigquery`, `db-dtypes`, `pandas`, `pyarrow`, `matplotlib`, and `jupyter`; tests use `pytest`. Phase B-1 adds no dependency.

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

## 6. Population, resume, and completeness

- Final attempts come only from `telemetry_attempt_outcomes_v1`; raw `telemetry_run_summary` rows are not counted as attempts.
- Clear rate is `Clear / (Clear + Dead)`; Abandon is reported separately.
- Death metrics use final attempts with `gameplayOutcome='Dead'`.
- Candidate detail includes every gameplay segment belonging to a selected attempt, using null-safe player ID equality.
- Run rows are deduplicated by `environment + runId`, and child rows by `environment + runId + rowIndex`.
- Detail requires both matching content/release scope and `telemetryComplete=TRUE` on `environment + runId + uploadId`.
- Explicit incomplete, missing legacy status, and mixed-scope resume segments are separately counted and excluded.
- Incoming damage and threat include all eligible resume segments once. Final lethal cause and player state use only the final Dead run.
- Lethal-hit events can precede revival and are never treated as final death counts.

## 7. Warning interpretation

Warnings do not fail report generation. Defaults flag fewer than 30 attempts, 10 unique players, 20 deaths, or 20 detail-eligible runs. Other stable codes identify incomplete or legacy detail, mixed content, missing attribution/state, unresolved release identity, unrecognized values, and invalid death timing. Treat affected metrics as descriptive aggregates with the stated coverage.

## 8. LLM handoff

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

## Tests and build

```powershell
python -m pytest
python -m build
```

See [docs/handoff/README.md](docs/handoff/README.md) for the source telemetry, privacy, join, completeness, and release-identity contracts.
