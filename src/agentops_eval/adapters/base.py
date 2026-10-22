from typing import Protocol

from agentops_eval.domain.models import AgentResponse, EvalCase, RunConfig
from agentops_eval.services.tracing import TraceRecorder


class AdapterError(Exception):
    """Safe adapter boundary: never persist raw upstream response bodies."""

    def __init__(self, code: str, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class AgentAdapter(Protocol):
    name: str
    capabilities: set[str]

    async def execute(
        self, case: EvalCase, trace: TraceRecorder, config: RunConfig, seed: int
    ) -> AgentResponse: ...
