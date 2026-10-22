"""Local dashboard, read-only evaluation API, and explicit human annotations."""

import base64
import hashlib
import re
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException

from agentops_eval.domain.models import Category, Failure, RunRecord
from agentops_eval.infra.storage import RunStore
from agentops_eval.services.comparison import compare_runs
from agentops_eval.services.metrics import compute_metrics


class Annotation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    case_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    repeat: int = Field(ge=0, le=9)
    failure: Failure


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(db: Path | str = "runtime/evaluations.db") -> FastAPI:
    store = RunStore(db)
    html = Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL)
    hashes = " ".join(
        "'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"
        for script in scripts
    )
    csp = (
        f"default-src 'none'; script-src {hashes}; style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    )
    app = FastAPI(title="AgentOps Evaluation Platform", docs_url=None, redoc_url=None)
    app.state.store = store

    @app.middleware("http")
    async def secure_response(request: Request, call_next):
        if request.method == "POST":
            content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            origin = request.headers.get("origin")
            current_origin = f"{request.url.scheme}://{request.url.netloc}"
            if content_type != "application/json":
                response = _error(415, "unsupported_media_type", "Use application/json.")
            elif origin is not None and origin != current_origin:
                response = _error(403, "origin_forbidden", "Cross-origin writes are not allowed.")
            else:
                try:
                    response = await call_next(request)
                except Exception:
                    response = _error(500, "internal_error", "The request could not be completed.")
        else:
            try:
                response = await call_next(request)
            except Exception:
                response = _error(500, "internal_error", "The request could not be completed.")
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": csp,
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
            }
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # FastAPI's default details can echo body values; keep the public boundary small.
        return _error(
            422, "invalid_request", "Request parameters or JSON do not match the contract."
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 404:
            return _error(404, "not_found", "The requested resource was not found.")
        if exc.status_code == 405:
            return _error(405, "method_not_allowed", "The method is not allowed.")
        return _error(exc.status_code, "request_failed", "The request could not be completed.")

    def record_or_404(run_id: str) -> RunRecord:
        record = store.get(run_id)
        if record is None:
            raise HTTPException(404)
        return record

    @app.get("/", response_class=HTMLResponse)
    def dashboard():
        return HTMLResponse(html)

    @app.get("/health")
    def health():
        return {"status": "ok", "schema_version": 1}

    @app.get("/runs")
    def runs(limit: Annotated[int, Query(ge=1, le=1000)] = 100):
        return {"runs": store.list(limit)}

    @app.get("/runs/{run_id}")
    def run(
        run_id: str,
        difficulty: Literal["easy", "medium", "hard"] | None = None,
        category: Category | None = None,
        failure: Failure | None = None,
        failures_only: bool = False,
    ):
        record = record_or_404(run_id)
        results = [
            result
            for result in record.results
            if (difficulty is None or result.difficulty == difficulty)
            and (category is None or result.category == category)
            and (failure is None or result.failure == failure)
            and (
                not failures_only
                or result.success is False
                or result.failure is not None
                or result.status in {"error", "timeout"}
            )
        ]
        payload = record.model_dump(mode="json")
        payload["results"] = [result.model_dump(mode="json") for result in results]
        payload["result_count"] = {"total": len(record.results), "filtered": len(results)}
        payload["metrics"] = compute_metrics(results)
        return payload

    @app.get("/metrics")
    def metrics(run_id: str | None = None):
        if run_id is None:
            latest = store.list(1)
            if not latest:
                raise HTTPException(404)
            run_id = latest[0]["run_id"]
        record = record_or_404(run_id)
        return {"run_id": record.run_id, "metrics": compute_metrics(record.results)}

    @app.get("/compare")
    def compare(baseline: str, candidate: str, allow_ablation: bool = False):
        left, right = record_or_404(baseline), record_or_404(candidate)
        try:
            return compare_runs(left, right, allow_ablation=allow_ablation)
        except ValueError:
            return _error(
                400,
                "comparison_invalid",
                "Runs must have compatible datasets, configurations, and case/repeat coverage.",
            )

    @app.get("/runs/{run_id}/annotations")
    def annotations(run_id: str):
        record_or_404(run_id)
        return {"run_id": run_id, "annotations": store.annotations(run_id)}

    @app.post("/runs/{run_id}/annotations")
    def annotate(run_id: str, annotation: Annotation):
        try:
            store.annotate(run_id, annotation.case_id, annotation.repeat, annotation.failure)
        except KeyError:
            raise HTTPException(404) from None
        return {"run_id": run_id, **annotation.model_dump()}

    return app
