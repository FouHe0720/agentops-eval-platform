"""Adversarial boundary tests for persisted contracts and comparison fairness."""

import json

import httpx
import pytest
from pydantic import ValidationError

from agentops_eval.adapters.http import WorkspaceHTTPAgentAdapter
from agentops_eval.api.app import create_app
from agentops_eval.cli import main
from agentops_eval.domain.models import (
    AgentResponse,
    Assertion,
    EvalCase,
    EvalResult,
    RunConfig,
    RunRecord,
)
from agentops_eval.infra.privacy import public_record
from agentops_eval.infra.storage import RunStore, export_record
from agentops_eval.services.comparison import compare_runs
from agentops_eval.services.datasets import digest
from agentops_eval.services.metrics import compute_metrics
from agentops_eval.services.runner import run_evaluation
from agentops_eval.services.tracing import TraceRecorder

SECRET = "sk-privatecredential123456789"
CONTENT = "private business content"


def make_record(*, run_id="run", sensitive=False, repeats=1, case_ids=("case",)):
    cases = [
        EvalCase(
            case_id=case_id,
            task=CONTENT,
            category="arithmetic",
            fixtures={"document": CONTENT, "api_key": SECRET},
            expected_answer="5",
            expected_tool_sequence=["calculator"],
            assertions=[Assertion(kind="contains", value="5")],
            tags=["synthetic"],
        )
        for case_id in case_ids
    ]
    results = []
    for repeat in range(repeats):
        for case in cases:
            trace = TraceRecorder(run_id)
            with trace.span("agent", case_id=case.case_id, repeat=repeat):
                with trace.span("tool_call", tool_name="calculator", content=CONTENT):
                    pass
            results.append(
                EvalResult(
                    run_id=run_id,
                    case_id=case.case_id,
                    category=case.category,
                    difficulty=case.difficulty,
                    repeat=repeat,
                    status="completed",
                    answer=CONTENT,
                    success=True,
                    tool_selection_correct=True,
                    latency_ms=1,
                    trace=trace.finish(),
                )
            )
    return RunRecord(
        run_id=run_id,
        agent="fixture",
        dataset_digest=digest(cases),
        cases=cases,
        config=RunConfig(repeats=repeats, sensitive_trace=sensitive),
        results=results,
    )


def test_valid_contract_round_trips_json_without_losing_trace_identity():
    record = make_record(repeats=2, case_ids=("a", "b"))
    restored = RunRecord.model_validate_json(record.model_dump_json())
    assert restored == record
    assert {(r.case_id, r.repeat) for r in restored.results} == {
        (case_id, repeat) for case_id in ("a", "b") for repeat in range(2)
    }
    assert all(
        event.run_id == restored.run_id
        for result in restored.results
        for event in result.trace.events
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_result",
        "duplicate_result",
        "foreign_result",
        "wrong_category",
        "foreign_trace",
        "duplicate_span",
        "forward_parent",
        "self_parent",
        "negative_duration",
        "nonfinite_cost",
    ],
)
def test_untrusted_run_json_rejects_invalid_coverage_and_trace(corruption):
    payload = make_record().model_dump(mode="json")
    result = payload["results"][0]
    events = result["trace"]["events"]
    if corruption == "missing_result":
        payload["results"] = []
    elif corruption == "duplicate_result":
        payload["results"].append(result)
    elif corruption == "foreign_result":
        result["run_id"] = "foreign"
    elif corruption == "wrong_category":
        result["category"] = "retrieval"
    elif corruption == "foreign_trace":
        for event in events:
            event["run_id"] = "foreign"
    elif corruption == "duplicate_span":
        events[1]["span_id"] = events[0]["span_id"]
    elif corruption == "forward_parent":
        events[0]["parent_span_id"] = events[1]["span_id"]
    elif corruption == "self_parent":
        events[1]["parent_span_id"] = events[1]["span_id"]
    elif corruption == "negative_duration":
        events[1]["duration_ms"] = -1
    elif corruption == "nonfinite_cost":
        events[1]["cost"] = float("inf")
    with pytest.raises(ValidationError):
        RunRecord.model_validate_json(json.dumps(payload))


