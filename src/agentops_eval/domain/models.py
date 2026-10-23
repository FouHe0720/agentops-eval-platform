"""Strict, versioned contracts shared by adapters, persistence and reports."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal["arithmetic", "retrieval", "recovery", "long_input"]
Failure = Literal[
    "planning_error",
    "tool_selection_error",
    "argument_error",
    "tool_execution_error",
    "reasoning_error",
    "unsupported_claim",
    "timeout",
    "unclassified",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Assertion(Contract):
    kind: Literal["contains", "not_contains", "equals", "source_quote"]
    value: str = Field(max_length=10000)


class EvalCase(Contract):
    case_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    task: str = Field(min_length=1, max_length=20000)
    category: Category
    difficulty: Literal["easy", "medium", "hard"] = "easy"
    expected_answer: str | None = Field(default=None, max_length=20000)
    expected_tool_sequence: list[str] | None = None
    assertions: list[Assertion] = Field(default_factory=list, max_length=100)
    fixtures: dict[str, Any] = Field(default_factory=dict)
    timeout: float = Field(default=2, gt=0, le=120)
    tags: list[str] = Field(default_factory=list)


class TraceEvent(Contract):
    run_id: str
    span_id: str
    parent_span_id: str | None = None
    event_type: Literal["agent", "model_call", "tool_call", "tool_result", "final", "retry"]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: float = Field(default=0, ge=0)
    tool_name: str | None = None
    status: Literal["ok", "error", "timeout"] = "ok"
    token_usage: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRunTrace(Contract):
    events: list[TraceEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_tree(self) -> "AgentRunTrace":
        ids = {e.span_id for e in self.events}
        if len(ids) != len(self.events):
            raise ValueError("duplicate span_id")
        if len({e.run_id for e in self.events}) > 1:
            raise ValueError("trace mixes runs")
        seen: set[str] = set()
        for event in self.events:
            if event.parent_span_id is not None and event.parent_span_id not in seen:
                raise ValueError("parent must exist before child (no cycles)")
            seen.add(event.span_id)
        return self


class AgentResponse(Contract):
    answer: str = Field(max_length=20000)
    error_kind: Failure | None = None


class EvalResult(Contract):
    run_id: str
    case_id: str
    category: Category
    difficulty: str
    repeat: int = Field(default=0, ge=0)
    status: Literal["completed", "error", "timeout", "skipped"]
    answer: str | None = None
    success: bool | None = None
    tool_selection_correct: bool | None = None
    failure: Failure | None = None
    latency_ms: float = Field(ge=0)
    attempts: int = Field(default=1, ge=0)
    trace: AgentRunTrace = Field(default_factory=AgentRunTrace)


class RunConfig(Contract):
    seed: int = 42
    repeats: int = Field(default=1, ge=1, le=10)
    concurrency: int = Field(default=4, ge=1, le=32)
    max_retries: int = Field(default=0, ge=0, le=3)
    planner: bool = True
    tool_descriptions: Literal["basic", "improved"] = "improved"
    tool_retries: int = Field(default=1, ge=0, le=3)
    sensitive_trace: bool = False


class RunRecord(Contract):
    schema_version: int = 1
    run_id: str
    agent: str
    dataset_digest: str
    cases: list[EvalCase]
    config: RunConfig
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    results: list[EvalResult]

    @model_validator(mode="after")
    def validate_results(self) -> "RunRecord":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("duplicate case_id")
        keys = [(result.case_id, result.repeat) for result in self.results]
        expected = {
            (case_id, repeat) for case_id in case_ids for repeat in range(self.config.repeats)
        }
        if len(keys) != len(set(keys)) or set(keys) != expected:
            raise ValueError("incomplete or duplicate case/repeat coverage")
        lookup = {case.case_id: case for case in self.cases}
        for result in self.results:
            if result.run_id != self.run_id:
                raise ValueError("result belongs to another run")
            if result.category != lookup[result.case_id].category:
                raise ValueError("result category mismatch")
            if any(event.run_id != self.run_id for event in result.trace.events):
                raise ValueError("trace belongs to another run")
        return self
