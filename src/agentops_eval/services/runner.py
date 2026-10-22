import asyncio
from time import perf_counter
from uuid import uuid4

from agentops_eval.adapters.base import AdapterError, AgentAdapter
from agentops_eval.domain.models import EvalCase, EvalResult, RunConfig, RunRecord
from agentops_eval.services.datasets import digest, validate_cases
from agentops_eval.services.scoring import score
from agentops_eval.services.tracing import TraceRecorder


async def run_evaluation(
    cases: list[EvalCase], adapter: AgentAdapter, config: RunConfig
) -> RunRecord:
    validate_cases(cases)
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
            if case.category not in adapter.capabilities:
                return EvalResult(**base, status="skipped", latency_ms=0, attempts=0)
            status, failure, response, attempts = "error", "unclassified", None, 0
            with trace.span("agent", case_id=case.case_id, repeat=repeat):
                for attempt in range(config.max_retries + 1):
                    attempts = attempt + 1
                    try:
                        # A timeout is a total case budget, including runner retries.
                        remaining = case.timeout - (perf_counter() - start)
                        if remaining <= 0:
                            raise TimeoutError
                        async with asyncio.timeout(remaining):
                            response = await adapter.execute(
                                case.model_copy(deep=True), trace, config, config.seed + repeat
                            )
                        status, failure = "completed", None
                        break
                    except TimeoutError:
                        status, failure = "timeout", "timeout"
                        trace.events[0].status = "timeout"
                        break
                    except AdapterError as exc:
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
                        failure = "unclassified"  # do not store arbitrary exception messages
                        break
                if response is not None:
                    with trace.span("final"):
                        pass
                elif status != "timeout":
                    trace.events[0].status = "error"
            tools = [e.tool_name for e in trace.events if e.event_type == "tool_call"]
            success, selection = False, None
            if response is not None:
                success, selection, failure = score(case, response, tools)
            return EvalResult(
                **base,
                status=status,
                answer=response.answer if response else None,
                success=success,
                tool_selection_correct=selection,
                failure=failure,
                latency_ms=(perf_counter() - start) * 1000,
                attempts=attempts,
                trace=trace.finish(),
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
