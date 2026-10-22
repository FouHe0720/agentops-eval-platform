"""CI gate: execute the 10-case offline candidate regression and check both contracts."""

import argparse
import asyncio
import json
from pathlib import Path

from agentops_eval.adapters.deterministic import DeterministicBaselineAgent
from agentops_eval.domain.models import RunConfig
from agentops_eval.services.datasets import load_dataset
from agentops_eval.services.metrics import run_metrics
from agentops_eval.services.runner import run_evaluation

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "datasets/regression.json")
    args = parser.parse_args()
    try:
        cases = load_dataset(args.dataset)
        record = asyncio.run(
            run_evaluation(cases, DeterministicBaselineAgent("candidate_v2"), RunConfig(seed=42))
        )
    except (ValueError, OSError) as exc:
        parser.exit(2, f"regression error: {type(exc).__name__}; check the dataset\n")
    failed = [
        {
            "case_id": result.case_id,
            "repeat": result.repeat,
            "status": result.status,
            "success": result.success,
            "tool_selection_correct": result.tool_selection_correct,
            "failure": result.failure,
        }
        for result in record.results
        if result.status != "completed"
        or result.success is not True
        or result.tool_selection_correct is not True
    ]
    print(
        json.dumps(
            {
                "benchmark": "Synthetic offline regression; no model/API calls.",
                "passed": not failed,
                "cases": len(cases),
                "run_id": record.run_id,
                "dataset_digest": record.dataset_digest,
                "metrics": run_metrics(record),
                "failed_results": failed,
            },
            indent=2,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
