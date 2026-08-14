# Cloud Access

## Cloud 식별자

| 항목 | 현재 repository 계약 | 원본 근거 |
|---|---|---|
| GCP project | `bald-ops` | `.firebaserc` |
| BigQuery dataset | `game_telemetry` | `functions/src/index.ts`의 `DATASET_ID` |
| Region/location | `asia-northeast3` | `functions/src/index.ts`의 `REGION`, `functions/README.md` |
| Raw bucket | `gs://bald-ops-telemetry-raw` | project ID + `telemetry-raw` suffix |
| Upload Function | `uploadTelemetry`, `asia-northeast3` | `functions/src/index.ts` |

위 값은 Source working tree에서 추출한 계약입니다. 이 handoff 생성 과정에서는 live GCP resource나 IAM binding을 조회하거나 변경하지 않았습니다.

## 로컬 분석 인증

```powershell
gcloud auth application-default login
gcloud config set project bald-ops
```

Python Google Cloud Client Library는 ADC를 사용합니다. `gcloud` CLI 계정 로그인과 client-library ADC는 별도 credential path이므로 `gcloud auth application-default login`이 필요합니다.

## 최소 IAM 후보

BigQuery SQL 분석만 수행하는 principal:

```text
Project/query billing project:
  roles/bigquery.jobUser

Dataset bald-ops.game_telemetry:
  roles/bigquery.dataViewer
```

Raw bucket 권한은 기본 분석 IAM에 포함하지 않습니다. 별도 승인된 raw 조사에 한해 bucket 범위의 `roles/storage.objectViewer`를 고려합니다.

참고: [Google Cloud ADC](https://docs.cloud.google.com/docs/authentication/provide-credentials-adc), [BigQuery query IAM](https://docs.cloud.google.com/bigquery/docs/running-queries)

## Test/Production 선택

현재 Unity runtime은 Editor, development build 또는 Development release channel이면 Test config를 선택하고, 그 외에는 Production config를 선택합니다.

```text
UNITY_EDITOR
OR Debug.isDebugBuild
OR releaseChannel == Development
→ Test telemetry config

otherwise
→ Production telemetry config
```

그러나 저장된 데이터에서 이 규칙을 역추론하지 않습니다. 실제 `environment`, `releaseChannel`, `releaseType`, `isDevelopmentBuild` 열을 각각 사용합니다.

원본 근거:

- `Assets/Scripts/ScenePlay/Telemetry/TelemetryRuntimeBootstrap.cs`
- `Assets/Scripts/Editor/TelemetryBuildValidator.cs`
- `Assets/Datas/Telemetry/TestConfig.asset`
- `Assets/Datas/Telemetry/ProductionConfig.asset`

## 인증 경계

```text
Unity client
→ X-Telemetry-App-Secret
→ uploadTelemetry

Analytics
→ ADC + read-only IAM
→ BigQuery
```

`X-Telemetry-App-Secret`과 TelemetryConfig의 upload secret은 Function upload용 shared secret이며 BigQuery 읽기 credential이 아닙니다. secret 값, Firebase credential, Google token, service-account JSON key는 이 repository로 복사하지 않습니다.
