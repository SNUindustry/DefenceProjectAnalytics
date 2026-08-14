# Telemetry Data Dictionary

정확한 column name/type/mode의 최종 Source of Truth는 [`../../contracts/bigquery-schema/`](../../contracts/bigquery-schema/)의 원본 그대로 복사된 JSON입니다. 이 문서는 row와 join의 의미를 설명합니다.

## 공통 시간 규칙

- `uploadedAtUtc`: Functions가 BigQuery에 mapping한 서버 시각
- `occurredAtUtc`: feedback/lobby/economy event가 client에서 발생한 시각
- `segmentStartedAtUtc`, `segmentEndedAtUtc`: gameplay/lifecycle segment wall-clock 경계
- `elapsedTime`: attempt 누적 gameplay 시간인 경우가 많음
- `segmentElapsedTime`: 현재 segment 안에서의 상대 시간

## Gameplay tables

| Table | 한 row의 의미 | Stable ID / join | 핵심 field 및 주의점 |
|---|---|---|---|
| `telemetry_run_summary` | gameplay 또는 lifecycle segment 하나 | `environment + runId`; attempt 묶음은 `environment + telemetryPlayerId + attemptId` | segment/outcome, stage, duration, player/release identity. `durationSeconds`는 legacy cumulative attempt elapsed이므로 segment 분석에는 `segmentDurationSeconds` 사용 |
| `telemetry_run_start_snapshot` | segment 시작 snapshot 하나 | `environment + runId`, `rowIndex=0` | stage/seed/evolution, 시작 level/HP/EXP, resume 여부, loadout/stat count |
| `telemetry_run_loadout` | 시작 loadout slot 하나 | `environment + runId + rowIndex` | slot category/order, armor slot, weapon/family/type |
| `telemetry_run_lobby_stats` | 시작 시점 lobby stat 하나 | `environment + runId + rowIndex` | `statType`, `statName`, `value` |
| `telemetry_player_snapshots` | 시간/이벤트 기반 player snapshot 하나 | `environment + runId + rowIndex`; `snapshotIndex` | position, phase/wave/floor/zone, HP/EXP/level, enemy count, core stats |
| `telemetry_player_snapshot_stats` | snapshot에 딸린 stat 하나 | `environment + runId + snapshotIndex + rowIndex` | full-stat 설정에 따라 없을 수 있음 |
| `telemetry_weapon_summary` | segment 내 weapon instance 집계 하나 | `environment + runId + rowIndex` | weapon/family, damage, boss damage, hits, damage share |
| `telemetry_weapon_analytics_samples` | weapon 상태 구간별 DPS sample 하나 | `environment + runId + rowIndex` | validity/finalize reason, DPS, damage, uptime, density/boss bucket, attribution |
| `telemetry_kill_summary` | segment kill 집계 하나 | `environment + runId` | total/elite/boss kills, attribution and overflow |
| `telemetry_threat_summary` | segment threat 집계 하나 | `environment + runId` | near death, low-HP duration, danger distance, density |
| `telemetry_enemy_damage_summary` | damage source/enemy 조합 집계 하나 | `environment + runId + rowIndex` | source classification, enemy ID/tier/class, damage/hit/lethal hit |
| `telemetry_upgrade_exposure_candidates` | upgrade exposure에서 표시된 candidate 하나 | `environment + runId + exposureId + candidateIndex` | 한 exposure가 여러 row. presented/recorded count 및 overflow 확인 |
| `telemetry_upgrade_selections` | 실제 upgrade 선택 하나 | `environment + runId + rowIndex` | exposure/candidate linkage, upgrade/family/grant ID. legacy linkage는 비어 있을 수 있음 |
| `telemetry_final_weapon_state` | segment 종료 weapon slot/state 하나 | `environment + runId + rowIndex` | attempt/segment, weapon instance/family, level, evolution/active |
| `telemetry_run_transitions` | wave/phase/boss transition 하나 | `environment + runId + rowIndex` | transition type, boss/phase/wave/floor, attempt/segment elapsed |
| `telemetry_upload_chunks` | v2 transport chunk receipt 하나 | `environment + runId + uploadId + chunkIndex` | transport diagnostics. gameplay dimension으로 사용하지 않음 |

Gameplay child table 대부분에는 `telemetryPlayerId`와 release field가 없습니다. `environment + runId`로 `telemetry_run_summary`에 연결합니다.

## Feedback / Economy / Lobby tables

