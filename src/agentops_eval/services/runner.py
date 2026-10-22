import asyncio
from time import perf_counter
from uuid import uuid4

from pydantic import ValidationError

from agentops_eval.adapters.base import AdapterError, AgentAdapter
from agentops_eval.domain.models import AgentResponse, EvalCase, EvalResult, RunConfig, RunRecord
from agentops_eval.services.datasets import digest, validate_cases
from agentops_eval.services.scoring import score
from agentops_eval.services.tracing import TraceRecorder


async def run_evaluation(
    cases: list[EvalCase], adapter: AgentAdapter, config: RunConfig
) -> RunRecord:
    """Run cases with cooperative cancellation and a total budget across retries.

    Blocking async adapters cannot be forcibly stopped here. An overrun is detected
    when they return; untrusted blocking adapters require process isolation.
    """
    validate_cases(cases)
    cases = [case.model_copy(deep=True) for case in cases]
    config = config.model_copy(deep=True)
    run_id = uuid4().hex
    semaphore = asyncio.Semaphore(config.concurrency)

    async def evaluate(case: EvalCase, repeat: int) -> EvalResult:
        async with semaphore:
            trace = TraceRecorder(run_id)
            start = perf_counter()
            base = dict(
                run_id=run_id,
                case_id=case.case_id,
                category=case.category,
                difficulty=case.difficulty,
                repeat=repeat,
            )
            status, failure, response, attempts = "error", "unclassified", None, 0
            try:
                if case.category not in adapter.capabilities:
                    return EvalResult(**base, status="skipped", latency_ms=0, attempts=0)
                with trace.span("agent", case_id=case.case_id, repeat=repeat) as agent_span:
                    for attempt in range(config.max_retries + 1):
                        attempts = attempt + 1
                        try:
                            adapter_case = case.model_copy(deep=True)
                            adapter_case.expected_answer = None
                            adapter_case.expected_tool_sequence = None
                            adapter_case.assertions = []
                            remaining = case.timeout - (perf_counter() - start)
                            if remaining <= 0:
                                raise TimeoutError
                            async with asyncio.timeout(remaining):
                                raw_response = await adapter.execute(
                                    adapter_case,
                                    trace,
                                    config.model_copy(deep=True),
                                    config.seed + repeat,
                                )
                            # A synchronous or cancellation-suppressing adapter can
                            # return after the budget without asyncio raising.
                            if perf_counter() - start >= case.timeout:
                                raise TimeoutError
                            response = AgentResponse.model_validate(
                                raw_response.model_dump(warnings=False)
                                if isinstance(raw_response, AgentResponse)
                                else raw_response
                            )
                            if perf_counter() - start >= case.timeout:
                                response = None
                                raise TimeoutError
                            status, failure = "completed", None
                            break
                        except TimeoutError:
                            status, failure = "timeout", "timeout"
                            trace.mark_timeout()
                            break
                        except ValidationError:
                            if perf_counter() - start >= case.timeout:
                                status, failure = "timeout", "timeout"
                                trace.mark_timeout()
                            else:
                                failure = "argument_error"
                            break
                        except AdapterError as exc:
                            if perf_counter() - start >= case.timeout:
                                status, failure = "timeout", "timeout"
                                trace.mark_timeout()
                                break
                            failure = (
                                "argument_error"
                                if exc.code == "invalid_response"
                                else "tool_execution_error"
                            )
                            if not exc.retryable or attempt == config.max_retries:
                                break
                            with trace.span("retry", retry_index=attempt + 1):
                                pass
                        except Exception:
                            # Never persist arbitrary upstream exception messages.
                            if perf_counter() - start >= case.timeout:
                                status, failure = "timeout", "timeout"
                                trace.mark_timeout()
                            else:
                                failure = "unclassified"
                            break
                    if response is not None:
                        with trace.span("final"):
                            pass
                    elif status != "timeout":
                        agent_span.status = "error"
                run_trace = trace.finish()
            except Exception:
                # A malformed adapter trace must not abort sibling evaluations.
                status, failure, response = "error", "unclassified", None
                safe_trace = TraceRecorder(run_id)
                with safe_trace.span("agent", case_id=case.case_id, repeat=repeat) as event:
                    event.status = "error"
                run_trace = safe_trace.finish()
            tools = [e.tool_name for e in run_trace.events if e.event_type == "tool_call"]
            success, selection, failure = score(case, response, tools, failure=failure)
            return EvalResult(
                **base,
                status=status,
                answer=response.answer if response else None,
                success=success,
                tool_selection_correct=selection,
                failure=failure,
                latency_ms=(perf_counter() - start) * 1000,
                attempts=attempts,
                trace=run_trace,
            )

    results = await asyncio.gather(*(evaluate(c, r) for r in range(config.repeats) for c in cases))
    return RunRecord(
        run_id=run_id,
        agent=adapter.name,
        dataset_digest=digest(cases),
        cases=cases,
        config=config,
        results=results,
    )
