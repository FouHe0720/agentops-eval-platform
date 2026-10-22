"""One persistence/export boundary for content minimization and secret redaction."""

import re
from typing import Any

from agentops_eval.domain.models import RunRecord

SECRET = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+/=-]+|sk-[a-z0-9_-]{8,}|gh[pousr]_[a-z0-9_]+|"
    r"(?:api[_-]?key|token|password|cookie|authorization)\s*[:=]\s*[^\s,;]+)"
)
SECRET_HEADER = re.compile(
    r"(?im)\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*[^\r\n]+"
)
SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    ["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|cookie|authorization)
    ["']?\s*[:=]\s*
    (?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\r\n,;}\]]+)
    """
)
PRIVATE_PATH = re.compile(r"(?:/home/[^\s\"']+|/mnt/[a-z]/[^\s\"']+)")
SENSITIVE_KEY = re.compile(
    r"(?i)(key|token|cookie|auth|password|prompt|content|file|body|answer|task|payload)"
)
SAFE_ERROR_CODES = {
    "not_configured",
    "invalid_configuration",
    "invalid_response",
    "http_error",
    "transport_error",
    "unsupported_category",
    "timeout",
    "unclassified",
}


def safe_metadata(metadata: dict, case_id: str, repeat: int) -> dict:
    safe = {}
    if "case_id" in metadata:
        safe["case_id"] = case_id
    if "repeat" in metadata:
        safe["repeat"] = repeat
    for key, low, high in (("retry_index", 0, 100), ("status_code", 100, 599)):
        value = metadata.get(key)
        if type(value) is int and low <= value <= high:
            safe[key] = value
    code = metadata.get("error_code")
    if isinstance(code, str) and code in SAFE_ERROR_CODES:
        safe["error_code"] = code
    return safe


def redact_strings(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_strings(v) for v in value]
    if isinstance(value, dict):
        return {redact(k): redact_strings(v) for k, v in value.items()}
    return value


def redact(value: Any) -> Any:
    if isinstance(value, str):
        value = SECRET_HEADER.sub("[REDACTED]", value)
        value = SECRET_ASSIGNMENT.sub("[REDACTED]", value)
        return PRIVATE_PATH.sub("[PATH]", SECRET.sub("[REDACTED]", value))
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SENSITIVE_KEY.search(k) else redact(v) for k, v in value.items()}
    return value


def public_record(record: RunRecord) -> RunRecord:
    data = record.model_dump(mode="json")
    for case in data["cases"]:
        if not record.config.sensitive_trace:
            case.update(
                task="[REDACTED]", expected_answer=None, assertions=[], fixtures={}, tags=[]
            )
        else:
            # Content opt-in still cannot persist common credential patterns.
            case["task"] = redact(case["task"])
            case["fixtures"] = redact(case["fixtures"])
            case["expected_answer"] = redact(case["expected_answer"])
            case["assertions"] = redact(case["assertions"])
    for result in data["results"]:
        result["answer"] = redact(result["answer"]) if record.config.sensitive_trace else None
        for event in result["trace"]["events"]:
            metadata = event["metadata"]
            if not record.config.sensitive_trace:
                metadata = safe_metadata(metadata, result["case_id"], result["repeat"])
            event["metadata"] = redact(metadata)
            if event["tool_name"]:
                event["tool_name"] = redact(event["tool_name"])[:120]
    return RunRecord.model_validate(redact_strings(data))
