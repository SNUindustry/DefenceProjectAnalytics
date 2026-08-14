# DefenceProjectAnalytics

대머리 특공대 telemetry 기반 balancing 분석 전용 repository입니다.

- Data Source: BigQuery `bald-ops.game_telemetry`
- Access: Application Default Credentials(ADC) + read-only BigQuery IAM
- Start Here: [`docs/handoff/README.md`](docs/handoff/README.md)
- Schema Contracts: [`contracts/bigquery-schema/`](contracts/bigquery-schema/)
- Canonical SQL: [`sql/canonical/`](sql/canonical/)
- Foundation SQL: [`sql/foundation/`](sql/foundation/)

Unity telemetry upload secret, Firebase credential, service-account key 또는 raw telemetry를 이 repository에 복사하거나 사용하지 않습니다.
