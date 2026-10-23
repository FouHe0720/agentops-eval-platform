import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentops_eval.adapters.campus import CampusEvidenceAdapter, search_documents
from agentops_eval.cli import get_adapter
from agentops_eval.domain.models import AgentResponse, Assertion, EvalCase, RunConfig
from agentops_eval.services.datasets import load_dataset
from agentops_eval.services.runner import run_evaluation
from agentops_eval.services.scoring import score

ROOT = Path(__file__).resolve().parents[1]


def test_campus_dataset_rebuild_and_provenance(tmp_path):
    output = tmp_path / "campus.json"
    subprocess.run(
        [sys.executable, "scripts/build_campus_dataset.py", "--output", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    assert output.read_bytes() == (ROOT / "datasets/campus.json").read_bytes()
    cases = load_dataset(output)
    assert len(cases) == 17
    assert all("historical-sources" in case.tags for case in cases)
    source = next(d for d in cases[0].fixtures["documents"] if d["id"] == "cs-curriculum-2024")
    assert source["cohort"] is None  # A curriculum edition does not establish a cohort.


async def test_campus_baseline_enforces_quotes_and_refusals():
    cases = load_dataset(ROOT / "datasets/campus.json")
    record = await run_evaluation(cases, get_adapter("campus_evidence"), RunConfig())
    assert all(result.success for result in record.results)
    assert all(event.event_type != "model_call" for r in record.results for event in r.trace.events)
    assert all(result.answer for result in record.results)
    for case, result in zip(cases, record.results, strict=True):
        if "refusal" in case.tags:
            assert "当前资料没有可核对的依据" in result.answer
            assert [e.tool_name for e in result.trace.events if e.event_type == "tool_call"] == [
                "search_campus_sources"
            ]


def test_source_quote_grader_rejects_fabricated_citations_and_text():
    case = EvalCase(
        case_id="quote",
        task="query",
        category="retrieval",
        assertions=[Assertion(kind="source_quote", value="known")],
        fixtures={"documents": [{"id": "known", "text": "原始资料"}]},
    )
    assert score(case, AgentResponse(answer="[known] 原始资料"), [])[0] is True
    for answer in ["[unknown] 原始资料", "[known] 编造规则", "原始资料"]:
        assert score(case, AgentResponse(answer=answer), [])[0] is False
    case.fixtures["documents"][0]["text"] = ""
    assert score(case, AgentResponse(answer="[known]"), [])[0] is False
    case.assertions[0].value = "missing"
    assert score(case, AgentResponse(answer="[missing] 原始资料"), [])[0] is False


async def test_adapter_does_not_use_reference_fields():
    cases = load_dataset(ROOT / "datasets/campus.json")[:1]
    cases[0].expected_answer = "ORACLE_MUST_NOT_REACH_ADAPTER"
    cases[0].assertions = [Assertion(kind="contains", value="ORACLE_MUST_NOT_REACH_ADAPTER")]

    class InspectingAdapter(CampusEvidenceAdapter):
        async def execute(self, case, trace, config, seed):
            assert case.expected_answer is None
            assert case.expected_tool_sequence is None
            assert case.assertions == []
            assert "ORACLE_MUST_NOT_REACH_ADAPTER" not in json.dumps(case.fixtures)
            return await super().execute(case, trace, config, seed)

    result = (await run_evaluation(cases, InspectingAdapter(), RunConfig())).results[0]
    assert result.status == "completed"
    assert result.success is False
    assert "ORACLE_MUST_NOT_REACH_ADAPTER" not in result.answer


def test_scope_applies_before_lexical_scoring():
    documents = [{"id": "old", "title": "新生选课", "text": "新生选课时间", "year": 2024}]
    assert search_documents("新生选课时间", documents, {"year": 2026}) == []
    assert search_documents("新生选课时间", documents, {"year": 2024}) == documents


@pytest.mark.parametrize("documents", [None, {}, ["invalid"], [{"id": "known", "text": None}]])
def test_source_quote_grader_handles_invalid_source_fixtures(documents):
    case = EvalCase(
        case_id="invalid-source",
        task="query",
        category="retrieval",
        assertions=[Assertion(kind="source_quote", value="known")],
        fixtures={"documents": documents},
    )
    assert score(case, AgentResponse(answer="[known] 伪造依据"), [])[0] is False
