"""Run an offline paired benchmark and three controlled ablations with real traces."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from agentops_eval.adapters.deterministic import DeterministicBaselineAgent
from agentops_eval.domain.models import EvalCase, RunConfig, RunRecord
from agentops_eval.infra.storage import RunStore, export_record
from agentops_eval.services.comparison import compare_runs, write_comparison
from agentops_eval.services.datasets import load_dataset
from agentops_eval.services.metrics import run_metrics
from agentops_eval.services.runner import run_evaluation

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = "Synthetic fixture benchmark; not evidence of production agent quality."


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_ablations(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Controlled ablation results",
        "",
        BENCHMARK,
        "",
        f"Reference candidate run: `{report['reference_run_id']}`",
        f"Dataset digest: `{report['dataset_digest']}`",
        f"Repeats per case: {report['config']['repeats']}; seed: {report['config']['seed']}",
        "",
        "Each comparison changes one option. Delta = ablation minus full candidate.",
        "Confidence intervals resample case clusters, keeping repeats together.",
        "Latency is observed execution time and may vary across runs.",
        "",
        "| Configuration | Success / scored | Tool selection / scored | "
        "Success delta | Bootstrap 95% CI | P50 / P95 latency (ms) |",
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
    for name, comparison in report["ablations"].items():
        metrics = comparison["candidate_metrics"]
        success = metrics["task_success_rate"]
        selection = metrics["tool_selection_accuracy"]
        latency = metrics["latency_ms"]
        lines.append(
            f"| {name} | {success['numerator']} / {success['denominator']} | "
            f"{selection['numerator']} / {selection['denominator']} | "
            f"{comparison['absolute_success_delta']} | {comparison['bootstrap_ci_95']} | "
            f"{latency['p50']} / {latency['p95']} |"
        )
        if comparison["warning"]:
            lines.extend(["", f"{name}: {comparison['warning']}", ""])
    lines.extend(
        [
            "",
            "Full metrics, denominators, run IDs and changed options are in `ablations.json`.",
            "No model calls are made; unobserved model tokens and cost remain null.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


async def execute_and_save(
    cases: list[EvalCase],
    agent_name: str,
    config: RunConfig,
    store: RunStore,
    output: Path,
) -> RunRecord:
    record = await run_evaluation(cases, DeterministicBaselineAgent(agent_name), config)
    safe = store.save(record)
    export_record(safe, output)
    return safe


async def run_demo(
    dataset: Path, runtime_dir: Path, reports_dir: Path, repeats: int = 3
) -> dict[str, Any]:
    config = RunConfig(seed=42, repeats=repeats)
    cases = load_dataset(dataset)
    store = RunStore(runtime_dir / "demo.db")
    baseline = await execute_and_save(
        cases, "baseline_v1", config, store, runtime_dir / "baseline.json"
    )
    candidate = await execute_and_save(
        cases, "candidate_v2", config, store, runtime_dir / "candidate.json"
    )
    records = {"baseline": baseline, "candidate": candidate}
    comparison = compare_runs(baseline, candidate)
    write_json(reports_dir / "results.json", comparison)
    write_comparison(comparison, reports_dir / "comparison.md")

    ablations = {}
    variants = {
        "no_planner": {"planner": False},
        "basic_tool_descriptions": {"tool_descriptions": "basic"},
        "no_tool_retries": {"tool_retries": 0},
    }
    for name, changed in variants.items():
        ablated_config = RunConfig.model_validate({**config.model_dump(), **changed})
        ablated = await execute_and_save(
            cases, "candidate_v2", ablated_config, store, runtime_dir / f"{name}.json"
        )
        records[name] = ablated
        result = compare_runs(candidate, ablated, allow_ablation=True)
        ablations[name] = {key: value for key, value in result.items() if key != "rows"}
    ablation_report = {
        "benchmark": BENCHMARK,
        "reference_run_id": candidate.run_id,
        "dataset_digest": candidate.dataset_digest,
        "config": config.model_dump(mode="json"),
        "ablations": ablations,
    }
    write_json(reports_dir / "ablations.json", ablation_report)
    write_ablations(ablation_report, reports_dir / "ablations.md")
    write_json(
        reports_dir / "run_manifest.json",
        {
            "benchmark": BENCHMARK,
            "seed": config.seed,
            "cases": len(cases),
            "repeats": config.repeats,
            "dataset_digest": candidate.dataset_digest,
            "runs": [
                {
                    "artifact": name,
                    "run_id": record.run_id,
                    "agent": record.agent,
                    "created_at": record.created_at.isoformat(),
                    "config": record.config.model_dump(mode="json"),
                    "metrics": run_metrics(record),
                }
                for name, record in records.items()
            ],
        },
    )
    example = next(
        (
            result
            for result in candidate.results
            if result.repeat == 0
            and result.category == "recovery"
            and any(event.event_type == "retry" for event in result.trace.events)
        ),
        candidate.results[0],
    )
    with (reports_dir / "example-trace.jsonl").open("w", encoding="utf-8") as handle:
        for event in example.trace.events:
            handle.write(
                json.dumps(
                    {
                        "case_id": example.case_id,
                        "repeat": example.repeat,
                        **event.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return {
        "benchmark": BENCHMARK,
        "cases": len(cases),
        "repeats": repeats,
        "results_per_run": len(candidate.results),
        "dataset_digest": candidate.dataset_digest,
        "baseline_run_id": baseline.run_id,
        "candidate_run_id": candidate.run_id,
        "baseline_metrics": run_metrics(baseline),
        "candidate_metrics": run_metrics(candidate),
        "absolute_success_delta": comparison["absolute_success_delta"],
        "bootstrap_ci_95": comparison["bootstrap_ci_95"],
        "ablation_success_deltas": {
            name: item["absolute_success_delta"] for name, item in ablations.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "datasets/synthetic.json")
    parser.add_argument("--runtime-dir", type=Path, default=ROOT / "runtime")
    parser.add_argument("--reports-dir", type=Path, default=ROOT / "reports")
    parser.add_argument("--repeats", type=int, choices=range(1, 11), default=3)
    args = parser.parse_args()
    try:
        summary = asyncio.run(
            run_demo(args.dataset, args.runtime_dir, args.reports_dir, args.repeats)
        )
    except (ValueError, OSError) as exc:
        parser.exit(2, f"demo error: {type(exc).__name__}; check input and output directories\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
