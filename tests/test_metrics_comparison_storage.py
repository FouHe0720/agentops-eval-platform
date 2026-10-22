import json

import pytest
from test_v0 import case

from agentops_eval.adapters.deterministic import DeterministicBaselineAgent
from agentops_eval.domain.models import RunConfig
from agentops_eval.infra.storage import RunStore, export_record
from agentops_eval.services.comparison import compare_runs
from agentops_eval.services.metrics import compute_metrics
from agentops_eval.services.runner import run_evaluation


def test_empty_metrics():
    metrics = compute_metrics([])
    assert metrics["task_success_rate"] == {"value": None, "numerator": 0, "denominator": 0}
    assert metrics["latency_ms"]["p95"] is None
    assert metrics["token_usage"]["total"] is None
    assert metrics["cost"]["total"] is None


async def test_denominators_and_nulls():
    c = case()
    c.expected_answer = None
    c.expected_tool_sequence = None
    run = await run_evaluation([c], DeterministicBaselineAgent(), RunConfig())
    metrics = compute_metrics(run.results)
    assert metrics["task_success_rate"]["denominator"] == 0
    assert metrics["tool_selection_accuracy"]["denominator"] == 0
    assert metrics["tool_execution_success"]["denominator"] == 1


async def test_pairing_config_cluster_and_validation():
    c = case()
    a = await run_evaluation([c], DeterministicBaselineAgent(), RunConfig(repeats=3))
    b = await run_evaluation([c], DeterministicBaselineAgent(), RunConfig(repeats=3))
    comparison = compare_runs(a, b)
    assert comparison["absolute_success_delta"] == 0
    assert comparison["case_clusters"] == 1 and comparison["paired_results"] == 3
    assert comparison["bootstrap_ci_95"] is None
    assert comparison["warning"]
    b.config.seed = 99
    with pytest.raises(ValueError, match="config"):
        compare_runs(a, b)
    b.config.seed = a.config.seed
    b.results.append(b.results[0])
    with pytest.raises(ValueError, match="duplicate"):
        compare_runs(a, b)


async def test_storage_privacy_export_and_annotation(tmp_path):
    c = case()
    c.task = "private prompt Bearer secret-credential"
    c.fixtures["password"] = "private-password"
    record = await run_evaluation([c], DeterministicBaselineAgent(), RunConfig())
    record.results[0].trace.events[0].metadata["content"] = "private-file-content"
    store = RunStore(tmp_path / "test.db")
    store.save(record)
    loaded = store.get(record.run_id)
    assert loaded.cases[0].task == "[REDACTED]"
    assert loaded.results[0].answer is None
    assert "private-" not in loaded.model_dump_json()
    assert store.list()[0]["run_id"] == record.run_id
    store.annotate(record.run_id, c.case_id, 0, "unclassified")
    assert store.annotations(record.run_id)[0]["failure"] == "unclassified"
    assert store.get(record.run_id).results[0].failure is None  # annotation is a separate layer
    export_record(record, tmp_path / "run.json")
    assert "private-" not in (tmp_path / "run.json").read_text()
    assert len((tmp_path / "run.jsonl").read_text().splitlines()) == 4
    assert (
        json.loads((tmp_path / "run.jsonl").read_text().splitlines()[0])["run_id"] == record.run_id
    )


async def test_bootstrap_reproducible():
    cases = [case().model_copy(update={"case_id": f"case-{i}"}) for i in range(12)]
    a = await run_evaluation(cases, DeterministicBaselineAgent(), RunConfig())
    b = await run_evaluation(cases, DeterministicBaselineAgent(), RunConfig())
    b.results[0].success = False
    first = compare_runs(a, b)
    assert first == compare_runs(a, b)
    assert first["absolute_success_delta"] == pytest.approx(-1 / 12)
    assert first["bootstrap_ci_95"][0] <= first["absolute_success_delta"]
