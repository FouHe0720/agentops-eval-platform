import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from agentops_eval.adapters.deterministic import DeterministicBaselineAgent
from agentops_eval.domain.models import RunConfig
from agentops_eval.services.datasets import load_dataset
from agentops_eval.services.runner import run_evaluation

ROOT = Path(__file__).resolve().parents[1]


def test_generated_datasets_are_reproducible(tmp_path):
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/generate_dataset.py"),
            "--seed",
            "42",
            "--output-dir",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    for filename in ("synthetic.json", "regression.json"):
        assert (tmp_path / filename).read_bytes() == (ROOT / "datasets" / filename).read_bytes()


def test_dataset_contract_and_regression_coverage():
    cases = load_dataset(ROOT / "datasets/synthetic.json")
    regression = load_dataset(ROOT / "datasets/regression.json")
    assert Counter(case.category for case in cases) == {
        "arithmetic": 30,
        "retrieval": 30,
        "recovery": 20,
        "long_input": 20,
    }
    assert len(regression) == 10
    assert set(case.category for case in regression) == {
        "arithmetic",
        "retrieval",
        "recovery",
        "long_input",
    }
    by_id = {case.case_id: case for case in cases}
    assert all(case == by_id[case.case_id] for case in regression)
    assert all(case.expected_answer is not None for case in cases)
    assert all(case.expected_tool_sequence for case in cases)
    assert all("seed-42" in case.tags and "synthetic" in case.tags for case in cases)
    assert all("expected_answer" not in json.dumps(case.fixtures) for case in cases)


def test_dataset_oracles_cover_boundaries():
    cases = {case.case_id: case for case in load_dataset(ROOT / "datasets/synthetic.json")}
    assert cases["arithmetic-007"].expected_answer == "0.75"
    assert cases["arithmetic-011"].expected_answer == "2000000000000"
    assert cases["retrieval-001"].expected_answer == "NOT_FOUND"
    assert cases["retrieval-003"].expected_answer == "Section Z2|Section A2|Section M2"
    assert cases["retrieval-005"].expected_answer == "|Synthetic detail 4\nSecond line."
    assert cases["long-input-001"].expected_answer == "0"
    assert cases["long-input-004"].expected_answer == "17"
    assert len(cases["long-input-016"].fixtures["values"]) == 256
    for case in cases.values():
        if case.category == "recovery":
            assert case.expected_tool_sequence == ["fetch"] * (
                case.fixtures["failures_before_success"] + 1
            )


@pytest.mark.parametrize("filename", ["synthetic.json", "regression.json"])
async def test_default_candidate_solves_every_case_with_expected_tool_calls(filename):
    cases = load_dataset(ROOT / "datasets" / filename)
    run = await run_evaluation(cases, DeterministicBaselineAgent("candidate_v2"), RunConfig())
    assert all(result.status == "completed" for result in run.results)
    assert all(result.success is True for result in run.results)
    assert all(result.tool_selection_correct is True for result in run.results)
    for case, result in zip(cases, run.results, strict=True):
        if case.category == "recovery":
            calls = [event for event in result.trace.events if event.event_type == "tool_call"]
            assert len(calls) == case.fixtures["failures_before_success"] + 1
            assert (
                sum(event.status == "error" for event in calls)
                == (case.fixtures["failures_before_success"])
            )


async def test_baseline_failures_come_from_tools_and_transient_errors():
    cases = load_dataset(ROOT / "datasets/synthetic.json")
    run = await run_evaluation(cases, DeterministicBaselineAgent("baseline_v1"), RunConfig())
    for case, result in zip(cases, run.results, strict=True):
        if case.category == "retrieval" and case.fixtures["ambiguous"]:
            assert result.success is False
            assert result.failure == "tool_selection_error"
            assert [
                event.tool_name for event in result.trace.events if event.event_type == "tool_call"
            ] == ["list_files"]
        elif case.category == "recovery" and case.fixtures["failures_before_success"]:
            assert result.success is False
            assert result.answer == "TOOL_FAILED"
            assert result.failure == "tool_execution_error"
        else:
            assert result.success is True


@pytest.mark.parametrize(
    ("config", "failure", "expected_categories"),
    [
        (RunConfig(planner=False), "planning_error", {"retrieval", "long_input"}),
        (RunConfig(tool_descriptions="basic"), "tool_selection_error", {"retrieval"}),
        (RunConfig(tool_retries=0), "tool_execution_error", {"recovery"}),
    ],
)
async def test_dataset_supports_real_ablations(config, failure, expected_categories):
    cases = load_dataset(ROOT / "datasets/synthetic.json")
    run = await run_evaluation(cases, DeterministicBaselineAgent("candidate_v2"), config)
    failed = [result for result in run.results if result.success is False]
    assert failed
    assert {result.category for result in failed} == expected_categories
    assert {result.failure for result in failed} == {failure}