def test_cli_validation_errors_do_not_echo_secret_input_or_local_paths(
    tmp_path, monkeypatch, capsys
):
    dataset = tmp_path / "private-dataset.json"
    dataset.write_text(json.dumps([{"case_id": SECRET, "task": CONTENT, "category": SECRET}]))
    monkeypatch.setattr("sys.argv", ["eval", "run", "--dataset", str(dataset)])
    with pytest.raises(SystemExit) as caught:
        main()
    output = capsys.readouterr()
    assert caught.value.code == 2
    assert "evaluation error" in output.err
    assert SECRET not in output.out + output.err
    assert CONTENT not in output.out + output.err
    assert str(tmp_path) not in output.out + output.err


async def test_invalid_trace_values_never_appear_in_output_or_serialized_failure(capsys, caplog):
    class MalformedAdapter:
        name = "malformed"
        capabilities = {"arithmetic"}

        async def execute(self, case, trace, config, seed):
            trace.events[0].cost = [SECRET]
            return AgentResponse(answer="5")

    run = await run_evaluation(make_record().cases, MalformedAdapter(), RunConfig())
    assert run.results[0].status == "error"
    assert run.results[0].failure == "unclassified"
    assert SECRET not in run.results[0].model_dump_json()
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err + caplog.text


@pytest.mark.parametrize("sensitive", [False, True])
def test_store_and_both_export_formats_enforce_same_privacy_boundary(tmp_path, sensitive):
    record = make_record(sensitive=sensitive)
    record.cases[0].expected_tool_sequence = ["calculator", SECRET]
    record.cases[0].tags = ["synthetic", SECRET]
    record.cases[0].task += f" Bearer {SECRET} /home/private-user/private-file.txt"
    record.results[0].answer += f" Bearer {SECRET}"
    record.results[0].trace.events[0].metadata["note"] = f"Bearer {SECRET}"
    record.results[0].trace.events[1].tool_name = SECRET
    original = record.model_dump_json()
    store = RunStore(tmp_path / "records.db")
    saved = store.save(record)
    loaded = store.get(record.run_id)
    export_record(record, tmp_path / "export.json")
    assert loaded == saved
    with store.connect() as connection:
        persisted = connection.execute("SELECT payload FROM runs").fetchone()[0]
    texts = [
        persisted,
        saved.model_dump_json(),
        (tmp_path / "export.json").read_text(),
        (tmp_path / "export.jsonl").read_text(),
    ]
    assert all(SECRET not in text for text in texts)
    assert all("/home/private-user" not in text for text in texts)
    assert (CONTENT in persisted) is sensitive
    assert record.model_dump_json() == original
    assert saved.results[0].success is True
    assert saved.results[0].tool_selection_correct is True


def test_default_metadata_allowlist_does_not_accept_content_hidden_in_values():
    record = make_record()
    metadata = record.results[0].trace.events[0].metadata
    metadata.update(
        case_id={"note": CONTENT},
        repeat=CONTENT,
        status_code={"note": CONTENT},
        retry_index=[CONTENT],
        error_code=CONTENT,
    )
    safe = public_record(record)
    exported = safe.model_dump_json()
    assert CONTENT not in exported
    retained = safe.results[0].trace.events[0].metadata
    assert retained["case_id"] == "case"
    assert retained["repeat"] == 0


