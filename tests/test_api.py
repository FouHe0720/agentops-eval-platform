import base64
import hashlib
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentops_eval.api.app import create_app
from agentops_eval.domain.models import (
    AgentRunTrace,
    EvalCase,
    EvalResult,
    RunConfig,
    RunRecord,
    TraceEvent,
)
from agentops_eval.infra.storage import RunStore


def record(agent="baseline_v1", *, digest="synthetic-dataset", config=None, offset=0):
    run_id = uuid4().hex
    definitions = [
        ("arithmetic", "easy", "completed", True, None),
        ("retrieval", "hard", "completed", False, "tool_selection_error"),
        ("recovery", "medium", "timeout", False, "timeout"),
        ("long_input", "hard", "skipped", None, None),
    ]
    cases, results = [], []
    for index, (category, difficulty, status, success, failure) in enumerate(definitions):
        case_id = f"case-{index}"
        cases.append(
            EvalCase(
                case_id=case_id,
                task="private task with api_key=private-credential",
                category=category,
                difficulty=difficulty,
                expected_answer="private grading answer",
                fixtures={"private-file": "private-fixture"},
            )
        )
        root_id = f"root-{index}"
        events = []
        if status != "skipped":
            events.append(
                TraceEvent(
                    run_id=run_id,
                    span_id=root_id,
                    event_type="agent",
                    status="timeout" if status == "timeout" else "ok",
                    duration_ms=10 + index,
                    metadata={"prompt": "private-prompt", "api_key": "private-credential"},
                )
            )
            events.append(
                TraceEvent(
                    run_id=run_id,
                    span_id=f"tool-{index}",
                    parent_span_id=root_id,
                    event_type="tool_call",
                    tool_name="calculator" if index == 0 else "search",
                    status="ok" if success else "error",
                    duration_ms=2 + index,
                )
            )
        results.append(
            EvalResult(
                run_id=run_id,
                case_id=case_id,
                category=category,
                difficulty=difficulty,
                status=status,
                answer="private output with token=private-credential",
                success=success,
                tool_selection_correct=True
                if success
                else False
                if status == "completed"
                else None,
                failure=failure,
                latency_ms=10 + index,
                trace=AgentRunTrace(events=events),
            )
        )
    return RunRecord(
        run_id=run_id,
        agent=agent,
        dataset_digest=digest,
        config=config or RunConfig(),
        created_at=datetime(2026, 10, 22, tzinfo=UTC) + timedelta(seconds=offset),
        cases=cases,
        results=results,
    )


@pytest.fixture
def api(tmp_path):
    db = tmp_path / "runs.db"
    baseline, candidate = record(), record("candidate_v2", offset=1)
    store = RunStore(db)
    store.save(baseline)
    store.save(candidate)
    with TestClient(create_app(db)) as client:
        yield client, store, baseline, candidate


