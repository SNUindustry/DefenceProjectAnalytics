# Common Analytics Report Contract v1

## Versioning

Every `metadata.json` contains `reportContractVersion: "1.0.0"` and an analysis-specific `analysisVersion`. A breaking field, meaning, denominator, population, or file-layout change requires a major version change. Additive backward-compatible fields require a minor version change; clarifications and renderer fixes may use a patch version.

## Bundle layout

```text
reports/generated/
└─ <analysis-type>/
   └─ <environment>__<stage-slug>__cv-<version>__<scope-hash8>/
      ├─ report.md
      ├─ metrics.json
      ├─ metadata.json
      └─ tables/
         └─ *.csv
```

The eight-character suffix is the beginning of the SHA-256 digest of canonical scope JSON. The scope includes environment, stage, content version, every optional release filter, development-build filter, and UTC upload-time bounds. This makes reports with different effective populations distinct.

Existing targets are not overwritten by default. Explicit overwrite is allowed only when the existing metadata has the same analysis type and exact scope. Installation is performed through a sibling temporary directory.

## JSON

- External field names use `camelCase`.
- Dataclass field order defines canonical key order; scope hashing separately sorts keys.
- Files are UTF-8, indented by two spaces, and end with LF.
- `NaN` and pandas missing values become JSON `null`; positive or negative infinity is rejected.
- UTC timestamps have second precision and end in `Z`.
- Every ratio uses `{ "count", "denominator", "ratio" }`; `ratio` is `null` when the denominator is zero.
- `metrics.json` contains aggregate analysis results. `metadata.json` contains scope, sample, quality, definitions, warnings, generation time, and dry-run estimated bytes.

## CSV

CSV files are the canonical full breakdowns. Headers and sort keys are fixed per analysis. Numeric floats use up to 12 significant digits; files are UTF-8 with LF. A supported table with no rows is emitted as a header-only file. CSV rows contain aggregates only and never player, attempt, run, or upload identifiers.

Stage Difficulty provides:

- death time buckets and phase/floor/wave/zone distributions;
- final death source, enemy, and available environment-effect distributions;
- incoming enemy damage and damage-source distributions;
- Dead/Clear threat summaries;
- aggregate selected player-state distributions.

## Markdown

`report.md` is a human/LLM-readable index of scope, sample, quality, outcome, timing, concentration, causes, damage, threat, state, deterministic statistical signals, caveats, and attached tables. Percentages and seconds display at one decimal place. It must not contain tuning judgments or recommendations.

Signals are emitted only for fixed descriptive thresholds:

- a concentration share of at least 50% when its denominator reaches the configured death threshold;
- a final lethal enemy share of at least 20% with at least five deaths;
- an incoming enemy damage share of at least 20% across at least five affected runs.

## Privacy and scope

Generated reports contain aggregates only. They do not store `telemetryPlayerId`, `attemptId`, `runId`, or `uploadId`. Queries use ADC and read-only BigQuery access. The contract does not authorize cloud mutation, raw object access, credential creation, or telemetry upload secrets.
