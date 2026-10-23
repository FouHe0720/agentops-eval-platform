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
        if assertion.kind == "source_quote":
            # The oracle is kept outside adapters. Require a known citation and
            # its exact reviewed passage, rather than accepting an invented ID.
            documents = case.fixtures.get("documents")
            source = next(
                (
                    d
                    for d in (documents if isinstance(documents, list) else [])
                    if isinstance(d, dict) and d.get("id") == assertion.value
                ),
                None,
            )
            text = source.get("text") if source else None
            checks.append(
                isinstance(text, str)
                and bool(text)
                and f"[{assertion.value}]" in response.answer
                and text in response.answer
            )
            continue
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
