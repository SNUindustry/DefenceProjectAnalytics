# Release Identity

각 필드는 독립적인 분석 차원입니다.

| Field | 의미 | 예상 값/주의점 |
|---|---|---|
| `environment` | telemetry routing/config 축 | 주 분석 값 `Test`, `Production`; legacy run server는 `Local`, `Debug`도 허용 |
| `appVersion` | 설치된 player binary의 `Application.version` | 문자열 버전 |
| `contentVersion` | 현재 release manifest의 content revision | manifest 부재 시 0 |
| `releaseId` | runtime catalog release ID, 없으면 manifest fallback | empty이면 unresolved 가능 |
| `releaseFolder` | release artifact의 물리적 folder 식별자 | release ID와 동일하다고 가정하지 않음 |
| `releaseChannel` | runtime pointer channel | `Production`, `Development` |
| `releaseType` | release scope | `ContentOnly`, `PlayerAndContent`, `HotFix` |
| `isDevelopmentBuild` | Unity `Debug.isDebugBuild` | telemetry environment와 별도 저장 |
| `catalogHash` | 실제 로드된 runtime catalog hash | content identity 비교에 사용 |
| `catalogSource` | catalog가 로드된 경로/상태 | `Unknown`, `StreamingAssets`, `PointerRelease`, `CachedRemote`, `UpdatedRemote` |
| `releaseIdentityResolved` | catalog source/hash가 알려졌거나 release manifest가 존재 | nullable legacy 주의 |

## 추론 금지

```text
environment = Test
≠ releaseChannel = Development
≠ releaseType = ContentOnly
≠ isDevelopmentBuild = TRUE
```

현재 runtime config 선택 로직 때문에 값들이 관련될 수 있지만, 한 field에서 다른 field를 역추론하지 않습니다. 실제 저장 열을 각각 group/filter합니다.

다음은 정상 release와 합치지 않고 unresolved cohort로 분리합니다.

```text
releaseIdentityResolved IS NOT TRUE
contentVersion = 0
empty releaseId
catalogSource = Unknown
```

원본 근거:

- `Assets/Scripts/ScenePlay/Telemetry/TelemetryIdentityProvider.cs`
- `Assets/Scripts/Distribution/RuntimeReleasePointerBootstrap.cs`
- `Assets/Scripts/Distribution/DistributionModels.cs`
- `Assets/Scripts/Economy/EconomyReleaseIdentity.cs`
- `functions/bigquery-schema/telemetry_run_summary.json`
- v2C event/feedback schema JSON