def assert_error(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    assert "private-" not in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_health_and_self_contained_dashboard_have_security_headers(api):
    client, *_ = api
    assert client.get("/health").json() == {"status": "ok", "schema_version": 1}
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "评测工作台" in response.text
    assert "Trace Timeline" in response.text
    assert "https://" not in response.text
    assert "innerHTML" not in response.text
    assert "textContent" in response.text
    assert "script-src 'sha256-" in response.headers["content-security-policy"]
    script = re.search(r"<script>(.*?)</script>", response.text, re.DOTALL).group(1)
    expected_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"'sha256-{expected_hash}'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_list_run_and_latest_metrics_use_persisted_private_record(api):
    client, _, baseline, candidate = api
    listed = client.get("/runs").json()["runs"]
    assert [item["run_id"] for item in listed] == [candidate.run_id, baseline.run_id]
    assert listed[0]["dataset_digest"] == candidate.dataset_digest
    assert listed[0]["config"] == candidate.config.model_dump()
    assert len(client.get("/runs?limit=1").json()["runs"]) == 1
    response = client.get(f"/runs/{baseline.run_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["result_count"] == {"total": 4, "filtered": 4}
    assert data["metrics"]["task_success_rate"] == {
        "value": 1 / 3,
        "numerator": 1,
        "denominator": 3,
    }
    assert data["metrics"]["skipped"] == 1
    assert data["metrics"]["token_usage"]["total"] is None
    assert data["metrics"]["cost"]["total"] is None
    assert "private " not in response.text and "private-" not in response.text
    assert all(result["answer"] is None for result in data["results"])
    trace = data["results"][0]["trace"]["events"]
    assert trace[1]["parent_span_id"] == trace[0]["span_id"]
    assert trace[1]["duration_ms"] == 2
    assert trace[0]["metadata"] == {}
    assert client.get("/metrics").json()["run_id"] == candidate.run_id
    assert client.get(f"/metrics?run_id={baseline.run_id}").json()["metrics"] == data["metrics"]


@pytest.mark.parametrize(
    "query,case_ids,denominator",
    [
        ("category=retrieval", ["case-1"], 1),
        ("difficulty=hard", ["case-1", "case-3"], 1),
        ("failure=timeout", ["case-2"], 1),
        ("failures_only=true", ["case-1", "case-2"], 2),
        ("difficulty=hard&category=recovery", [], 0),
        (
            "category=retrieval&difficulty=hard&failure=tool_selection_error&failures_only=true",
            ["case-1"],
            1,
        ),
    ],
)
def test_filters_intersect_and_recompute_effective_denominators(api, query, case_ids, denominator):
    client, _, baseline, _ = api
    response = client.get(f"/runs/{baseline.run_id}?{query}")
    assert response.status_code == 200
    body = response.json()
    assert [result["case_id"] for result in body["results"]] == case_ids
    assert body["result_count"] == {"total": 4, "filtered": len(case_ids)}
    assert body["metrics"]["task_success_rate"]["denominator"] == denominator
    if denominator == 0:
        assert body["metrics"]["task_success_rate"]["value"] is None
        assert body["metrics"]["latency_ms"]["p50"] is None


def test_paired_comparison_and_incompatible_runs(api):
    client, store, baseline, candidate = api
    response = client.get(
        "/compare", params={"baseline": baseline.run_id, "candidate": candidate.run_id}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["absolute_success_delta"] == 0
    assert data["paired_results"] == 3
    assert data["case_clusters"] == 3
    assert data["bootstrap_ci_95"] is None
    assert len(data["rows"]) == 4
    incompatible = record("mismatch", digest="different-dataset")
    store.save(incompatible)
    assert_error(
        client.get(
            "/compare", params={"baseline": baseline.run_id, "candidate": incompatible.run_id}
        ),
        400,
        "comparison_invalid",
    )


def test_ablation_must_be_explicit_and_cannot_allow_seed_difference(api):
    client, store, baseline, _ = api
    ablated = record("ablation", config=RunConfig(planner=False))
    store.save(ablated)
    params = {"baseline": baseline.run_id, "candidate": ablated.run_id}
    assert_error(client.get("/compare", params=params), 400, "comparison_invalid")
    response = client.get("/compare", params={**params, "allow_ablation": "true"})
    assert response.status_code == 200
    assert response.json()["changed_config"] == ["planner"]
    other_seed = record("other-seed", config=RunConfig(seed=99))
    store.save(other_seed)
    params["candidate"] = other_seed.run_id
    assert_error(
        client.get("/compare", params={**params, "allow_ablation": "true"}),
        400,
        "comparison_invalid",
    )


def test_annotations_persist_and_do_not_change_original_metrics(api):
    client, store, baseline, _ = api
    path = f"/runs/{baseline.run_id}/annotations"
    before = client.get(f"/metrics?run_id={baseline.run_id}").json()
    assert client.get(path).json() == {"run_id": baseline.run_id, "annotations": []}
    payload = {"case_id": "case-1", "repeat": 0, "failure": "reasoning_error"}
    response = client.post(path, json=payload)
    assert response.status_code == 200
    assert response.json() == {"run_id": baseline.run_id, **payload}
    assert store.annotations(baseline.run_id) == [payload]
    payload["failure"] = "argument_error"
    assert (
        client.post(path, json=payload, headers={"Origin": "http://testserver"}).status_code == 200
    )
    assert client.get(path).json()["annotations"] == [payload]
    assert client.get(f"/metrics?run_id={baseline.run_id}").json() == before
    assert (
        client.get(f"/runs/{baseline.run_id}").json()["results"][1]["failure"]
        == "tool_selection_error"
    )


@pytest.mark.parametrize(
    "origin", ["http://evil.test", "https://testserver", "http://testserver.evil", "null"]
)
def test_annotations_reject_cross_origin_writes(api, origin):
    client, store, baseline, _ = api
    response = client.post(
        f"/runs/{baseline.run_id}/annotations",
        json={"case_id": "case-1", "repeat": 0, "failure": "reasoning_error"},
        headers={"Origin": origin},
    )
    assert_error(response, 403, "origin_forbidden")
    assert store.annotations(baseline.run_id) == []


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", ""])
def test_annotations_require_json_content_type(api, content_type):
    client, _, baseline, _ = api
    response = client.post(
        f"/runs/{baseline.run_id}/annotations",
        content='{"case_id":"case-1","repeat":0,"failure":"reasoning_error"}',
        headers={"Content-Type": content_type},
    )
    assert_error(response, 415, "unsupported_media_type")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"case_id": "case-1", "repeat": 0, "failure": "unknown-private-value"},
        {"case_id": "case-1", "repeat": "0", "failure": "reasoning_error"},
        {"case_id": "case-1", "repeat": True, "failure": "reasoning_error"},
        {"case_id": "case-1", "repeat": -1, "failure": "reasoning_error"},
        {"case_id": "../private-path", "repeat": 0, "failure": "reasoning_error"},
        {"case_id": "case-1", "repeat": 0, "failure": "reasoning_error", "extra": "private-value"},
        ["private-value"],
    ],
)
def test_annotation_contract_is_strict_and_validation_does_not_echo_input(api, payload):
    client, store, baseline, _ = api
    assert_error(
        client.post(f"/runs/{baseline.run_id}/annotations", json=payload), 422, "invalid_request"
    )
    assert store.annotations(baseline.run_id) == []


