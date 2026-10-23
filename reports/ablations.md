# Controlled ablation results

Synthetic fixture benchmark; not evidence of production agent quality.

Reference candidate run: `92a59864481841b49fc6e5da9ee9b3ea`
Dataset digest: `b00d4b1b785c20d9b0c3bcc23cb24d9ce71b42568244700623293ef59670c3fe`
Repeats per case: 3; seed: 42

Each comparison changes one option. Delta = ablation minus full candidate.
Confidence intervals resample case clusters, keeping repeats together.
Latency is observed execution time and may vary across runs.

| Configuration | Success / scored | Tool selection / scored | Success delta | Bootstrap 95% CI | P50 / P95 latency (ms) |
| --- | ---: | ---: | ---: | --- | --- |
| no_planner | 222 / 300 | 222 / 300 | -0.26 | [-0.34025, -0.18] | 0.23703150009168894 / 0.38430229994901316 |
| basic_tool_descriptions | 240 / 300 | 240 / 300 | -0.2 | [-0.28, -0.12] | 0.25777450014174974 / 0.4179167497568415 |
| no_tool_retries | 255 / 300 | 255 / 300 | -0.15 | [-0.22, -0.09] | 0.25811849991441704 / 0.48084810014188406 |

Full metrics, denominators, run IDs and changed options are in `ablations.json`.
No model calls are made; unobserved model tokens and cost remain null.