@pytest.mark.parametrize(
    ("fragment", "credentials"),
    [
        ("Authorization: Basic cHJpdmF0ZTpjcmVkZW50aWFs", ["cHJpdmF0ZTpjcmVkZW50aWFs"]),
        ('{"api_key":"privatecredential"}', ["privatecredential"]),
        ("Cookie: first=privateone; session=privatetwo", ["privateone", "privatetwo"]),
        ('password = "private credential with spaces"', ["private credential", "with spaces"]),
    ],
)
def test_content_opt_in_still_removes_complete_common_credential_forms(
    tmp_path, fragment, credentials
):
    record = make_record(sensitive=True)
    record.cases[0].task = f"public explanation: {fragment}"
    record.results[0].answer = fragment
    record.results[0].trace.events[0].metadata["note"] = fragment
    store = RunStore(tmp_path / "records.db")
    store.save(record)
    export_record(record, tmp_path / "export.json")
    with store.connect() as connection:
        persisted = connection.execute("SELECT payload FROM runs").fetchone()[0]
    texts = [
        persisted,
        store.get(record.run_id).model_dump_json(),
        (tmp_path / "export.json").read_text(),
        (tmp_path / "export.jsonl").read_text(),
    ]
    assert "public explanation" in persisted
    assert all(credential not in text for credential in credentials for text in texts)


def test_secret_like_case_identifiers_fail_closed_before_writing(tmp_path):
    record = make_record(case_ids=(SECRET,))
    store = RunStore(tmp_path / "records.db")
    with pytest.raises(ValidationError):
        store.save(record)
    assert store.list() == []
    with pytest.raises(ValidationError):
        export_record(record, tmp_path / "export.json")
    assert not (tmp_path / "export.json").exists()


async def test_api_validation_and_corrupt_stored_contract_errors_do_not_echo_inputs(tmp_path):
    app = create_app(tmp_path / "records.db")
    store = app.state.store
    record = make_record()
    store.save(record)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        invalid_request = await client.post(
            f"/runs/{record.run_id}/annotations",
            json={"case_id": "case", "repeat": 0, "failure": SECRET},
        )
        assert invalid_request.status_code == 422
        assert invalid_request.json()["error"]["code"] == "invalid_request"
        payload = record.model_dump(mode="json")
        payload["results"][0]["trace"]["events"][0]["cost"] = SECRET
        with store.connect() as connection:
            connection.execute(
                "UPDATE runs SET payload=? WHERE run_id=?",
                (json.dumps(payload), record.run_id),
            )
        invalid_record = await client.get(f"/runs/{record.run_id}")
        assert invalid_record.status_code == 500
        assert invalid_record.json()["error"]["code"] == "internal_error"
    for response in (invalid_request, invalid_record):
        assert SECRET not in response.text
        assert CONTENT not in response.text
        assert str(tmp_path) not in response.text
        assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("field", ["seed", "concurrency", "max_retries", "sensitive_trace"])
@pytest.mark.parametrize("allow_ablation", [False, True])
def test_ablation_never_relaxes_unrelated_execution_configuration(field, allow_ablation):
    baseline = make_record(run_id="baseline")
    candidate = make_record(run_id="candidate")
    current = getattr(candidate.config, field)
    setattr(candidate.config, field, not current if isinstance(current, bool) else current + 1)
    with pytest.raises(ValueError):
        compare_runs(baseline, candidate, allow_ablation=allow_ablation)


def test_ablation_is_explicit_and_records_exactly_changed_factors():
    baseline = make_record(run_id="baseline")
    candidate = make_record(run_id="candidate")
    candidate.config.planner = False
    candidate.config.tool_descriptions = "basic"
    candidate.config.tool_retries = 0
    with pytest.raises(ValueError):
        compare_runs(baseline, candidate)
    result = compare_runs(baseline, candidate, allow_ablation=True)
    assert result["changed_config"] == ["planner", "tool_descriptions", "tool_retries"]
    assert result["case_clusters"] == result["paired_results"] == 1


@pytest.mark.parametrize("field", ["task", "expected_answer", "fixtures", "assertions"])
def test_identical_digest_cannot_hide_different_case_definitions(field):
    baseline = make_record(run_id="baseline")
    candidate = make_record(run_id="candidate")
    replacements = {
        "task": "a different task",
        "expected_answer": "999",
        "fixtures": {"operation": "multiply"},
        "assertions": [],
    }
    setattr(candidate.cases[0], field, replacements[field])
    assert baseline.dataset_digest == candidate.dataset_digest
    with pytest.raises(ValueError):
        compare_runs(baseline, candidate)


