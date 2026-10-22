from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from agentops_eval.domain.models import Failure, RunRecord
from agentops_eval.infra.privacy import public_record


class RunStore:
    def __init__(self, path: Path | str = "runtime/evaluations.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, agent TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS annotations (
                    run_id TEXT NOT NULL, case_id TEXT NOT NULL, repeat INTEGER NOT NULL,
                    failure TEXT NOT NULL, PRIMARY KEY(run_id, case_id, repeat),
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, record: RunRecord) -> RunRecord:
        safe = public_record(record)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?)",
                (safe.run_id, safe.created_at.isoformat(), safe.agent, safe.model_dump_json()),
            )
        return safe

    def get(self, run_id: str) -> RunRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return RunRecord.model_validate_json(row[0]) if row else None

    def list(self, limit: int = 100) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT run_id, created_at, agent, json_extract(payload, '$.dataset_digest'), "
                "json_extract(payload, '$.config') FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "run_id": row[0],
                "created_at": row[1],
                "agent": row[2],
                "dataset_digest": row[3],
                "config": json.loads(row[4]),
            }
            for row in rows
        ]

    def annotate(self, run_id: str, case_id: str, repeat: int, failure: Failure) -> None:
        record = self.get(run_id)
        if record is None or not any(
            r.case_id == case_id and r.repeat == repeat for r in record.results
        ):
            raise KeyError("result not found")
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO annotations VALUES (?, ?, ?, ?)",
                (run_id, case_id, repeat, failure),
            )

    def annotations(self, run_id: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT case_id, repeat, failure FROM annotations WHERE run_id=?", (run_id,)
            ).fetchall()
        return [dict(zip(("case_id", "repeat", "failure"), row, strict=True)) for row in rows]


def export_record(record: RunRecord, output: Path) -> None:
    safe = public_record(record)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(safe.model_dump_json(indent=2) + "\n")
    with output.with_suffix(".jsonl").open("w") as handle:
        for result in safe.results:
            for event in result.trace.events:
                # Include result identity because case/repeat is necessary to group spans.
                handle.write(
                    json.dumps(
                        {
                            "case_id": result.case_id,
                            "repeat": result.repeat,
                            **event.model_dump(mode="json"),
                        }
                    )
                    + "\n"
                )
