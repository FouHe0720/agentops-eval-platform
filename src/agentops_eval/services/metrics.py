"""Metrics with explicit denominators. Missing observations remain null."""

from collections import Counter, defaultdict
from statistics import mean
from typing import Any

from agentops_eval.domain.models import EvalResult, RunRecord


def ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "value": numerator / denominator if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
    }


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def compute_metrics(results: list[EvalResult]) -> dict[str, Any]:
    active = [r for r in results if r.status != "skipped"]
    scored = [r for r in active if r.success is not None]
    selection = [r for r in active if r.tool_selection_correct is not None]
    tools = [e for r in active for e in r.trace.events if e.event_type == "tool_call"]
    models = [e for r in active for e in r.trace.events if e.event_type == "model_call"]
    latency = [r.latency_ms for r in active]
    tokens = [e.token_usage for e in models if e.token_usage is not None]
    costs = [e.cost for e in models if e.cost is not None]
    grouped: dict[str, list[bool]] = defaultdict(list)
    for result in scored:
        grouped[result.case_id].append(result.success)
    repeated = [values for values in grouped.values() if len(values) >= 2]
    return {
        "total_results": len(results),
        "evaluated": len(active),
        "skipped": len(results) - len(active),
        "task_success_rate": ratio(sum(r.success is True for r in scored), len(scored)),
        "tool_selection_accuracy": ratio(
            sum(r.tool_selection_correct is True for r in selection), len(selection)
        ),
        "tool_execution_success": ratio(sum(e.status == "ok" for e in tools), len(tools)),
        "average_steps": {
            "value": len(tools) / len(active) if active else None,
            "numerator": len(tools),
            "denominator": len(active),
        },
        "latency_ms": {
            "p50": percentile(latency, 0.5),
            "p95": percentile(latency, 0.95),
            "mean": mean(latency) if latency else None,
            "denominator": len(latency),
        },
        "token_usage": {
            "total": sum(tokens) if tokens else None,
            "observed_model_calls": len(tokens),
            "model_calls": len(models),
        },
        "cost": {
            "total": sum(costs) if costs else None,
            "observed_model_calls": len(costs),
            "model_calls": len(models),
        },
        "timeout_rate": ratio(sum(r.status == "timeout" for r in active), len(active)),
        "stability": ratio(sum(len(set(v)) == 1 for v in repeated), len(repeated)),
        "failure_counts": dict(Counter(r.failure for r in active if r.failure)),
        "runner_retries": sum(max(r.attempts - 1, 0) for r in active),
        "tool_retry_events": sum(
            e.event_type == "retry" and e.tool_name is not None
            for r in active
            for e in r.trace.events
        ),
    }


def run_metrics(record: RunRecord) -> dict[str, Any]:
    return compute_metrics(record.results)
