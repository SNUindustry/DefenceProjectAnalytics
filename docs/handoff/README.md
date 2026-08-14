# Telemetry Analytics Handoff

## 목적

이 package는 `DefenceProject`의 Unity/Functions telemetry 계약을 Python/SQL 기반 balancing 분석에서 read-only로 사용하기 위한 snapshot입니다. 기본 분석 Source of Truth는 BigQuery `bald-ops.game_telemetry`이며, raw Cloud Storage archive가 아닙니다.

원본 기준 revision과 복사 파일 해시는 [`source-manifest.md`](source-manifest.md)에 기록되어 있습니다. 정확한 BigQuery column type/mode는 [`../../contracts/bigquery-schema/`](../../contracts/bigquery-schema/)의 JSON을 최종 기준으로 사용합니다.

## 분석 기본 규칙

```text
Segment 분석
→ telemetry_gameplay_segments_v1

Attempt outcome 분석
→ telemetry_attempt_outcomes_v1

v2 상세 gameplay 분석
→ telemetry_upload_status_v1.telemetryComplete = TRUE 확인
```

`telemetry_run_summary` 한 행은 attempt 전체가 아니라 gameplay 또는 lifecycle segment 하나입니다. resume/process kill 때문에 한 `attemptId`에 여러 `runId`가 존재할 수 있으므로 raw row count를 attempt 수로 사용하면 안 됩니다.

`LifecycleTerminal`은 resume 거부·불가 또는 새 stage 시작으로 기존 attempt를 닫는 lifecycle record입니다. 플레이 수, 플레이 시간, combat, DPS, weapon-performance denominator에 포함하지 않습니다.

v2의 weapon, enemy damage, detailed player snapshot, upgrade detail은 일부 Data chunk가 누락될 수 있습니다. 상세 분석은 `environment + runId + uploadId`로 upload-status view에 연결하고 `telemetryComplete=TRUE`를 요구합니다. Core-only final outcome 분석은 incomplete detail을 의도적으로 포함할 수 있습니다.

## 환경과 접근 범위

- 일반 분석 환경: `Test`, `Production`
- legacy run envelope에 environment가 없으면 Functions mapper가 `Test`로 분류합니다.
- `environment`, `releaseChannel`, `releaseType`, `isDevelopmentBuild`는 별도 열로 분석합니다.
- raw bucket `gs://bald-ops-telemetry-raw`는 기본 분석 대상과 기본 IAM 범위에서 제외합니다.
- Unity client upload 인증과 BigQuery 분석 인증은 서로 다릅니다.

## 문서

- [`cloud-access.md`](cloud-access.md): cloud 식별자, ADC, 최소 IAM 후보, 인증 경계
- [`telemetry-data-dictionary.md`](telemetry-data-dictionary.md): 25개 table과 4개 canonical view 의미
- [`telemetry-join-contracts.md`](telemetry-join-contracts.md): ID, join, run/economy/shop/feedback 계약
- [`release-identity.md`](release-identity.md): release/version 차원
- [`privacy-contract.md`](privacy-contract.md): 허용·금지 데이터와 raw archive 주의점
- [`source-manifest.md`](source-manifest.md): 원본 revision, path mapping, SHA-256

## 원본 의미 계약

이 repository에는 Unity/Functions source를 복사하지 않았습니다. 의미 변경 여부를 점검할 때는 원본의 다음 파일을 다시 확인해야 합니다.

- `functions/README.md`
- `functions/src/telemetryContract.ts`
- `functions/src/telemetryBigQueryMapper.ts`
- `functions/src/index.ts`
- `Assets/Scripts/ScenePlay/Telemetry/TelemetryModels.cs`
- `Assets/Scripts/ScenePlay/Telemetry/LobbyTelemetryModels.cs`
- `Assets/Scripts/ScenePlay/Telemetry/TelemetryIdentityProvider.cs`
