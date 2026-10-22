"""Explicitly configured remote agents with a bounded, sanitized wire contract."""

import json
import math
import os
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentops_eval.adapters.base import AdapterError
from agentops_eval.domain.models import AgentResponse, EvalCase, RunConfig, TraceEvent
from agentops_eval.services.tracing import TraceRecorder

MAX_RESPONSE_BYTES = 1_048_576


class _RemoteEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    span_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,80}$")
    parent_span_id: str | None = Field(default=None, max_length=80)
    run_id: str | None = Field(default=None, max_length=128)
    event_type: Literal["agent", "model_call", "tool_call", "tool_result", "final", "retry"]
    duration_ms: float = Field(default=0, ge=0)
    tool_name: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.:-]{1,80}$")
    status: Literal["ok", "error", "timeout"] = "ok"
    token_usage: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)


class _RemoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    answer: str = Field(max_length=20000)
    trace: list[_RemoteEvent] = Field(default_factory=list, max_length=1000)


def _endpoint(value: str | None) -> str:
    if not value:
        raise AdapterError("not_configured")
    try:
        url = httpx.URL(value)
        if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.fragment:
            raise ValueError
    except (httpx.InvalidURL, TypeError, ValueError):
        raise AdapterError("invalid_configuration") from None
    return str(url)


def _headers(api_key: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if api_key:
        if not api_key.isascii() or "\r" in api_key or "\n" in api_key:
            raise AdapterError("invalid_configuration")
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _timeout(value: float, case_timeout: float) -> httpx.Timeout:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AdapterError("invalid_configuration")
    if not math.isfinite(value) or value <= 0:
        raise AdapterError("invalid_configuration")
    return httpx.Timeout(min(value, case_timeout))


async def _post_json(
    endpoint: str,
    payload: dict,
    *,
    api_key: str | None,
    request_timeout: httpx.Timeout,
    transport: httpx.AsyncBaseTransport | None,
) -> object:
    """No upstream URL, headers, body, or exception text crosses this boundary."""
    try:
        async with httpx.AsyncClient(
            timeout=request_timeout, follow_redirects=False, transport=transport, trust_env=False
        ) as client:
            async with client.stream(
                "POST", endpoint, json=payload, headers=_headers(api_key)
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise AdapterError(
                        "http_error",
                        retryable=response.status_code == 429 or response.status_code >= 500,
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise AdapterError("invalid_response")
                    body.extend(chunk)
        return json.loads(body)
    except httpx.TimeoutException:
        raise TimeoutError from None
    except httpx.TransportError:
        raise AdapterError("transport_error", retryable=True) from None
    except (ValueError, UnicodeError):
        raise AdapterError("invalid_response") from None


def _remote_response(payload: object) -> _RemoteResponse:
    try:
        response = _RemoteResponse.model_validate(payload)
        seen: set[str] = set()
        roots = 0
        run_ids = {event.run_id for event in response.trace if event.run_id is not None}
        if len(run_ids) > 1:
            raise ValueError
        for event in response.trace:
            if event.span_id in seen:
                raise ValueError
            if event.parent_span_id is None:
                roots += 1
            elif event.parent_span_id not in seen:
                raise ValueError
            if event.event_type in {"tool_call", "tool_result"} and event.tool_name is None:
                raise ValueError
            seen.add(event.span_id)
        if response.trace and roots != 1:
            raise ValueError
        return response
    except (ValidationError, ValueError):
        raise AdapterError("invalid_response") from None


class _HTTPAgentAdapter:
    name: str
    capabilities: set[str]
    endpoint_env: str
    api_key_env: str

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        api_key: str | None = None,
        timeout: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._endpoint = endpoint if endpoint is not None else os.getenv(self.endpoint_env)
        self._api_key = api_key if api_key is not None else os.getenv(self.api_key_env)
        self._timeout = timeout
        self._transport = transport

    async def execute(
        self, case: EvalCase, trace: TraceRecorder, config: RunConfig, seed: int
    ) -> AgentResponse:
        if case.category not in self.capabilities:
            raise AdapterError("unsupported_category")
        event = None
        try:
            with trace.span("agent", adapter=self.name) as event:
                payload = await _post_json(
                    _endpoint(self._endpoint),
                    {
                        "task": case.task,
                        "category": case.category,
                        "fixtures": case.fixtures,
                        "seed": seed,
                        "config": {
                            "planner": config.planner,
                            "tool_descriptions": config.tool_descriptions,
                            "tool_retries": config.tool_retries,
                        },
                    },
                    api_key=self._api_key,
                    request_timeout=_timeout(self._timeout, case.timeout),
                    transport=self._transport,
                )
                response = _remote_response(payload)
                mapping = {remote.span_id: uuid4().hex for remote in response.trace}
                # Validate the complete remote tree before mutating the recorder.
                imported = [
                    TraceEvent(
                        run_id=trace.run_id,
                        span_id=mapping[remote.span_id],
                        parent_span_id=(
                            mapping[remote.parent_span_id]
                            if remote.parent_span_id is not None
                            else event.span_id
                        ),
                        **remote.model_dump(exclude={"run_id", "span_id", "parent_span_id"}),
                    )
                    for remote in response.trace
                ]
                trace.events.extend(imported)
                return AgentResponse(answer=response.answer)
        except TimeoutError:
            if event is not None:
                event.status = "timeout"
            raise


class WorkspaceHTTPAgentAdapter(_HTTPAgentAdapter):
    name = "workspace_http"
    capabilities = {"arithmetic", "retrieval", "long_input"}
    endpoint_env = "WORKSPACE_AGENT_ENDPOINT"
    api_key_env = "WORKSPACE_AGENT_API_KEY"


class ResearchAgentAdapter(_HTTPAgentAdapter):
    name = "research_http"
    capabilities = {"retrieval"}
    endpoint_env = "RESEARCH_AGENT_ENDPOINT"
    api_key_env = "RESEARCH_AGENT_API_KEY"