@pytest.mark.parametrize("missing", ["result", "case"])
def test_comparison_rejects_missing_case_pairs_even_on_mutated_models(missing):
    baseline = make_record(run_id="baseline", case_ids=("a", "b"))
    candidate = make_record(run_id="candidate", case_ids=("a", "b"))
    candidate.results.pop()
    if missing == "case":
        candidate.cases.pop()
    with pytest.raises(ValueError):
        compare_runs(baseline, candidate)


def test_comparison_works_for_records_loaded_through_privacy_boundary():
    baseline = public_record(make_record(run_id="baseline"))
    candidate = public_record(make_record(run_id="candidate"))
    comparison = compare_runs(baseline, candidate)
    assert comparison["absolute_success_delta"] == 0
    assert comparison["paired_results"] == 1


def test_case_clusters_have_equal_weight_when_skips_remove_some_repeat_pairs():
    baseline = make_record(run_id="baseline", repeats=2, case_ids=("a", "b"))
    candidate = make_record(run_id="candidate", repeats=2, case_ids=("a", "b"))
    for result in baseline.results + candidate.results:
        result.success = False
    candidate.results[0].success = True
    candidate.results[2].status = "skipped"
    candidate.results[2].success = None
    comparison = compare_runs(baseline, candidate)
    assert comparison["paired_results"] == 3
    assert comparison["case_clusters"] == 2
    assert comparison["absolute_success_delta"] == 0.5
    assert comparison["bootstrap_ci_95"] is None


def test_metrics_keep_execution_failures_and_exclude_unreferenced_and_skipped_results():
    record = make_record(case_ids=("ok", "error", "timeout", "unreferenced", "skipped"))
    ok, error, timeout, unreferenced, skipped = record.results
    error.status, error.success, error.tool_selection_correct = "error", False, False
    timeout.status, timeout.success, timeout.tool_selection_correct = "timeout", False, False
    unreferenced.status = "error"
    unreferenced.success, unreferenced.tool_selection_correct = None, False
    skipped.status, skipped.success, skipped.tool_selection_correct = "skipped", None, None
    ok.trace.events[1].status = "ok"
    error.trace.events[1].status = "error"
    timeout.trace.events[1].status = "timeout"
    unreferenced.trace.events = []
    metrics = compute_metrics(record.results)
    assert metrics["task_success_rate"] == {"value": 1 / 3, "numerator": 1, "denominator": 3}
    assert metrics["tool_selection_accuracy"] == {"value": 0.25, "numerator": 1, "denominator": 4}
    assert metrics["tool_execution_success"] == {"value": 1 / 3, "numerator": 1, "denominator": 3}
    assert metrics["timeout_rate"] == {"value": 0.25, "numerator": 1, "denominator": 4}
    assert metrics["skipped"] == 1
    assert metrics["latency_ms"]["denominator"] == 4
    assert metrics["token_usage"]["total"] is None
    assert metrics["cost"]["total"] is None


async def test_remote_content_urls_are_inert_and_cannot_select_another_destination():
    requests = []
    internal_url = "http://169.254.169.254/latest/meta-data/"
    content = f'<script>fetch("{internal_url}")</script>'

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"answer": content})

    record = make_record()
    record.cases[0].fixtures = {"url": internal_url}
    adapter = WorkspaceHTTPAgentAdapter(
        "http://configured-agent.test/execute", transport=httpx.MockTransport(handle)
    )
    run = await run_evaluation(record.cases, adapter, RunConfig())
    assert [str(request.url) for request in requests] == ["http://configured-agent.test/execute"]
    assert run.results[0].answer == content
    assert public_record(run).results[0].answer is None
