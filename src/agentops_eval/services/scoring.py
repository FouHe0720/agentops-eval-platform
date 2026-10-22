from agentops_eval.domain.models import AgentResponse, EvalCase, Failure


def score(
    case: EvalCase,
    response: AgentResponse | None,
    tools: list[str | None],
    *,
    failure: Failure | None = None,
) -> tuple[bool | None, bool | None, Failure | None]:
    selection = (
        tools == case.expected_tool_sequence if case.expected_tool_sequence is not None else None
    )
    if response is None:
        # Only cases with an answer reference belong in the task-success denominator.
        success = False if case.expected_answer is not None or case.assertions else None
        return success, selection, failure or "unclassified"
    checks = []
    if case.expected_answer is not None:
        checks.append(response.answer.strip() == case.expected_answer.strip())
    for assertion in case.assertions:
        checks.append(
            {
                "contains": assertion.value in response.answer,
                "not_contains": assertion.value not in response.answer,
                "equals": assertion.value == response.answer,
            }[assertion.kind]
        )
    success = all(checks) if checks else None
    failure = failure or response.error_kind
    if failure is None and selection is False:
        failure = "tool_selection_error"
    if failure is None and success is False:
        failure = "reasoning_error"
    return success, selection, failure
