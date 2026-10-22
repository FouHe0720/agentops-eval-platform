import asyncio
import time

import pytest

from agentops_eval.adapters.base import AdapterError
from agentops_eval.domain.models import AgentResponse, Assertion, EvalCase, RunConfig
from agentops_eval.services.runner import run_evaluation
from agentops_eval.services.tracing import TraceRecorder


def make_case(case_id="case-1", **overrides):
    values = dict(
        case_id=case_id,
        task="Add two and three",
        category="arithmetic",
        expected_answer="5",
        expected_tool_sequence=["calculator"],
        fixtures={"nested": {"value": 1}},
    )
    values.update(overrides)
    return EvalCase(**values)


class FakeAdapter:
    name = "fake"
    capabilities = {"arithmetic"}

    def __init__(self, action):
        self.action = action

    async def execute(self, case, trace, config, seed):
        return await self.action(case, trace, config, seed)


async def test_inputs_and_configuration_are_isolated_on_every_attempt():
    received = []

    async def execute(case, trace, config, seed):
        assert case.expected_answer is None
        assert case.expected_tool_sequence is None
        assert case.assertions == []
        assert case.fixtures["nested"]["value"] == 1
        assert config.planner is True
        assert config.max_retries == 1
        received.append((case.case_id, seed))
        case.fixtures["nested"]["value"] = 999
        config.planner = False
        config.max_retries = 0
        with trace.span("tool_call", tool_name="calculator"):
            pass
        if len(received) % 2:
            raise AdapterError("temporary", retryable=True)
        return AgentResponse(answer="5")

    original = make_case(
        assertions=[Assertion(kind="contains", value="5")],
        expected_tool_sequence=["calculator", "calculator"],
    )
    config = RunConfig(max_retries=1, repeats=2, concurrency=1)
    run = await run_evaluation([original], FakeAdapter(execute), config)
    assert received == [("case-1", 42), ("case-1", 42), ("case-1", 43), ("case-1", 43)]
    assert all(result.success and result.tool_selection_correct for result in run.results)
    assert all(result.attempts == 2 for result in run.results)
    assert original.fixtures["nested"]["value"] == 1
    assert original.expected_answer == "5"
    assert len(original.assertions) == 1
    assert config.planner and config.max_retries == 1
    assert run.config == config
    assert run.cases == [original]


@pytest.mark.parametrize("invalid", [None, {"answer": 5}, {"answer": "5", "extra": True}])
async def test_invalid_responses_fail_one_case_without_aborting_siblings(invalid):
    async def execute(case, trace, config, seed):
        if case.case_id == "bad":
            return invalid
        return {"answer": "5"}

    run = await run_evaluation(
        [make_case("bad"), make_case("good")], FakeAdapter(execute), RunConfig()
    )
    bad, good = run.results
    assert (bad.status, bad.failure, bad.success) == ("error", "argument_error", False)
    assert bad.tool_selection_correct is False
    assert bad.attempts == 1
    assert (good.status, good.success) == ("completed", True)


async def test_mutated_response_instances_are_revalidated():
    async def execute(case, trace, config, seed):
        response = AgentResponse(answer="5")
        response.answer = 5
        return response

    result = (await run_evaluation([make_case()], FakeAdapter(execute), RunConfig())).results[0]
    assert (result.status, result.failure) == ("error", "argument_error")


async def test_malformed_trace_is_isolated_to_one_case():
    async def execute(case, trace, config, seed):
        if case.case_id == "bad":
            trace.events[0].parent_span_id = "missing-parent"
        return AgentResponse(answer="5")

    run = await run_evaluation(
        [make_case("bad"), make_case("good")], FakeAdapter(execute), RunConfig()
    )
    assert run.results[0].status == "error"
    assert run.results[0].attempts == 1
    assert run.results[0].trace.events[0].status == "error"
    assert run.results[1].success is True


@pytest.mark.parametrize("max_retries", [0, 1, 3])
async def test_runner_retry_limit_and_ordered_tool_selection(max_retries):
    calls = 0

    async def execute(case, trace, config, seed):
        nonlocal calls
        calls += 1
        with trace.span("tool_call", tool_name="calculator"):
            raise AdapterError("temporary", retryable=True)

    result = (
        await run_evaluation(
            [make_case(expected_tool_sequence=["calculator"] * (max_retries + 1))],
            FakeAdapter(execute),
            RunConfig(max_retries=max_retries),
        )
    ).results[0]
    assert calls == result.attempts == max_retries + 1
    assert result.failure == "tool_execution_error"
    assert result.success is False
    assert result.tool_selection_correct is True
    assert sum(event.event_type == "retry" for event in result.trace.events) == max_retries


async def test_nonretryable_adapter_error_does_not_retry():
    async def execute(case, trace, config, seed):
        raise AdapterError("invalid_response", retryable=False)

    result = (
        await run_evaluation([make_case()], FakeAdapter(execute), RunConfig(max_retries=3))
    ).results[0]
    assert result.attempts == 1
    assert result.failure == "argument_error"


