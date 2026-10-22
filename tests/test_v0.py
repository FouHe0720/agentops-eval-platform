import pytest

from agentops_eval.adapters.deterministic import DeterministicBaselineAgent
from agentops_eval.domain.models import EvalCase, RunConfig, RunRecord
from agentops_eval.services.runner import run_evaluation


def case():
    return EvalCase(
        case_id="add-1",
        task="Add 2 and 3",
        category="arithmetic",
        fixtures={"operation": "add", "a": 2, "b": 3},
        expected_answer="5",
        expected_tool_sequence=["calculator"],
    )


async def test_offline_end_to_end():
    run = await run_evaluation([case()], DeterministicBaselineAgent(), RunConfig())
    assert run.results[0].success is True
    assert run.results[0].tool_selection_correct is True
    assert [e.event_type for e in run.results[0].trace.events] == [
        "agent",
        "tool_call",
        "tool_result",
        "final",
    ]
    assert RunRecord.model_validate_json(run.model_dump_json()) == run


async def test_duplicate_ids_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        await run_evaluation([case(), case()], DeterministicBaselineAgent(), RunConfig())
