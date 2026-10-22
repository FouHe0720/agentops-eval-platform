import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import Any
from uuid import uuid4

from agentops_eval.domain.models import AgentRunTrace, TraceEvent


class TraceRecorder:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.events: list[TraceEvent] = []
        self._parents: ContextVar[tuple[str, ...]] = ContextVar("trace_parents", default=())
        self._cancelled_spans: set[str] = set()

    @contextmanager
    def span(self, event_type: str, tool_name: str | None = None, **metadata: Any):
        parents = self._parents.get()
        event = TraceEvent(
            run_id=self.run_id,
            span_id=uuid4().hex,
            parent_span_id=parents[-1] if parents else None,
            event_type=event_type,
            tool_name=tool_name,
            metadata=metadata,
        )
        self.events.append(event)
        token = self._parents.set((*parents, event.span_id))
        start = perf_counter()
        try:
            yield event
        except TimeoutError:
            event.status = "timeout"
            raise
        except asyncio.CancelledError:
            # The runner promotes deadline cancellation to timeout after asyncio.timeout
            # identifies its own cancellation; external cancellation remains an error.
            event.status = "error"
            self._cancelled_spans.add(event.span_id)
            raise
        except BaseException:
            event.status = "error"
            raise
        finally:
            event.duration_ms = (perf_counter() - start) * 1000
            self._parents.reset(token)

    def mark_timeout(self) -> None:
        timed_out = self._cancelled_spans | set(self._parents.get())
        for event in self.events:
            if event.span_id in timed_out:
                event.status = "timeout"

    def finish(self) -> AgentRunTrace:
        # Revalidate mutable event instances at the adapter boundary.
        return AgentRunTrace.model_validate(
            {"events": [e.model_dump(warnings=False) for e in self.events]}
        )
