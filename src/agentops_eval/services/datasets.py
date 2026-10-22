import hashlib
import json
from pathlib import Path

from pydantic import TypeAdapter

from agentops_eval.domain.models import EvalCase


def load_dataset(path: Path) -> list[EvalCase]:
    if path.stat().st_size > 10_000_000:
        raise ValueError("dataset exceeds 10 MB")
    cases = TypeAdapter(list[EvalCase]).validate_json(path.read_text())
    validate_cases(cases)
    return cases


def validate_cases(cases: list[EvalCase]) -> None:
    ids = [c.case_id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case_id")
    if not cases or len(cases) > 10000:
        raise ValueError("dataset must contain 1..10000 cases")


def digest(cases: list[EvalCase]) -> str:
    payload = json.dumps([c.model_dump(mode="json") for c in cases], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()
