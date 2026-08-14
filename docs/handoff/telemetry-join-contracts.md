# Telemetry Join Contracts

## ID semantics

| ID | 의미 | 사용 규칙 |
|---|---|---|
| `telemetryPlayerId` | save profile에 생성되는 persistent random GUID; Firebase UID가 아님 | 환경을 포함해 run/lobby/economy의 pseudonymous player 연결 |
| `attemptId` | 새 stage attempt ID. resume 후에도 유지. legacy resume는 stage/seed/start time으로 파생 | attempt segment 묶음 |
| `runId` | gameplay segment 또는 lifecycle terminal마다 새 GUID | run summary와 gameplay child join |
| `eventId` | logical event canonical material의 64자 lowercase SHA-256 | event identity, idempotent archive, child parent key |
| `operationId` | transaction 실행 호출마다 새 GUID | Attempt와 Result 연결 |
| `logicalTransactionId` | environment와 raw execution ID를 SHA-256한 값 | retry/duplicate를 포함한 동일 logical transaction 연결; available flag 확인 |
| `presentationId` | shop section presentation 시작마다 새 GUID | exposure와 selection을 같은 presentation으로 묶음 |
| `batchId` | 물리적 lobby upload batch GUID | transport 진단 전용; repacking 시 바뀔 수 있음 |
| `uploadId` | 한 run segment의 v2 chunk transport ID | run summary와 upload-status 연결 |
| `parentEventId` | cost/reward/offer child가 참조하는 header event ID | child→transaction/shop header |
| `linkedResultEventId` | progression이 참조하는 transaction Result event ID | Result→progression; standalone progression이면 비어 있음 |
| `rowIndex` | parent 또는 run 안의 안정적인 행 순서 | child 복합키와 deterministic insert identity |

## 권장 join

```text
Run ↔ gameplay child
  environment + runId

Attempt segments
  environment + telemetryPlayerId + attemptId

Run ↔ Feedback
  environment + runId

Run ↔ Lobby/Economy
  environment + telemetryPlayerId + timestamp window

Transaction Attempt ↔ Result
  environment + operationId

Transaction ↔ Cost/Reward
  environment + eventId = parentEventId

Transaction Result ↔ Progression
  environment + eventId = linkedResultEventId

Shop Exposure ↔ Offer
  environment + eventId = parentEventId

Shop presentation
  environment + telemetryPlayerId + presentationId

Run ↔ upload status
  environment + runId + uploadId
```

`batchId`는 transport identity이므로 semantic join key로 사용하지 않습니다.

## Attempt / Run semantics

```text
attemptId : runId = 1:N
```

- 새 attempt는 새 `attemptId`, `segmentIndex=1`로 시작합니다.
- 각 gameplay segment는 새 `runId`를 받습니다.
- resume은 같은 `attemptId`를 유지하고 `segmentIndex`를 증가시킵니다.
- process kill 전에 저장된 active checkpoint는 다음 boot에서 `Gameplay + Interrupted + outcome None + isAttemptFinal=false`로 enqueue됩니다.
- resume 거부/불가 또는 새 stage 시작은 새 `LifecycleTerminal` run을 만들어 기존 attempt를 final `Abandon`으로 닫습니다.
- 정상 `Clear`/`Dead`는 completed gameplay segment이며 `isAttemptFinal=true`입니다.
- gameplay 도중 ReturnToLobby는 gameplay `Abandon` final이 될 수 있습니다.

Final attempt 분석은 raw table을 직접 deduplicate하지 말고 `telemetry_attempt_outcomes_v1`을 사용합니다. Final gameplay만 필요하면 추가로 `segmentKind='Gameplay'`를 적용합니다.

`segmentDurationSeconds`는 현재 segment 길이, `attemptElapsedTimeSeconds`는 해당 segment 종료 시점까지의 누적 attempt 시간입니다. `LifecycleTerminal`은 실제 gameplay segment가 아니며 start timestamp가 없을 수 있습니다.

## telemetryComplete

`telemetry_upload_status_v1.telemetryComplete`는 다음이 모두 참일 때만 `TRUE`입니다.

- distinct received chunk count가 expected count와 일치
- index 0의 Core chunk가 정확히 하나 존재
- chunk count, transport version, index-kind metadata가 일관됨
- chunk index가 0부터 `chunkCount-1`까지 이어짐

v2 weapon, enemy damage, detailed snapshots, upgrade detail 분석은 다음 join 후 complete row만 사용합니다.

