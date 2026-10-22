import json

import httpx
import pytest

from agentops_eval.adapters.base import AdapterError
from agentops_eval.adapters.deterministic import DeterministicBaselineAgent
from agentops_eval.adapters.http import ResearchAgentAdapter, WorkspaceHTTPAgentAdapter
from agentops_eval.adapters.openai_compatible import OpenAICompatibleAdapter
from agentops_eval.domain.models import Assertion, EvalCase, RunConfig
from agentops_eval.services.runner import run_evaluation
from agentops_eval.services.tracing import TraceRecorder


@pytest.fixture(autouse=True)
def clear_adapter_environment(monkeypatch):
    for name in (
        "WORKSPACE_AGENT_ENDPOINT",
        "WORKSPACE_AGENT_API_KEY",
        "RESEARCH_AGENT_ENDPOINT",
        "RESEARCH_AGENT_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def sample_case(category="arithmetic"):
    return EvalCase(
        case_id="sample",
        task="Add the fixture values",
        category=category,
        fixtures={"operation": "add", "a": 2, "b": 3},
        expected_answer="private-expected-answer",
        expected_tool_sequence=["private-expected-tool"],
        assertions=[Assertion(kind="contains", value="private-assertion")],
        tags=["private-tag"],
        timeout=0.25,
    )


def assert_safe_request(request):
    serialized = request.content.decode()
    assert "private-" not in serialized
    assert "expected_answer" not in serialized
    assert "expected_tool_sequence" not in serialized
    assert "assertions" not in serialized


async def test_workspace_http_success_remaps_complete_tree_and_limits_request():
    def handle(request):
        assert request.url == "http://agent.test/execute"
        assert request.headers["Authorization"] == "Bearer secret-key"
        payload = json.loads(request.content)
        assert set(payload) == {"task", "category", "fixtures", "seed", "config"}
        assert payload["seed"] == 42
        assert payload["config"] == {
            "planner": True,
            "tool_descriptions": "improved",
            "tool_retries": 1,
        }
        assert request.extensions["timeout"] == dict.fromkeys(
            ["connect", "read", "write", "pool"], 0.25
        )
        assert_safe_request(request)
        return httpx.Response(
            200,
            json={
                "answer": "5",
                "trace": [
                    {"span_id": "remote-root", "run_id": "remote-run", "event_type": "agent"},
                    {
                        "span_id": "remote-tool",
                        "parent_span_id": "remote-root",
                        "run_id": "remote-run",
                        "event_type": "tool_call",
                        "tool_name": "calculator",
                        "duration_ms": 1.2,
                    },
                    {
                        "span_id": "remote-result",
                        "parent_span_id": "remote-tool",
                        "run_id": "remote-run",
                        "event_type": "tool_result",
                        "tool_name": "calculator",
                    },
                ],
            },
        )

    adapter = WorkspaceHTTPAgentAdapter(
        "http://agent.test/execute", api_key="secret-key", transport=httpx.MockTransport(handle)
    )
    trace = TraceRecorder("local-run")
    with trace.span("agent"):
        response = await adapter.execute(sample_case(), trace, RunConfig(), 42)
    assert response.answer == "5"
    events = trace.finish().events
    assert [event.event_type for event in events] == [
        "agent",
        "agent",
        "agent",
        "tool_call",
        "tool_result",
    ]
    assert {event.run_id for event in events} == {"local-run"}
    assert all(not event.span_id.startswith("remote-") for event in events)
    assert [event.parent_span_id for event in events[1:]] == [
        event.span_id for event in events[:-1]
    ]
    assert "secret-key" not in trace.finish().model_dump_json()


async def test_research_environment_configuration_and_capabilities(monkeypatch):
    monkeypatch.setenv("RESEARCH_AGENT_ENDPOINT", "http://research.test/run")
    monkeypatch.setenv("RESEARCH_AGENT_API_KEY", "research-key")
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer research-key"
        return httpx.Response(200, json={"answer": "retrieved"})

    adapter = ResearchAgentAdapter(transport=httpx.MockTransport(handle))
    assert adapter.capabilities == {"retrieval"}
    run = await run_evaluation(
        [sample_case(), sample_case("retrieval").model_copy(update={"case_id": "retrieval"})],
        adapter,
        RunConfig(),
    )
    assert [result.status for result in run.results] == ["skipped", "completed"]
    assert len(requests) == 1


@pytest.mark.parametrize(
    "adapter_class", [WorkspaceHTTPAgentAdapter, ResearchAgentAdapter, OpenAICompatibleAdapter]
)
async def test_missing_configuration_is_local_error_and_offline_still_runs(adapter_class):
    adapter = adapter_class()
    category = "retrieval" if adapter_class is ResearchAgentAdapter else "arithmetic"
    with pytest.raises(AdapterError) as caught:
        await adapter.execute(sample_case(category), TraceRecorder("run"), RunConfig(), 42)
    assert caught.value.code == "not_configured"
    run = await run_evaluation([sample_case()], DeterministicBaselineAgent(), RunConfig())
    assert run.results[0].answer == "5"


@pytest.mark.parametrize(
    "trace",
    [
        [{"span_id": "a", "event_type": "agent", "parent_span_id": "missing"}],
        [{"span_id": "a", "event_type": "agent", "parent_span_id": "a"}],
        [{"span_id": "a", "event_type": "agent"}] * 2,
        [{"span_id": "a", "event_type": "agent"}, {"span_id": "b", "event_type": "agent"}],
        [
            {"span_id": "a", "event_type": "agent", "run_id": "one"},
            {"span_id": "b", "parent_span_id": "a", "event_type": "final", "run_id": "two"},
        ],
        [{"span_id": "a", "event_type": "agent", "duration_ms": -1}],
        [{"span_id": "a", "event_type": "agent", "token_usage": True}],
        [{"span_id": "a", "event_type": "tool_call"}],
        [{"span_id": "a", "event_type": "agent", "metadata": {"secret": "sensitive-body"}}],
        [{"span_id": "a", "event_type": "agent", "duration_ms": float("inf")}],
    ],
)
async def test_malformed_remote_trace_rejected_atomically(trace):
    # httpx deliberately permits writing this invalid JSON constant for protocol coverage.
    body = json.dumps({"answer": "5", "trace": trace})
    adapter = WorkspaceHTTPAgentAdapter(
        "http://agent.test/run",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
    )
    recorder = TraceRecorder("local")
    with pytest.raises(AdapterError) as caught:
        await adapter.execute(sample_case(), recorder, RunConfig(), 42)
    assert caught.value.code == "invalid_response"
    assert len(recorder.events) == 1
    assert recorder.events[0].status == "error"
    assert "sensitive-body" not in recorder.finish().model_dump_json()


@pytest.mark.parametrize("payload", [{}, {"answer": 5}, {"answer": "5", "secret": "raw-body"}])
async def test_invalid_http_answer_contract(payload):
    adapter = WorkspaceHTTPAgentAdapter(
        "http://agent.test/run",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    with pytest.raises(AdapterError, match="^invalid_response$"):
        await adapter.execute(sample_case(), TraceRecorder("local"), RunConfig(), 42)


@pytest.mark.parametrize("status,retryable", [(302, False), (401, False), (429, True), (503, True)])
async def test_http_errors_are_sanitized_and_redirects_are_never_followed(status, retryable):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status, text="secret-response-body", headers={"location": "http://other.test/steal"}
        )

    adapter = WorkspaceHTTPAgentAdapter(
        "http://agent.test/run", api_key="secret-key", transport=httpx.MockTransport(handle)
    )
    trace = TraceRecorder("local")
    with pytest.raises(AdapterError) as caught:
        await adapter.execute(sample_case(), trace, RunConfig(), 42)
    assert caught.value.code == "http_error"
    assert caught.value.retryable is retryable
    assert len(calls) == 1
    assert "secret-" not in str(caught.value) + trace.finish().model_dump_json()


@pytest.mark.parametrize("adapter_kind", ["http", "openai"])
async def test_transport_timeout_has_timeout_trace_and_no_raw_exception(adapter_kind):
    def handle(request):
        raise httpx.ReadTimeout("secret-token-and-raw-body", request=request)

    transport = httpx.MockTransport(handle)
    adapter = (
        WorkspaceHTTPAgentAdapter("http://agent.test/run", transport=transport)
        if adapter_kind == "http"
        else OpenAICompatibleAdapter("http://model.test/v1", "model", transport=transport)
    )
    trace = TraceRecorder("local")
    with pytest.raises(TimeoutError) as caught:
        await adapter.execute(sample_case(), trace, RunConfig(), 42)
    assert str(caught.value) == ""
    assert trace.finish().events[0].status == "timeout"
    assert "secret-" not in trace.finish().model_dump_json()
    run = await run_evaluation([sample_case()], adapter, RunConfig())
    assert run.results[0].status == "timeout"
    assert run.results[0].failure == "timeout"


async def test_transport_exception_and_oversized_body_are_safe():
    def handle(request):
        raise httpx.ConnectError("secret-connection-details", request=request)

    adapter = WorkspaceHTTPAgentAdapter(
        "http://agent.test/run", transport=httpx.MockTransport(handle)
    )
    with pytest.raises(AdapterError) as caught:
        await adapter.execute(sample_case(), TraceRecorder("run"), RunConfig(), 42)
    assert str(caught.value) == "transport_error"
    assert caught.value.retryable is True
    adapter = WorkspaceHTTPAgentAdapter(
        "http://agent.test/run",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * 1_048_577)
        ),
    )
    with pytest.raises(AdapterError, match="^invalid_response$"):
        await adapter.execute(sample_case(), TraceRecorder("run"), RunConfig(), 42)