@pytest.mark.parametrize("has_reference", [False, True])
async def test_timeout_marks_nested_trace_and_scores_reference_denominators(has_reference):
    async def execute(case, trace, config, seed):
        with trace.span("tool_call", tool_name="calculator"):
            with trace.span("model_call"):
                await asyncio.sleep(1)
        return AgentResponse(answer="5")

    case = make_case(timeout=0.02, expected_answer="5" if has_reference else None)
    result = (await run_evaluation([case], FakeAdapter(execute), RunConfig(max_retries=3))).results[
        0
    ]
    assert (result.status, result.failure, result.attempts) == ("timeout", "timeout", 1)
    assert result.success is (False if has_reference else None)
    assert result.tool_selection_correct is True
    assert all(event.status == "timeout" for event in result.trace.events)


async def test_retries_share_one_total_timeout_budget():
    async def execute(case, trace, config, seed):
        with trace.span("tool_call", tool_name="calculator"):
            await asyncio.sleep(0.03)
            raise AdapterError("temporary", retryable=True)

    result = (
        await run_evaluation(
            [make_case(timeout=0.05)], FakeAdapter(execute), RunConfig(max_retries=3)
        )
    ).results[0]
    assert result.status == "timeout"
    assert result.attempts == 2
    assert result.tool_selection_correct is False
    tools = [event for event in result.trace.events if event.event_type == "tool_call"]
    assert [event.status for event in tools] == ["error", "timeout"]


def blocking_pause():
    time.sleep(0.02)


@pytest.mark.parametrize("outcome", ["response", "adapter_error", "exception"])
async def test_synchronous_overrun_is_detected_after_adapter_returns(outcome):
    async def execute(case, trace, config, seed):
        blocking_pause()
        if outcome == "adapter_error":
            raise AdapterError("temporary", retryable=True)
        if outcome == "exception":
            raise RuntimeError("private upstream message")
        return AgentResponse(answer="5")

    result = (
        await run_evaluation([make_case(timeout=0.005)], FakeAdapter(execute), RunConfig())
    ).results[0]
    assert result.status == "timeout"
    assert result.answer is None
    assert result.trace.events[0].status == "timeout"


async def test_cancellation_suppression_cannot_turn_timeout_into_success():
    async def execute(case, trace, config, seed):
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            return AgentResponse(answer="5")

    result = (
        await run_evaluation([make_case(timeout=0.01)], FakeAdapter(execute), RunConfig())
    ).results[0]
    assert result.status == "timeout"
    assert result.success is False


async def test_external_cancellation_propagates_and_closes_spans():
    started = asyncio.Event()
    traces = []

    async def execute(case, trace, config, seed):
        traces.append(trace)
        with trace.span("tool_call", tool_name="calculator"):
            started.set()
            await asyncio.sleep(1)
        return AgentResponse(answer="5")

    task = asyncio.create_task(run_evaluation([make_case()], FakeAdapter(execute), RunConfig()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(event.status == "error" for event in traces[0].finish().events)
    with traces[0].span("final") as event:
        assert event.parent_span_id is None


async def test_concurrent_tool_spans_are_siblings():
    trace = TraceRecorder("run")
    started = asyncio.Event()
    both_started = asyncio.Event()

    async def first():
        with trace.span("tool_call", tool_name="first"):
            started.set()
            await both_started.wait()

    async def second():
        await started.wait()
        with trace.span("tool_call", tool_name="second"):
            both_started.set()
            await asyncio.sleep(0)

    with trace.span("agent") as root:
        await asyncio.gather(first(), second())
    events = trace.finish().events
    assert len(events) == 3
    assert all(event.parent_span_id == root.span_id for event in events[1:])


async def test_concurrency_limit_and_seed_repeats():
    active = 0
    peak = 0
    seen = []

    async def execute(case, trace, config, seed):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        seen.append((case.case_id, seed))
        await asyncio.sleep(0.001)
        active -= 1
        return AgentResponse(answer="5")

    run = await run_evaluation(
        [make_case("first"), make_case("second")],
        FakeAdapter(execute),
        RunConfig(concurrency=2, repeats=3, seed=7),
    )
    assert peak == 2
    assert [(result.case_id, result.repeat) for result in run.results] == [
        (case_id, repeat) for repeat in range(3) for case_id in ("first", "second")
    ]
    assert sorted(seen) == sorted(
        (case_id, 7 + repeat) for repeat in range(3) for case_id in ("first", "second")
    )


async def test_unsupported_cases_are_skipped_without_metrics():
    async def execute(case, trace, config, seed):
        pytest.fail("unsupported case must not execute")

    result = (
        await run_evaluation([make_case(category="retrieval")], FakeAdapter(execute), RunConfig())
    ).results[0]
    assert result.status == "skipped"
    assert result.success is None and result.tool_selection_correct is None
    assert result.attempts == 0 and result.trace.events == []