def test_malformed_json_and_nonexistent_case_repeat(api):
    client, _, baseline, _ = api
    path = f"/runs/{baseline.run_id}/annotations"
    assert_error(
        client.post(
            path, content="private-invalid-json", headers={"Content-Type": "application/json"}
        ),
        422,
        "invalid_request",
    )
    for case_id, repeat in [("missing", 0), ("case-1", 1)]:
        assert_error(
            client.post(path, json={"case_id": case_id, "repeat": repeat, "failure": "timeout"}),
            404,
            "not_found",
        )


@pytest.mark.parametrize(
    "path", ["/runs/missing", "/metrics?run_id=missing", "/runs/missing/annotations", "/unknown"]
)
def test_missing_resources_are_safe_404(api, path):
    client, *_ = api
    assert_error(client.get(path), 404, "not_found")


@pytest.mark.parametrize(
    "path",
    [
        "/runs?limit=0",
        "/runs?limit=1001",
        "/compare",
        "/compare?baseline=x&candidate=y&allow_ablation=invalid",
    ],
)
def test_invalid_queries_return_uniform_422(api, path):
    client, *_ = api
    assert_error(client.get(path), 422, "invalid_request")


@pytest.mark.parametrize(
    "query", ["difficulty=expert", "category=network", "failure=unknown", "failures_only=maybe"]
)
def test_invalid_result_filters_are_rejected(api, query):
    client, _, baseline, _ = api
    assert_error(client.get(f"/runs/{baseline.run_id}?{query}"), 422, "invalid_request")


def test_empty_database_has_useful_empty_list_and_metrics_404(tmp_path):
    with TestClient(create_app(tmp_path / "empty.db")) as client:
        assert client.get("/runs").json() == {"runs": []}
        assert_error(client.get("/metrics"), 404, "not_found")
        assert client.get("/").status_code == 200


def test_internal_errors_never_expose_exception_text(api, monkeypatch):
    client, *_ = api

    def broken(limit):
        raise RuntimeError("private-password /home/private-user/secret.db")

    monkeypatch.setattr(client.app.state.store, "list", broken)
    assert_error(client.get("/runs"), 500, "internal_error")


def test_no_arbitrary_execution_or_file_reading_endpoints(api):
    client, *_ = api
    assert_error(client.post("/runs", json={"task": "private-task"}), 405, "method_not_allowed")
    for path in ("/execute", "/files?path=/etc/passwd", "/runs/missing/export"):
        assert_error(client.get(path), 404, "not_found")
