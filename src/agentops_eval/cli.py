"""A single CLI for execution, fair comparison, inspection and local serving."""

import argparse
import asyncio
import json
import random
from pathlib import Path

from agentops_eval.adapters.base import AdapterError
from agentops_eval.adapters.deterministic import DeterministicBaselineAgent
from agentops_eval.domain.models import RunConfig, RunRecord
from agentops_eval.infra.storage import RunStore, export_record
from agentops_eval.services.comparison import compare_runs, write_comparison
from agentops_eval.services.datasets import load_dataset
from agentops_eval.services.metrics import run_metrics
from agentops_eval.services.runner import run_evaluation


def get_adapter(name: str):
    if name in {"baseline", "baseline_v1", "candidate_v2"}:
        return DeterministicBaselineAgent("candidate_v2" if name == "baseline" else name)
    if name == "campus_evidence":
        from agentops_eval.adapters.campus import CampusEvidenceAdapter

        return CampusEvidenceAdapter()
    if name == "openai":
        from agentops_eval.adapters.openai_compatible import OpenAICompatibleAdapter

        return OpenAICompatibleAdapter()
    from agentops_eval.adapters.http import ResearchAgentAdapter, WorkspaceHTTPAgentAdapter

    return WorkspaceHTTPAgentAdapter() if name == "workspace" else ResearchAgentAdapter()


def read_record(path: Path) -> RunRecord:
    if path.stat().st_size > 50_000_000:
        raise ValueError("run file exceeds 50 MB")
    return RunRecord.model_validate_json(path.read_text())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eval")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="execute cases, persist to SQLite, export JSON and JSONL")
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument(
        "--agent",
        choices=[
            "baseline",
            "baseline_v1",
            "candidate_v2",
            "campus_evidence",
            "workspace",
            "research",
            "openai",
        ],
        default="baseline",
    )
    run.add_argument("--output", type=Path, default=Path("runtime/run.json"))
    run.add_argument("--db", type=Path, default=Path("runtime/evaluations.db"))
    run.add_argument("--limit", type=int)
    run.add_argument("--category", choices=["arithmetic", "retrieval", "recovery", "long_input"])
    run.add_argument("--difficulty", choices=["easy", "medium", "hard"])
    run.add_argument("--case-id", action="append")
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--concurrency", type=int, default=4)
    run.add_argument("--max-retries", type=int, default=0)
    run.add_argument("--tool-retries", type=int, default=1)
    run.add_argument("--no-planner", action="store_true")
    run.add_argument("--tool-descriptions", choices=["basic", "improved"], default="improved")
    run.add_argument(
        "--sensitive-trace",
        action="store_true",
        help="opt in to content storage; common secrets still redacted",
    )
    compare = sub.add_parser("compare", help="paired case-cluster bootstrap comparison")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--output", type=Path, default=Path("reports/results.json"))
    compare.add_argument("--allow-ablation", action="store_true")
    serve = sub.add_parser("serve", help="local dashboard and read API")
    serve.add_argument("--db", type=Path, default=Path("runtime/evaluations.db"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    inspect = sub.add_parser("inspect", help="read saved run metrics")
    inspect.add_argument("run_id", nargs="?")
    inspect.add_argument("--db", type=Path, default=Path("runtime/evaluations.db"))
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "run":
            config = RunConfig(
                seed=args.seed,
                repeats=args.repeats,
                concurrency=args.concurrency,
                max_retries=args.max_retries,
                planner=not args.no_planner,
                tool_retries=args.tool_retries,
                tool_descriptions=args.tool_descriptions,
                sensitive_trace=args.sensitive_trace,
            )
            cases = load_dataset(args.dataset)
            if args.category:
                cases = [c for c in cases if c.category == args.category]
            if args.difficulty:
                cases = [c for c in cases if c.difficulty == args.difficulty]
            if args.case_id:
                if set(args.case_id) - {c.case_id for c in cases}:
                    raise ValueError("unknown case ID in current selection")
                cases = [c for c in cases if c.case_id in args.case_id]
            if args.limit is not None:
                if args.limit <= 0:
                    raise ValueError("limit must be positive")
                indexes = sorted(
                    random.Random(args.seed).sample(range(len(cases)), min(args.limit, len(cases)))
                )
                cases = [cases[i] for i in indexes]
            record = asyncio.run(run_evaluation(cases, get_adapter(args.agent), config))
            RunStore(args.db).save(record)
            export_record(record, args.output)
            print(
                json.dumps(
                    {
                        "run_id": record.run_id,
                        "agent": record.agent,
                        "metrics": run_metrics(record),
                        "output": str(args.output),
                    },
                    indent=2,
                )
            )
        elif args.command == "compare":
            comparison = compare_runs(
                read_record(args.baseline),
                read_record(args.candidate),
                allow_ablation=args.allow_ablation,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(comparison, indent=2) + "\n")
            write_comparison(comparison, args.output.with_suffix(".md"))
            print(
                json.dumps(
                    {
                        k: comparison[k]
                        for k in [
                            "absolute_success_delta",
                            "case_clusters",
                            "bootstrap_ci_95",
                            "warning",
                        ]
                    },
                    indent=2,
                )
            )
        elif args.command == "inspect":
            store = RunStore(args.db)
            if args.run_id:
                record = store.get(args.run_id)
                if record is None:
                    raise ValueError("run not found")
                print(json.dumps(run_metrics(record), indent=2))
            else:
                print(json.dumps(store.list(), indent=2))
        else:
            import uvicorn

            from agentops_eval.api.app import create_app

            if not 1 <= args.port <= 65535:
                raise ValueError("invalid port")
            uvicorn.run(create_app(args.db), host=args.host, port=args.port)
    except (ValueError, OSError, AdapterError) as exc:
        # No upstream messages, paths, prompts or credentials in normal error output.
        parser.exit(2, f"evaluation error: {type(exc).__name__}; check input/configuration\n")


if __name__ == "__main__":
    main()
