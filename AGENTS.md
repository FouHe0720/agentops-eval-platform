# Repository instructions

## Entry points
- `src/agentops_eval/cli.py`: `eval run`, `compare`, `inspect`, `serve`.
- `domain/models.py`: strict data and serialization contracts.
- `services/runner.py`, `tracing.py`, `scoring.py`, `metrics.py`, `comparison.py`: execution through analysis.
- `adapters/`: fixture-only deterministic tools and explicitly configured HTTP/model services.
- `infra/privacy.py`, `storage.py`: shared persistence/export boundary.
- `api/app.py`, `dashboard.html`: same-origin local dashboard and annotations.
- `scripts/demo.py`: regenerate all synthetic reports, manifest and actual trace together.

## Local workflow
- Install with `make install` (uv frozen project environment); use `.venv/bin/python`.
- Validate with `make lint test regression`; CI also checks dataset regeneration and privacy.
- Run `make demo` for 100 cases × 3 repeats and three controlled ablations.
- Run `make serve` against `runtime/demo.db`; a fresh clone needs a demo first.
- Before publication stage intended files, then run `scripts/privacy_check.py` to inspect both index blobs and working files.

## Project-specific constraints
- Never send expected answers, assertions or expected tools to adapters.
- Preserve null for unavailable metrics; report denominators and token/cost observation coverage.
- Keep repeats clustered by case during bootstrap and require compatible dataset/config/seed.
- Separate runner retries from internal tool retries; remote operations may require idempotency.
- Persist content only through the shared privacy boundary; keep `.env`, databases and private exports ignored.
- Publish only synthetic data and reviewed reports; identify live-service tests separately.
- Treat asyncio timeouts as cooperative cancellation, not process isolation.

## Gotchas
- `--agent baseline` resolves to the current deterministic `candidate_v2`; use explicit version names for comparisons.
- The latest demo run is an ablation; select the compatible candidate config for default A/B.
- Default saved cases have content removed and cannot independently reconstruct grading inputs.
- Changing report runs also changes IDs and timings; regenerate screenshots only from corresponding saved runs.
- FastAPI's current TestClient uses the dev-only httpx2 dependency; production adapters use httpx.
- Browser screenshots are optional acceptance artifacts; core/CI do not need Playwright or system browser libraries.
