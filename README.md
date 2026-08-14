# DefenceProjectAnalytics

`DefenceProject` telemetry의 handoff, BigQuery schema contract, canonical SQL을 Source of Truth로 사용하는 read-only Python analytics foundation입니다. Phase A는 연결·계약 검증과 Stage Overview까지만 제공하며 balancing 모델, dashboard, Streamlit은 포함하지 않습니다.

## 안전 경계

- 인증은 Application Default Credentials(ADC)만 사용합니다.
- BigQuery query는 `SELECT`/`WITH`만 허용합니다.
- `CREATE`, `DROP`, `UPDATE`, `DELETE`, `INSERT`, `MERGE`, export 등 mutation은 거부합니다.
- service-account JSON, Firebase credential, Google token, Unity `X-Telemetry-App-Secret`을 만들거나 저장하지 않습니다.
- raw bucket에 접근하지 않으며 raw telemetry 전체를 로컬로 dump하지 않습니다.

기본 설정은 GCP project `bald-ops`, dataset `game_telemetry`, location `asia-northeast3`입니다. 필요하면 `DPA_GCP_PROJECT`, `DPA_BIGQUERY_DATASET`, `DPA_BIGQUERY_LOCATION` 환경 변수 또는 CLI option으로 변경할 수 있습니다. `environment`, `stageKey`, `contentVersion`은 query parameter로만 전달합니다.

## 설치

현재 개발 기준은 Python 3.11.9입니다(`requires-python >=3.11`).

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

설치되는 주요 dependency는 `google-cloud-bigquery`, `db-dtypes`, `pandas`, `pyarrow`, `matplotlib`, `jupyter`, `pytest`입니다.

## ADC 인증

Google Cloud CLI를 설치한 뒤 사용자 ADC를 설정합니다.

```powershell
gcloud auth application-default login
gcloud config set project bald-ops
```

CLI 로그인과 client-library ADC는 별도이므로 `gcloud auth login`만으로는 충분하지 않습니다. ADC가 없으면 CLI는 위 명령을 포함한 diagnostic을 출력합니다. 필요한 최소 권한 후보는 project의 `roles/bigquery.jobUser`와 `bald-ops.game_telemetry` dataset의 `roles/bigquery.dataViewer`입니다.

## 실행

로컬 JSON 25개를 parse하고 실제 핵심 table/view 및 필수 column을 확인합니다. legacy의 nullable mode 차이는 blocker로 판정하지 않습니다.

```powershell
defence-analytics validate-contracts
```

필수 table/view 6개에 metadata lookup과 최소 read-only query를 실행합니다.

```powershell
defence-analytics connection-smoke
```

Stage Overview를 실행합니다. `contentVersion`은 선택 사항입니다.

```powershell
defence-analytics stage-overview --stage-key <stage-key> --environment Production
defence-analytics stage-overview --stage-key <stage-key> --environment Test --content-version 42
```

비용 상한을 추가하려면 `--maximum-bytes-billed <bytes>`를 지정할 수 있습니다. 결과는 raw row가 아니라 집계된 JSON 한 건입니다.

Python API도 동일한 query parameter와 read-only 경계를 사용합니다.

```python
from defence_project_analytics import StageOverviewRequest, get_stage_overview

result = get_stage_overview(
    StageOverviewRequest(
        stage_key="stage-key",
        environment="Production",
        content_version=None,
    )
)
```

## 지표 계약

[`sql/analysis/run_fact_v1.sql`](sql/analysis/run_fact_v1.sql)은 `telemetry_attempt_outcomes_v1`을 중심으로 final attempt당 한 행을 만듭니다. raw `telemetry_run_summary` 행 수를 attempt 수로 세지 않습니다.

- Feedback: `environment + runId`
- v2 completeness: `environment + runId + uploadId`
- Clear rate: `Clear / (Clear + Dead)`; Abandon은 분모에서 제외
- telemetryComplete rate: status가 존재하는 v2 attempt만 분모에 포함; legacy/status 없음은 `NULL`
- 빈 문자열/nullable telemetry player ID는 unique player 수에서 제외

## 테스트

```powershell
python -m pytest
```

테스트는 SQL placeholder와 named parameter binding, read-only guard, run-fact 계약, Clear-rate 분모, nullable/legacy 처리, Production/Test 및 contentVersion filter를 검증합니다.

상세 telemetry 의미와 privacy/IAM 경계는 [`docs/handoff/README.md`](docs/handoff/README.md)에서 시작하십시오.