@pytest.mark.parametrize(
    "usage,expected",
    [
        (None, None),
        ({}, None),
        ({"total_tokens": 17}, 17),
        ({"prompt_tokens": 10, "completion_tokens": 3}, 13),
        ({"total_tokens": 0}, 0),
    ],
)
async def test_openai_real_call_usage_and_unknown_cost(monkeypatch, usage, expected):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://model.test/v1/")
    monkeypatch.setenv("OPENAI_MODEL", "configured-model")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-secret")

    def handle(request):
        assert str(request.url) == "http://model.test/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer provider-secret"
        payload = json.loads(request.content)
        assert payload["model"] == "configured-model"
        prompt = json.loads(payload["messages"][1]["content"])
        assert set(prompt) == {"task", "category", "fixtures"}
        assert_safe_request(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "5"}}], "usage": usage}
        )

    run = await run_evaluation(
        [sample_case()], OpenAICompatibleAdapter(transport=httpx.MockTransport(handle)), RunConfig()
    )
    result = run.results[0]
    assert result.status == "completed"
    assert result.answer == "5"
    root, call, final = result.trace.events
    assert (root.event_type, call.event_type, final.event_type) == ("agent", "model_call", "final")
    assert call.parent_span_id == root.span_id
    assert call.token_usage == expected
    assert call.cost is None
    assert "provider-secret" not in result.model_dump_json()


