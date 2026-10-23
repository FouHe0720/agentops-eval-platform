"""Offline evidence baseline. Returns source quotes; performs no model inference."""

import asyncio
import re
from typing import Any

from agentops_eval.domain.models import AgentResponse, EvalCase, RunConfig
from agentops_eval.services.tracing import TraceRecorder

REFUSAL = "当前资料没有可核对的依据，请向学校相关部门确认。"


def terms(text: str) -> set[str]:
    """Chinese bigrams and Latin words; intentionally small, inspectable baseline."""
    result = set(re.findall(r"[a-z0-9]+", text.lower()))
    for word in re.findall(r"[\u4e00-\u9fff]+", text):
        result.update(word[i : i + 2] for i in range(len(word) - 1))
    return result


def search_documents(question: str, documents: list[dict[str, Any]], scope: dict) -> list[dict]:
    query = terms(question)
    if not query:
        return []
    ranked = []
    for doc in documents:
        if any(doc.get(key) != value for key, value in scope.items()):
            continue
        overlap = query & terms(doc["title"] + " " + doc["text"])
        coverage = len(overlap) / len(query)
        if len(overlap) >= 2 and coverage >= 0.18:
            ranked.append((coverage, doc["id"], doc))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in ranked[:2]]


class CampusEvidenceAdapter:
    """Lexical retrieval for contract regression, never an LLM quality benchmark."""

    name = "campus_evidence"
    capabilities = {"retrieval"}

    async def execute(
        self, case: EvalCase, trace: TraceRecorder, config: RunConfig, seed: int
    ) -> AgentResponse:
        await asyncio.sleep(0)
        documents = case.fixtures.get("documents", [])
        scope = case.fixtures.get("scope", {})
        with trace.span("tool_call", tool_name="search_campus_sources"):
            matches = search_documents(case.task, documents, scope)
            with trace.span("tool_result", tool_name="search_campus_sources"):
                pass
        if not matches:
            return AgentResponse(answer=REFUSAL)
        quotes = []
        for doc in matches:
            with trace.span("tool_call", tool_name="read_campus_source"):
                quotes.append(f"[{doc['id']}] {doc['title']}\n{doc['text']}")
                with trace.span("tool_result", tool_name="read_campus_source"):
                    pass
        return AgentResponse(
            answer="参考摘录（未改写；资料版本见来源说明）：\n" + "\n\n".join(quotes)
        )
