# Privacy Contract

## 허용되는 pseudonymous / logical identifiers

- `telemetryPlayerId`
- `attemptId`
- `runId`
- `eventId`
- `operationId`
- `logicalTransactionId`
- `presentationId`
- `batchId`
- `uploadId`
- `correlationHash`
- stage, weapon, offer, product, transaction definition, catalog/release hash 등 content ID

`telemetryPlayerId`는 Firebase UID나 account ID가 아니라 save profile에 생성되는 persistent random analytics GUID입니다.

## 금지 데이터

다음 데이터는 분석 repository에서 보거나 저장하지 않습니다.

- Firebase UID
- account/provider ID
- receipt 원문
- order ID
- raw Store transaction ID
- raw economy execution ID
- exception 또는 stack trace 전문
- FailureReason 전문
- Unity telemetry upload secret
- Firebase credential 또는 Google account token
- service-account JSON/private key

허용되는 `failureCode`는 제한된 symbolic code이고 FailureReason 전문이 아닙니다. `logicalTransactionId`와 `correlationHash`는 raw private value의 SHA-256 결과입니다.

## Raw bucket

기본 analytics path는 BigQuery만 사용합니다. `gs://bald-ops-telemetry-raw`는 기본 분석 대상과 기본 IAM에서 제외합니다.

BigQuery contract schema에는 위 금지 identifier 열이 없습니다. 그러나 Functions는 수신 payload 전체를 raw JSON으로 archive하고, validation은 추가 JSON 속성을 모두 거부하는 strict allow-list가 아닙니다. 따라서 BigQuery schema가 안전하다는 이유만으로 raw bucket도 동일하다고 가정하면 안 됩니다.

Raw access가 필요한 경우:

1. 별도 privacy/security review를 수행합니다.
2. bucket-scoped read 권한만 검토합니다.
3. raw JSON을 repository, notebook output 또는 report에 복사하지 않습니다.

## 인증 자료

Unity client upload secret은 BigQuery credential이 아닙니다. `.gitignore`로 숨긴 뒤 복사하는 방식도 사용하지 않고 애초에 destination으로 가져오지 않습니다.

원본 근거:

- `Assets/Scripts/Core/GameSaveData.cs`
- `Assets/Scripts/Core/TelemetryIdentity.cs`
- `Assets/Scripts/Economy/Transactions/IapTelemetryReporter.cs`
- `functions/src/telemetryContract.ts`
- `functions/src/index.ts` raw archive functions
- copied BigQuery schema JSON 전체