async def test_openai_self_hosted_without_key():
    def handle(request):
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"choices": [{"message": {"content": "5"}}]})

    adapter = OpenAICompatibleAdapter(
        "http://localhost:8080/v1", "local-model", transport=httpx.MockTransport(handle)
    )
    response = await adapter.execute(sample_case(), TraceRecorder("run"), RunConfig(), 42)
    assert response.answer == "5"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [None]},
        {"choices": [{"message": []}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": "5", "tool_calls": [{"id": "unsupported"}]}}]},
        {"choices": [{"message": {"content": "5"}}], "usage": {"total_tokens": -1}},
        {"choices": [{"message": {"content": "5"}}], "usage": {"total_tokens": True}},
        {
            "choices": [{"message": {"content": "5"}}],
            "usage": {"total_tokens": 10, "prompt_tokens": 10, "completion_tokens": 5},
        },
    ],
)
async def test_openai_rejects_malformed_response_with_error_model_span(payload):
    adapter = OpenAICompatibleAdapter(
        "http://model.test/v1",
        "model",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    run = await run_evaluation([sample_case()], adapter, RunConfig())
    result = run.results[0]
    assert result.status == "error"
    assert result.failure == "argument_error"
    assert result.trace.events[1].event_type == "model_call"
    assert result.trace.events[1].status == "error"
    assert result.trace.events[1].token_usage is None


@pytest.mark.parametrize("endpoint", ["file:///secret", "http://user:secret@agent.test", "junk"])
async def test_invalid_endpoint_is_configuration_error(endpoint):
    adapter = WorkspaceHTTPAgentAdapter(endpoint)
    with pytest.raises(AdapterError, match="^invalid_configuration$"):
        await adapter.execute(sample_case(), TraceRecorder("run"), RunConfig(), 42)


@pytest.mark.parametrize("body", [b"invalid-secret-json", b'{"answer":null}'])
async def test_invalid_json_does_not_escape_response_body(body):
    adapter = WorkspaceHTTPAgentAdapter(
        "http://agent.test/run",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
    )
    with pytest.raises(AdapterError, match="^invalid_response$"):
        await adapter.execute(sample_case(), TraceRecorder("run"), RunConfig(), 42)


@pytest.mark.parametrize("budget", [0, -1, float("inf"), float("nan"), True])
async def test_invalid_timeout_is_a_configuration_error(budget):
    adapter = WorkspaceHTTPAgentAdapter("http://agent.test/run", timeout=budget)
    with pytest.raises(AdapterError, match="^invalid_configuration$"):
        await adapter.execute(sample_case(), TraceRecorder("run"), RunConfig(), 42)


async def test_trace_size_is_bounded():
    adapter = WorkspaceHTTPAgentAdapter(
        "http://agent.test/run",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"answer": "5", "trace": [{"span_id": "a", "event_type": "agent"}] * 1001},
            )
        ),
    )
    with pytest.raises(AdapterError, match="^invalid_response$"):
        await adapter.execute(sample_case(), TraceRecorder("run"), RunConfig(), 42)
