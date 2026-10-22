from agentops_eval.domain.models import AgentResponse, EvalCase, Failure


def score(
    case: EvalCase, response: AgentResponse, tools: list[str]
) -> tuple[bool | None, bool | None, Failure | None]:
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
    selection = (
        tools == case.expected_tool_sequence if case.expected_tool_sequence is not None else None
    )
    success = all(checks) if checks else None
    failure = None
    if success is False:
        failure = response.error_kind or (
            "tool_selection_error" if selection is False else "reasoning_error"
        )
    return success, selection, failure
