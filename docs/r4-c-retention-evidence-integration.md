# R4-C Retention Evidence Integration

R4-C adds the aggregate `retentionEvidence` R4-B bundle as an optional C-1
single-version domain. It does not change any B-7 or B-8 metric, SQL, or report
contract. C-1 reads only the normal aggregate bundle, verifies its manifest, and
rejects raw profile, player, process, occurrence, and episode identifiers.

Each selected R4 evidence item carries an explicit `authority` object. The object
preserves the evidence class, subject level, factual eligibility, static comparison
capability, runtime comparison decision and reasons, fixed horizon, denominator
semantics, maturity state, source finalization states, and safe source-cut digest
references. Decision, target, guardrail, and rollback eligibility are always false.
Unknown or missing runtime comparison authority is treated as `Denied`.

C-2 applies the following stages:

- Stage A may cite R4 evidence for observations, bounded interpretations, and
  evidence gaps. Observed uninstall does not mean permanent loss. A bounded
  absence is not churn, and right-censoring is not a non-return outcome.
- Stage B cannot use R4 evidence to support hypotheses or change candidates.
- Stage C may place one of the six comparison-capable R4 return metrics in
  `metricsToWatch` only when the selected C-1 evidence has runtime comparison
  decision `Allowed`. This is `MonitorOnly` authority. R4 metrics remain invalid as
  a target, guardrail, rollback indicator, expected direction, or change target.

The Anthropic compact transport includes the authority object in its exact
canonical round trip. Its metric alias catalog contains the existing target metrics
and the six R4 MonitorOnly candidates. The local canonical validator remains the
final authority and rejects a candidate everywhere except the permitted
`metricsToWatch` context.

Run the local contract validator without ADC or BigQuery access:

```text
defence-project-analytics validate-r4c-contracts
defence-project-analytics validate-r4c-contracts --normal-bundle <r4-b-bundle>
```

The first command checks registry and C-1/C-2 registration. The second also checks
the R4-B manifest, privacy boundary, exact 11-item factual projection, and authority
shape.
