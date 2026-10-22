import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentops_eval.domain.models import RunRecord
from agentops_eval.infra.storage import RunStore
from agentops_eval.services.metrics import run_metrics

ROOT = Path(__file__).resolve().parents[1]


def run_script(name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / name), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("repeats", [1, 3])
def test_demo_persists_real_paired_results_and_controlled_ablations(tmp_path, repeats):
    runtime, reports = tmp_path / "runtime", tmp_path / "reports"
    args = [
        "--dataset",
        str(ROOT / "datasets/regression.json"),
        "--runtime-dir",
        str(runtime),
        "--reports-dir",
        str(reports),
    ]
    if repeats != 3:
        args.extend(["--repeats", str(repeats)])
    completed = run_script("demo.py", *args)
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["cases"] == 10
    assert summary["repeats"] == repeats
    assert summary["results_per_run"] == 10 * repeats
    assert summary["candidate_metrics"]["task_success_rate"]["value"] == 1
    assert summary["candidate_metrics"]["token_usage"]["total"] is None
    assert summary["candidate_metrics"]["cost"]["total"] is None
    assert summary["absolute_success_delta"] > 0
    store = RunStore(runtime / "demo.db")
    assert len(store.list()) == 5
    records = {}
    for name in (
        "baseline",
        "candidate",
        "no_planner",
        "basic_tool_descriptions",
        "no_tool_retries",
    ):
        record = RunRecord.model_validate_json((runtime / f"{name}.json").read_text())
        records[name] = record
        assert store.get(record.run_id) == record
        assert record.config.repeats == repeats
        assert len(record.results) == 10 * repeats
        assert all(case.task == "[REDACTED]" for case in record.cases)
        assert all(case.fixtures == {} and case.expected_answer is None for case in record.cases)
        assert all(result.answer is None for result in record.results)
        events = [json.loads(line) for line in (runtime / f"{name}.jsonl").read_text().splitlines()]
        assert len(events) == sum(len(result.trace.events) for result in record.results)
        assert {event["run_id"] for event in events} == {record.run_id}
        assert any(event["event_type"] == "tool_call" for event in events)
        assert all("content" not in event["metadata"] for event in events)

    comparison = json.loads((reports / "results.json").read_text())
    assert len(comparison["rows"]) == 10 * repeats
    assert comparison["case_clusters"] == 10
    assert comparison["paired_results"] == 10 * repeats
    assert comparison["changed_config"] == []
    assert records["baseline"].config == records["candidate"].config
    assert comparison["baseline_metrics"] == run_metrics(records["baseline"])
    assert comparison["candidate_metrics"] == run_metrics(records["candidate"])
    assert comparison["candidate_run_id"] in (reports / "comparison.md").read_text()
    assert comparison["absolute_success_delta"] == summary["absolute_success_delta"]
    ablations = json.loads((reports / "ablations.json").read_text())
    assert ablations["reference_run_id"] == records["candidate"].run_id
    expected_changes = {
        "no_planner": "planner",
        "basic_tool_descriptions": "tool_descriptions",
        "no_tool_retries": "tool_retries",
    }
    for name, option in expected_changes.items():
        item = ablations["ablations"][name]
        assert "rows" not in item
        assert item["changed_config"] == [option]
        assert item["case_clusters"] == 10
        assert item["paired_results"] == 10 * repeats
        assert item["baseline_run_id"] == records["candidate"].run_id
        assert item["candidate_run_id"] == records[name].run_id
        assert item["dataset_digest"] == comparison["dataset_digest"]
        assert item["absolute_success_delta"] < 0
        assert item["candidate_metrics"] == run_metrics(records[name])
        assert name in (reports / "ablations.md").read_text()


@pytest.mark.parametrize("repeats", ["0", "11"])
def test_demo_rejects_invalid_repeat_count_before_writing(tmp_path, repeats):
    completed = run_script("demo.py", "--repeats", repeats, "--runtime-dir", str(tmp_path / "run"))
    assert completed.returncode == 2
    assert not (tmp_path / "run").exists()


def test_regression_ci_checks_all_ten_cases():
    completed = run_script("regression.py")
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["passed"] is True
    assert summary["cases"] == 10
    assert summary["metrics"]["task_success_rate"]["numerator"] == 10
    assert summary["metrics"]["tool_selection_accuracy"]["numerator"] == 10
    assert summary["failed_results"] == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("expected_answer", "WRONG_ANSWER"), ("expected_tool_sequence", ["search"])],
)
def test_regression_exits_one_for_answer_or_tool_regression(tmp_path, field, value):
    case = json.loads((ROOT / "datasets/regression.json").read_text())[0]
    case[field] = value
    path = tmp_path / "failing.json"
    path.write_text(json.dumps([case]))
    completed = run_script("regression.py", "--dataset", str(path))
    assert completed.returncode == 1, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["passed"] is False
    assert len(summary["failed_results"]) == 1
    failed = summary["failed_results"][0]
    assert failed["case_id"] == case["case_id"]
    assert failed["success"] is False or failed["tool_selection_correct"] is False
