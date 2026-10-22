"""Create inspectable, fixture-only synthetic benchmarks; no adapter is used as an oracle."""

import argparse
import json
import random
from pathlib import Path

from agentops_eval.domain.models import Assertion, EvalCase

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def arithmetic_cases(rng: random.Random, seed: int) -> list[EvalCase]:
    boundaries = [
        ("add", 0, 0),
        ("subtract", 0, 7),
        ("multiply", -4, -9),
        ("add", 10**12, 1),
        ("subtract", -20, -5),
        ("multiply", 0, 10**12),
        ("add", 0.5, 0.25),
        ("subtract", 0.75, 0.25),
        ("multiply", 0.5, -8),
        ("add", -100, 100),
        ("subtract", 10**12, -(10**12)),
        ("multiply", -1, 10**12),
    ]
    operations = ("add", "subtract", "multiply")
    specs = boundaries + [
        (operations[i % 3], rng.randint(-10000, 10000), rng.randint(-10000, 10000))
        for i in range(18)
    ]
    cases = []
    for i, (operation, a, b) in enumerate(specs, 1):
        # Deliberately independent of the agent's OPS dispatch table.
        if operation == "add":
            answer = a + b
        elif operation == "subtract":
            answer = a - b
        else:
            answer = a * b
        cases.append(
            EvalCase(
                case_id=f"arithmetic-{i:03d}",
                task=f"Use the calculator to {operation} {a} and {b}; return only the result.",
                category="arithmetic",
                difficulty="medium" if abs(a) >= 10**12 or isinstance(a, float) else "easy",
                expected_answer=str(answer),
                expected_tool_sequence=["calculator"],
                fixtures={"operation": operation, "a": a, "b": b},
                tags=["synthetic", f"seed-{seed}", operation, "boundary" if i <= 12 else "sampled"],
            )
        )
    return cases


def retrieval_cases(seed: int) -> list[EvalCase]:
    cases = []
    for i in range(30):
        topic = f"topic-{i:02d}"
        mode = i % 5
        files = {"index.txt": f"Synthetic index for {topic}; this filename is not a match."}
        if mode == 0:
            query = f"missing-{i:02d}"
        else:
            query = topic
            if mode == 1:
                files[f"notes/{topic}-guide.txt"] = f"Synthetic guide {i}: status ready."
            elif mode == 2:
                # Insertion order intentionally differs from alphabetical filename order.
                files[f"z/{topic}-last.txt"] = f"Section Z{i}"
                files[f"a/{topic}-first.txt"] = f"Section A{i}"
                files[f"m/{topic}-middle.txt"] = f"Section M{i}"
            elif mode == 3:
                files[f"示例/{topic}-说明.txt"] = f"合成说明 {i}：无需联网。"
            else:
                files[f"notes/{topic}-empty.txt"] = ""
                files[f"notes/{topic}-detail.txt"] = f"Synthetic detail {i}\nSecond line."
            # File content is irrelevant to filename search, and matching is case-sensitive.
            files[f"distractors/{topic.upper()}.txt"] = f"Uppercase distractor {i}"
        ambiguous = i % 3 != 0
        multi_step = i % 2 == 0
        matches = [name for name in files if query in name]
        answer = "|".join(files[name] for name in matches) if matches else "NOT_FOUND"
        assertions = [Assertion(kind="not_contains", value="Uppercase distractor")]
        if mode == 3:
            assertions.append(Assertion(kind="contains", value="合成说明"))
        cases.append(
            EvalCase(
                case_id=f"retrieval-{i + 1:03d}",
                task=(
                    f"Search fixture filenames for the case-sensitive substring {query!r}. "
                    "Read every matching file in fixture insertion order and join complete "
                    "contents with '|'. Return NOT_FOUND if no filenames match."
                ),
                category="retrieval",
                difficulty="hard" if len(matches) > 1 else "medium" if ambiguous else "easy",
                expected_answer=answer,
                expected_tool_sequence=["search"] + ["read_file"] * len(matches),
                assertions=assertions,
                fixtures={
                    "files": files,
                    "query": query,
                    "ambiguous": ambiguous,
                    "multi_step": multi_step,
                },
                tags=[
                    "synthetic",
                    f"seed-{seed}",
                    "ambiguous-tools" if ambiguous else "clear-tools",
                    "multi-step" if multi_step else "single-step",
                    f"matches-{len(matches)}",
                ],
            )
        )
    return cases