```sql
JOIN `bald-ops.game_telemetry.telemetry_upload_status_v1` AS status
  USING (environment, runId, uploadId)
WHERE status.telemetryComplete = TRUE
```

Core-only outcome/final-death 분석은 incomplete detail을 의도적으로 포함할 수 있습니다. Legacy non-chunk upload에는 status row가 없습니다.

## Economy / Progression

- `Attempt`는 실행 전에 기록되며 `Offered` resource snapshot을 가질 수 있습니다.
- `Result`는 실행이 해결된 뒤 기록됩니다.
- `Succeeded`: 이번 실행에서 commit됨.
- `Duplicate`: 같은 logical execution이 이미 commit되어 다시 적용하지 않음.
- `Cancelled`: 명시적 취소.
- `Blocked`: invalid request, busy, recovery, catalog/target/price/resource/content 전제조건 실패.
- `Failed`: 그 밖의 예기치 않은 실행/저장 실패.
- `failureCode`는 symbolic enum이며 FailureReason 전문이 아닙니다.
- `Committed` cost/reward는 성공 Result에, `ResolvedNotCommitted` cost는 commit되지 않은 해결 결과에 연결됩니다.
- `amount`는 resource 요청/제시/결과 수량이며 계정 잔액 before/after가 아닙니다.
- 장기 분석에서는 자유 형식 `source`보다 안정 enum `sourceCategory`를 우선합니다.
- 성공 transaction의 progression은 같은 durable bundle에 보존되고 `linkedResultEventId`로 Result에 연결됩니다.
- Evolution `ApplyOnly`는 비용 없는 standalone progression이며 transaction header 없이 `linkedResultEventId`가 비어 있습니다.

## Shop / Lobby

- `TabViewed`/`ShopSectionViewed`는 page가 실제 active이고 navigation revision/current tab이 일치하는지 frame yield 후 검증한 뒤 기록됩니다.
- `Initial`, `User`, `Programmatic`은 최초 표시, 사용자 선택, 코드 이동을 구분합니다.
- shop section presentation 시작마다 새 `presentationId`가 생성됩니다.
- `OfferExposure`는 presentation 확인 후에만 기록되고 같은 presentation 안에서 동일 offer를 중복 노출하지 않습니다.
- `OfferSelected`는 현재 presentation과 선택 offer를 기록합니다.
- stale/cancelled rebuild 자체는 view 또는 exposure event가 아닙니다.

## Feedback

- `Exposure`는 prompt가 실제 표시된 시점이며 response는 `NULL`입니다.
- `Response`는 `Positive` 또는 `Negative`입니다.
- feedback event ID는 `environment + runId + eventKind`의 deterministic SHA-256입니다.
- feedback는 `environment + runId`로 final run에 연결합니다.
- Production prompt 대상은 final Clear/Dead attempt, 최소 attempt elapsed 60초이며 eligible attempt 3~5회 cadence를 사용합니다.
- response 제출은 `system.fun-feedback.uranium.2` reward transaction이 성공하거나 duplicate로 확인된 뒤 enqueue됩니다. 보상은 Uranium 2개입니다.

Feedback reward의 exact logical transaction ID는 다음과 같이 재현할 수 있습니다.

```sql
TO_HEX(SHA256(CONCAT(
  'transaction:v1|',
  feedback.environment,
  '|feedback_reward:',
  feedback.attemptId
)))
```

추가 확인 조건은 `transactionId='system.fun-feedback.uranium.2'`, `sourceCategory='FunFeedback'`, `eventKind='Result'`, `resultCategory IN ('Succeeded','Duplicate')`입니다.

원본 근거:

- `Assets/Scripts/Core/TelemetryIdentity.cs`
- `Assets/Scripts/Core/PlaySessionData.cs`
- `Assets/Scripts/Game/Resume/RunResumeSnapshotData.cs`
- `Assets/Scripts/ScenePlay/Telemetry/TelemetryManager.cs`
- `Assets/Scripts/ScenePlay/Telemetry/Upload/TelemetryAttemptRecoveryCoordinator.cs`
- `Assets/Scripts/ScenePlay/Telemetry/TelemetryLifecycleTerminalReporter.cs`
- `Assets/Scripts/Economy/Transactions/TransactionTelemetryRuntime.cs`
- `Assets/Scripts/SceneLobby/ShopTelemetryReporter.cs`
- `Assets/Scripts/SceneLobby/LobbyActivityTelemetryCoordinator.cs`
- `Assets/Scripts/ScenePlay/Feedback/FunFeedbackCoordinator.cs`
- `functions/src/index.ts`
