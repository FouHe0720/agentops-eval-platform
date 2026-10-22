import argparse
import asyncio
from pathlib import Path

from agentops_eval.adapters.deterministic import DeterministicBaselineAgent
from agentops_eval.domain.models import RunConfig
from agentops_eval.services.datasets import load_dataset
from agentops_eval.services.runner import run_evaluation


def main() -> None:
    parser = argparse.ArgumentParser(prog="eval")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument(
        "--agent", choices=["baseline", "baseline_v1", "candidate_v2"], default="baseline"
    )
    run.add_argument("--output", type=Path, default=Path("runtime/run.json"))
    run.add_argument("--limit", type=int)
    args = parser.parse_args()
    try:
        cases = load_dataset(args.dataset)
        if args.limit:
            cases = cases[: args.limit]
        adapter = DeterministicBaselineAgent(
            "candidate_v2" if args.agent == "baseline" else args.agent
        )
        record = asyncio.run(run_evaluation(cases, adapter, RunConfig()))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(record.model_dump_json(indent=2) + "\n")
        print(f"run_id={record.run_id} results={len(record.results)} output={args.output}")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"evaluation error: {type(exc).__name__}\n")


if __name__ == "__main__":
    main()
