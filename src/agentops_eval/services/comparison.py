"""Paired, case-clustered bootstrap. Repeats are not independent samples."""

import random
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from agentops_eval.domain.models import RunRecord
from agentops_eval.services.metrics import percentile, run_metrics


def compare_runs(
    baseline: RunRecord,
    candidate: RunRecord,
    *,
    allow_ablation: bool = False,
    bootstrap_samples: int = 2000,
) -> dict[str, Any]:
    if baseline.dataset_digest != candidate.dataset_digest:
        raise ValueError("comparison requires identical cases and dataset digest")
    left, right = baseline.config.model_dump(), candidate.config.model_dump()
    allowed = {"planner", "tool_descriptions", "tool_retries"} if allow_ablation else set()
    changed = {key for key in left if left[key] != right[key]}
    if changed - allowed:
        raise ValueError("comparison requires identical config and seed")
    if bootstrap_samples < 100 or bootstrap_samples > 10000:
        raise ValueError("bootstrap_samples must be 100..10000")

    def indexed(record: RunRecord):
        pairs = {(r.case_id, r.repeat): r for r in record.results}
        if len(pairs) != len(record.results):
            raise ValueError("duplicate case/repeat in run")
        expected = {(c.case_id, r) for c in record.cases for r in range(record.config.repeats)}
        if set(pairs) != expected:
            raise ValueError("incomplete case/repeat coverage")
        return pairs

    a, b = indexed(baseline), indexed(candidate)
    if a.keys() != b.keys():
        raise ValueError("comparison requires identical case/repeat pairs")
    rows, groups = [], defaultdict(list)
    for key in sorted(a):
        x, y = a[key], b[key]
        eligible = x.success is not None and y.success is not None
        if x.status == "skipped" or y.status == "skipped":
            eligible = False
        delta = int(y.success) - int(x.success) if eligible else None
        rows.append(
            {
                "case_id": key[0],
                "repeat": key[1],
                "baseline_success": x.success,
                "candidate_success": y.success,
                "delta": delta,
                "baseline_failure": x.failure,
                "candidate_failure": y.failure,
            }
        )
        if delta is not None:
            groups[key[0]].append(delta)
    deltas = [mean(values) for values in groups.values()]
    interval = None
    warning = None
    if len(deltas) < 10:
        warning = "Fewer than 10 eligible case clusters: confidence interval omitted."
    else:
        rng = random.Random(baseline.config.seed)
        samples = [mean(rng.choices(deltas, k=len(deltas))) for _ in range(bootstrap_samples)]
        interval = [percentile(samples, 0.025), percentile(samples, 0.975)]
    return {
        "baseline_run_id": baseline.run_id,
        "candidate_run_id": candidate.run_id,
        "dataset_digest": baseline.dataset_digest,
        "changed_config": sorted(changed),
        "paired_results": sum(len(v) for v in groups.values()),
        "case_clusters": len(deltas),
        "absolute_success_delta": mean(deltas) if deltas else None,
        "bootstrap_ci_95": interval,
        "bootstrap_samples": bootstrap_samples,
        "warning": warning,
        "rows": rows,
        "baseline_metrics": run_metrics(baseline),
        "candidate_metrics": run_metrics(candidate),
    }


def write_comparison(comparison: dict[str, Any], path: Path) -> None:
    delta = comparison["absolute_success_delta"]
    lines = [
        "# Paired evaluation comparison",
        "",
        "Synthetic fixture benchmark; this is not evidence of production agent quality.",
        "",
        f"Baseline run: `{comparison['baseline_run_id']}`",
        f"Candidate run: `{comparison['candidate_run_id']}`",
        "",
        f"Eligible case clusters: {comparison['case_clusters']}",
        f"Absolute task success delta: {delta}",
        f"Case-cluster bootstrap 95% CI: {comparison['bootstrap_ci_95']}",
        f"Changed config (explicit ablation only): {comparison['changed_config']}",
        "",
    ]
    if comparison["warning"]:
        lines.extend([comparison["warning"], ""])
    lines.extend(["| case | repeat | baseline | candidate | delta |", "|---|---:|---|---|---:|"])
    for row in comparison["rows"]:
        lines.append(
            f"| {row['case_id']} | {row['repeat']} | {row['baseline_success']} | "
            f"{row['candidate_success']} | {row['delta']} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
