"""Minimal OpenAI-compatible text inference, enabled only by explicit configuration."""

import json
import os

import httpx

from agentops_eval.adapters.base import AdapterError
from agentops_eval.adapters.http import _endpoint, _post_json, _timeout
from agentops_eval.domain.models import AgentResponse, EvalCase, RunConfig
from agentops_eval.services.tracing import TraceRecorder


def _answer_and_usage(payload: object) -> tuple[str, int | None]:
    try:
        if not isinstance(payload, dict):
            raise ValueError
        choices = payload["choices"]
        if not isinstance(choices, list) or not choices:
            raise ValueError
        if not isinstance(choices[0], dict):
            raise ValueError
        message = choices[0]["message"]
        if not isinstance(message, dict):
            raise ValueError
        answer = message["content"]
        if not isinstance(answer, str) or len(answer) > 20000 or message.get("tool_calls"):
            raise ValueError
        usage = payload.get("usage")
        if usage is None:
            return answer, None
        if not isinstance(usage, dict):
            raise ValueError
        for field in ("total_tokens", "prompt_tokens", "completion_tokens"):
            value = usage.get(field)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError
        tokens = usage.get("total_tokens")
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if tokens is None:
            if prompt is not None and completion is not None:
                tokens = prompt + completion
        elif prompt is not None and completion is not None and tokens != prompt + completion:
            raise ValueError
        return answer, tokens
    except (KeyError, TypeError, IndexError, ValueError):
        raise AdapterError("invalid_response") from None


class OpenAICompatibleAdapter:
    name = "openai_compatible"
    # This adapter performs text inference; it has no tool execution loop.
    capabilities = {"arithmetic", "long_input"}

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        *,
        api_key: str | None = None,
        timeout: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = base_url if base_url is not None else os.getenv("OPENAI_BASE_URL")
        self._model = model if model is not None else os.getenv("OPENAI_MODEL")
        self._api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY")
        self._timeout = timeout
        self._transport = transport

    async def execute(
        self, case: EvalCase, trace: TraceRecorder, config: RunConfig, seed: int
    ) -> AgentResponse:
        if case.category not in self.capabilities:
            raise AdapterError("unsupported_category")
        event = None
        try:
            with trace.span("model_call", adapter=self.name) as event:
                if not self._model:
                    raise AdapterError("not_configured")
                if not isinstance(self._model, str) or not self._model.strip():
                    raise AdapterError("invalid_configuration")
                base_url = _endpoint(self._base_url)
                if httpx.URL(base_url).query:
                    raise AdapterError("invalid_configuration")
                payload = await _post_json(
                    base_url.rstrip("/") + "/chat/completions",
                    {
                        "model": self._model,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "Solve the task using only the supplied fixtures. "
                                    "Return only the final answer. External tools are unavailable."
                                ),
                            },
                            {
                                "role": "user",
                                "content": json.dumps(
                                    {
                                        "task": case.task,
                                        "category": case.category,
                                        "fixtures": case.fixtures,
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        ],
                    },
                    api_key=self._api_key,
                    request_timeout=_timeout(self._timeout, case.timeout),
                    transport=self._transport,
                )
                answer, event.token_usage = _answer_and_usage(payload)
                # Provider token usage is observed; no pricing is assumed.
                return AgentResponse(answer=answer)
        except TimeoutError:
            if event is not None:
                event.status = "timeout"
            raise
