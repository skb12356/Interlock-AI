# Interlock release evidence

Overall status: **MISS**

> The 300-example OpenRouter anchor audit is human-reviewed. Other evidence is generated or offline unless its row says otherwise. No production traffic is claimed.

| Check | Status | Value | Evidence |
| --- | --- | --- | --- |
| seeded_safety | pass | {'worst_catch_rate': 1.0, 'worst_escape_rate': 0.0} | artifacts/eval/report-seed-*.json |
| false_intervention | miss | 0.6496815286624203 | artifacts/eval/report-seed-*.json |
| openrouter_anchor | inconclusive | {'binary_agreement': 0.8766666666666667, 'judge_false_positive_rate': 0.085} | artifacts/eval/manual_anchor_report.json |
| calibration | pass | 0.0037127486047197587 | artifacts/calibration/report.json |
| conformal_operability | inconclusive | {'escape_rate': 0.0, 'intervention_rate': 1.0} | artifacts/calibration/lambda.json |
| load_latency | miss | {'n': 2000, 'budget_ms': 120.0, 'overhead_p50_ms': 343.0, 'overhead_p95_ms': 531.0, 'overhead_max_ms': 672.0, 'within_budget': False, 'ttft_p50_ms': 234.0, 'ttft_p95_ms': 469.0, 'buffered': {'n': 0, 'p95_ms': 0.0}, 'unbuffered': {'n': 2000, 'p95_ms': 531.0}, 'by_lane_p95_ms': {'lane_a': 422.0}, 'unattributed_mean_ms': 123.26, 'notes': ['123.3 ms of mean overhead is unattributed to any lane -- something is spending time nobody instrumented', 'p95 overhead 531 ms exceeds the 120 ms budget'], 'caveat': "Lane B runs concurrently with generation; only the portion the commit gate actually waited on ('gate_hold') is counted, because the rest is not time the customer spent waiting."} | artifacts/load/load_pass.json |
| fairness | inconclusive | {'n_pairs': 5, 'offline': True} | artifacts/eval/fairness_run.json |
| security_sweep | pass | True | artifacts/security/security_sweep.json |
| production_economics | unavailable | None |  |
| penetration_test | not_run | None |  |

## Seeded metrics

- Pre-Action Catch Rate: [1.0, 1.0, 1.0]
- Added p95 latency: [0.40191600471735, 0.4236669987440109, 0.4125000014901161]
- Verification cost: [0.0558252427184466, 0.05175718849840256, 0.0560126582278481]
- Net spend change: [-0.17799999999999983, -0.17013333333333316, -0.15813333333333318]
- Ungrounded escapes: [0.0, 0.0, 0.0]
- False interventions: [0.6305732484076433, 0.6050955414012739, 0.6496815286624203]
-   ...of those, disruptive: [0.6305732484076433, 0.6050955414012739, 0.6496815286624203]
-   ...Rs.0-100: [0.0, 0.0, 0.0]
-   ...Rs.100-1,000: [0.0, 0.0, 0.0]
-   ...Rs.1,000-10,000: [0.0, 0.0, 0.0]
-   ...Rs.10,000+: [1.0, 1.0, 1.0]
- Twin pairs treated alike: [1.0, 1.0, 1.0]
