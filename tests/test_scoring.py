import pytest

from agentops_eval.domain.models import AgentResponse, Assertion, EvalCase
from agentops_eval.services.scoring import score


def make_case(**overrides):
    values = dict(case_id="case", task="task", category="arithmetic")
    values.update(overrides)
    return EvalCase(**values)


@pytest.mark.parametrize(
    ("case", "answer", "expected"),
    [
        (make_case(expected_answer="5"), " 5\n", True),
        (make_case(expected_answer="5"), "6", False),
        (make_case(), "5", None),
        (make_case(assertions=[Assertion(kind="contains", value="five")]), "five!", True),
        (make_case(assertions=[Assertion(kind="not_contains", value="six")]), "six", False),
        (make_case(assertions=[Assertion(kind="equals", value="5")]), " 5", False),
    ],
)
def test_answer_references_and_assertions(case, answer, expected):
    success, selection, failure = score(case, AgentResponse(answer=answer), [])
    assert success is expected
    assert selection is None
    assert failure == ("reasoning_error" if expected is False else None)


def test_all_answer_checks_must_pass():
    case = make_case(expected_answer="5", assertions=[Assertion(kind="not_contains", value="5")])
    assert score(case, AgentResponse(answer="5"), [])[0] is False


@pytest.mark.parametrize(
    ("tools", "selection"),
    [
        (["search", "read_file"], True),
        (["read_file", "search"], False),
        (["search", "search", "read_file"], False),
        (["search"], False),
        ([], False),
    ],
)
def test_tool_selection_is_strict_and_ordered_including_retries(tools, selection):
    case = make_case(expected_answer="5", expected_tool_sequence=["search", "read_file"])
    success, correct, failure = score(case, AgentResponse(answer="5"), tools)
    assert success is True
    assert correct is selection
    assert failure == (None if selection else "tool_selection_error")


@pytest.mark.parametrize("kind", ["planning_error", "argument_error", "unsupported_claim"])
@pytest.mark.parametrize("has_reference", [False, True])
def test_adapter_error_kind_is_preserved(kind, has_reference):
    case = make_case(
        expected_answer="5" if has_reference else None, expected_tool_sequence=["tool"]
    )
    success, selection, failure = score(case, AgentResponse(answer="wrong", error_kind=kind), [])
    assert success is (False if has_reference else None)
    assert selection is False
    assert failure == kind


@pytest.mark.parametrize("failure", ["timeout", "tool_execution_error", "unclassified"])
@pytest.mark.parametrize("reference", [None, "answer", "assertion"])
def test_failed_execution_reference_and_tool_selection_denominators(failure, reference):
    case = make_case(
        expected_answer="5" if reference == "answer" else None,
        assertions=[Assertion(kind="contains", value="5")] if reference == "assertion" else [],
        expected_tool_sequence=["tool"],
    )
    success, selection, attributed = score(case, None, [], failure=failure)
    assert success is (None if reference is None else False)
    assert selection is False
    assert attributed == failure


def test_empty_expected_sequence_is_a_reference_and_not_a_truthiness_check():
    assert score(make_case(expected_tool_sequence=[]), None, [], failure="timeout") == (
        None,
        True,
        "timeout",
    )
    assert score(make_case(expected_tool_sequence=[]), AgentResponse(answer="5"), [None]) == (
        None,
        False,
        "tool_selection_error",
    )
