from contextlib import contextmanager
from time import perf_counter
from typing import Any
from uuid import uuid4

from agentops_eval.domain.models import AgentRunTrace, TraceEvent


class TraceRecorder:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.events: list[TraceEvent] = []
        self._parents: list[str] = []

    @contextmanager
    def span(self, event_type: str, tool_name: str | None = None, **metadata: Any):
        event = TraceEvent(
            run_id=self.run_id,
            span_id=uuid4().hex,
            parent_span_id=self._parents[-1] if self._parents else None,
            event_type=event_type,
            tool_name=tool_name,
            metadata=metadata,
        )
        self.events.append(event)
        self._parents.append(event.span_id)
        start = perf_counter()
        try:
            yield event
        except BaseException:
            event.status = "error"
            raise
        finally:
            event.duration_ms = (perf_counter() - start) * 1000
            self._parents.pop()

    def finish(self) -> AgentRunTrace:
        return AgentRunTrace(events=self.events)
