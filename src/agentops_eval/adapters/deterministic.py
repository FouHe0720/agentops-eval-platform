"""Fixture-only agent: executes tools; never reads expected answers or host files."""

import asyncio
import operator
from typing import Any

from agentops_eval.domain.models import AgentResponse, EvalCase, RunConfig
from agentops_eval.services.tracing import TraceRecorder

OPS = {"add": operator.add, "subtract": operator.sub, "multiply": operator.mul}


class DeterministicBaselineAgent:
    capabilities = {"arithmetic", "retrieval", "recovery", "long_input"}

    def __init__(self, version: str = "candidate_v2"):
        if version not in {"baseline_v1", "candidate_v2"}:
            raise ValueError("unknown deterministic agent version")
        self.name = version

    async def execute(
        self, case: EvalCase, trace: TraceRecorder, config: RunConfig, seed: int
    ) -> AgentResponse:
        await asyncio.sleep(0)  # cancellation point; state below is per-case
        data = case.fixtures
        effective_retry = config.tool_retries if self.name == "candidate_v2" else 0
        improved = config.tool_descriptions == "improved" and self.name == "candidate_v2"

        def tool(name: str, action: Any) -> Any:
            with trace.span("tool_call", tool_name=name):
                value = action()
                with trace.span("tool_result", tool_name=name):
                    pass
                return value

        try:
            if case.category == "arithmetic":
                op = data["operation"]
                if op not in OPS:
                    return AgentResponse(answer="INVALID", error_kind="argument_error")
                value = tool("calculator", lambda: OPS[op](data["a"], data["b"]))
                return AgentResponse(answer=str(value))
            if case.category == "retrieval":
                if not config.planner and data.get("multi_step"):
                    return AgentResponse(answer="NO_PLAN", error_kind="planning_error")
                name = "search" if improved or not data.get("ambiguous") else "list_files"
                matches = tool(name, lambda: [k for k in data["files"] if data["query"] in k])
                if name == "list_files":
                    return AgentResponse(answer=",".join(matches))
                contents = [tool("read_file", lambda k=k: data["files"][k]) for k in matches]
                return AgentResponse(answer="|".join(contents) if contents else "NOT_FOUND")
            if case.category == "recovery":
                for attempt in range(effective_retry + 1):
                    try:

                        def fetch(attempt: int = attempt) -> str:
                            if attempt < data.get("failures_before_success", 1):
                                raise OSError("synthetic transient failure")
                            return data["payload"]

                        return AgentResponse(answer=tool("fetch", fetch))
                    except OSError:
                        if attempt == effective_retry:
                            return AgentResponse(
                                answer="TOOL_FAILED", error_kind="tool_execution_error"
                            )
                        with trace.span("retry", retry_index=attempt + 1):
                            pass
            if case.category == "long_input":
                if data.get("invalid"):
                    return AgentResponse(answer=tool("validate", lambda: "INVALID"))
                values = data["values"]
                if not config.planner and len(values) > 10:
                    return AgentResponse(answer="NO_PLAN", error_kind="planning_error")
                return AgentResponse(answer=str(tool("aggregate", lambda: sum(values))))
        except (KeyError, TypeError, ValueError):
            return AgentResponse(answer="INVALID", error_kind="argument_error")
        return AgentResponse(answer="UNSUPPORTED", error_kind="unclassified")