| Table | 한 row의 의미 | Stable ID / join | 핵심 field 및 주의점 |
|---|---|---|---|
| `telemetry_run_feedback_events` | feedback `Exposure` 또는 `Response` 하나 | event: `environment + eventId`; run: `environment + runId` | response, attempt, player/release identity |
| `telemetry_transaction_events` | transaction `Attempt` 또는 `Result` header 하나 | `environment + eventId`; Attempt/Result는 `environment + operationId` | result/failure code, transaction/source/target, logical transaction |
| `telemetry_transaction_costs` | transaction header의 cost resource snapshot 하나 | `environment + parentEventId + rowIndex` | resource type/ID, amount, snapshot kind |
| `telemetry_transaction_rewards` | transaction header의 reward resource snapshot 하나 | `environment + parentEventId + rowIndex` | resource type/ID, amount, snapshot kind |
| `telemetry_shop_events` | OfferExposure header 또는 OfferSelected 하나 | `environment + eventId`; presentation은 player+presentation ID | tab, section, offer, presentation |
| `telemetry_shop_exposure_offers` | OfferExposure header에 포함된 offer 하나 | `environment + parentEventId + rowIndex` | `offerId` |
| `telemetry_progression_events` | progression state/value 변경 하나 | `environment + eventId`; transaction link는 `linkedResultEventId` | kind, target/secondary, before/after state/value |
| `telemetry_iap_events` | IAP lifecycle event 하나 | `environment + eventId` | product/definition/type/platform, localized price/currency, correlation hash |
| `telemetry_lobby_activity_events` | 실제 확인된 tab/section view 하나 | `environment + eventId` | event kind, tab/section, navigation source |

이 event table들은 `batchId`, `telemetryPlayerId`, release identity와 `uploadedAtUtc`를 공통으로 갖습니다. `batchId`는 transport identity이므로 semantic join key가 아닙니다.

## Canonical views

| View | 계약 |
|---|---|
| `telemetry_gameplay_segments_v1` | `segmentKind='Gameplay'`인 row만 노출 |
| `telemetry_completed_gameplay_segments_v1` | gameplay이면서 `segmentTermination='Completed'` |
| `telemetry_attempt_outcomes_v1` | `isAttemptFinal=TRUE`, non-null attempt ID를 `environment + telemetryPlayerId + attemptId`당 최신 한 row로 deduplicate |
| `telemetry_upload_status_v1` | expected/received chunk, Core, metadata 일관성을 집계하여 `telemetryComplete` 계산 |

## 주요 enum

- Segment: `Gameplay`, `LifecycleTerminal`; termination: `Completed`, `Interrupted`
- Outcome: `None`, `Clear`, `Dead`, `Abandon`
- Abandon reason: `ResumeDeclined`, `ResumeUnavailable`, `NewStageStarted`, `ReturnToLobby`
- Transaction event: `Attempt`, `Result`
- Transaction result: `Succeeded`, `Duplicate`, `Cancelled`, `Blocked`, `Failed`
- Transaction source category: `Other`, `Shop`, `WeaponRecipe`, `Evolution`, `StatReset`, `RandomBox`, `FunFeedback`, `IAP`, `System`
- Resource snapshot: `Requested`, `Offered`, `ResolvedNotCommitted`, `Committed`
- Shop: `OfferExposure`, `OfferSelected`
- Feedback: `Exposure`, `Response`; response: `Positive`, `Negative`
- Lobby activity: `TabViewed`, `ShopSectionViewed`; navigation: `Initial`, `User`, `Programmatic`
- IAP: `Initiated`, `InitiationFailed`, `Pending`, `Deferred`, `Cancelled`, `StoreFailed`, `FulfillmentFailed`, `Fulfilled`, `Duplicate`, `Recovered`

## Nullable / legacy

- balancing, release, identity field는 legacy row에서 `NULL`일 수 있습니다.
- legacy environment 누락은 server mapper에서 `Test`로 분류됩니다.
- `telemetryPlayerIdAvailable`, `logicalTransactionIdAvailable`, `releaseIdentityResolved`를 문자열 값보다 먼저 확인합니다.
- 빈 문자열과 `NULL`이 함께 존재할 수 있으므로 `NULLIF(TRIM(field), '')` 사용을 권장합니다.
- `contentVersion=0`은 unresolved fallback일 수 있습니다.
- v2 detail은 upload completeness를 확인하지만 legacy non-chunk upload에는 동일한 completeness 판정을 적용할 수 없습니다.

원본 근거:

- `functions/bigquery-schema/*.json`
- `functions/src/index.ts`
- `functions/src/telemetryBigQueryMapper.ts`
- `functions/bigquery-views/*.sql`