def recovery_cases(seed: int) -> list[EvalCase]:
    cases = []
    for i in range(20):
        failures = 0 if i % 4 == 0 else 1
        payload = [
            f"Synthetic payload {i}: complete.",
            f"合成载荷 {i}：成功。",
            f'{{"synthetic_id": {i}, "status": "ready"}}',
            "",
        ][i % 4]
        cases.append(
            EvalCase(
                case_id=f"recovery-{i + 1:03d}",
                task=(
                    "Fetch the synthetic service payload. Retry a transient tool failure "
                    "once if needed, then return the complete payload unchanged."
                ),
                category="recovery",
                difficulty="medium" if failures else "easy",
                expected_answer=payload,
                expected_tool_sequence=["fetch"] * (failures + 1),
                assertions=[Assertion(kind="not_contains", value="TOOL_FAILED")],
                fixtures={"failures_before_success": failures, "payload": payload},
                tags=["synthetic", f"seed-{seed}", "transient-error" if failures else "healthy"],
            )
        )
    return cases


def long_input_cases(rng: random.Random, seed: int) -> list[EvalCase]:
    cases = []
    lengths = [0, 1, 2, 3, 10, 11, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256]
    boundaries = [[], [0], [-7, 7], [2**40, -(2**40), 17]]
    for i, length in enumerate(lengths):
        if i < len(boundaries):
            values = boundaries[i]
        elif i % 4 == 0:
            values = [0] * length
        elif i % 4 == 1:
            values = [j if j % 2 else -j for j in range(length)]
        elif i % 4 == 2:
            values = [-rng.randint(0, 10000) for _ in range(length)]
        else:
            values = [rng.randint(-10000, 10000) for _ in range(length)]
        # A simple fold is an independent oracle for the adapter's aggregate tool.
        total = 0
        for value in values:
            total += value
        cases.append(
            EvalCase(
                case_id=f"long-input-{i + 1:03d}",
                task=(
                    f"Plan and aggregate all {len(values)} numeric values in the fixture. "
                    "Return their exact sum, including zero for an empty array."
                ),
                category="long_input",
                difficulty="hard" if length > 64 else "medium" if length > 10 else "easy",
                expected_answer=str(total),
                expected_tool_sequence=["aggregate"],
                fixtures={"values": values},
                tags=["synthetic", f"seed-{seed}", f"length-{length}", "aggregation"],
            )
        )
    invalid_values = [None, ["NaN"], "3,4", [1, {"unexpected": 2}]]
    for i, values in enumerate(invalid_values, 17):
        cases.append(
            EvalCase(
                case_id=f"long-input-{i:03d}",
                task=(
                    "The fixture is explicitly marked invalid. Use validation and return "
                    "INVALID without aggregating its malformed values."
                ),
                category="long_input",
                difficulty="medium",
                expected_answer="INVALID",
                expected_tool_sequence=["validate"],
                fixtures={"invalid": True, "values": values},
                tags=["synthetic", f"seed-{seed}", "invalid-input", "flagged-validation"],
            )
        )
    return cases


def build_datasets(seed: int = 42) -> tuple[list[EvalCase], list[EvalCase]]:
    rng = random.Random(seed)
    cases = (
        arithmetic_cases(rng, seed)
        + retrieval_cases(seed)
        + recovery_cases(seed)
        + long_input_cases(rng, seed)
    )
    regression_ids = {
        "arithmetic-001",
        "arithmetic-003",
        "arithmetic-007",
        "retrieval-001",
        "retrieval-003",
        "retrieval-005",
        "recovery-001",
        "recovery-002",
        "long-input-016",
        "long-input-017",
    }
    return cases, [case for case in cases if case.case_id in regression_ids]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "datasets")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    synthetic, regression = build_datasets(args.seed)
    for filename, cases in (("synthetic.json", synthetic), ("regression.json", regression)):
        payload = [case.model_dump(mode="json") for case in cases]
        (args.output_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Wrote {len(cases)} synthetic cases: {filename} (seed={args.seed})")


if __name__ == "__main__":
    main()
