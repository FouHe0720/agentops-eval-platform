"""One persistence/export boundary for content minimization and secret redaction."""

import re
from typing import Any

from agentops_eval.domain.models import RunRecord

SECRET = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+/=-]+|sk-[a-z0-9_-]{8,}|gh[pousr]_[a-z0-9_]+|"
    r"(?:api[_-]?key|token|password|cookie|authorization)\s*[:=]\s*[^\s,;]+)"
)
PRIVATE_PATH = re.compile(r"(?:/home/[^\s\"']+|/mnt/[a-z]/[^\s\"']+)")
SENSITIVE_KEY = re.compile(
    r"(?i)(key|token|cookie|auth|password|prompt|content|file|body|answer|task|payload)"
)
SAFE_META = {"case_id", "repeat", "retry_index", "status_code", "error_code"}


def redact(value: Any) -> Any:
    if isinstance(value, str):
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
                metadata = {k: v for k, v in metadata.items() if k in SAFE_META}
            event["metadata"] = redact(metadata)
            if event["tool_name"]:
                event["tool_name"] = redact(event["tool_name"])[:120]
    return RunRecord.model_validate(data)
