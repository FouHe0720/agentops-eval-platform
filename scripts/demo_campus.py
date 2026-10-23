"""Run the campus evidence contract and save a reviewed, content-free summary."""

import asyncio
import json
from pathlib import Path

from agentops_eval.adapters.campus import CampusEvidenceAdapter
from agentops_eval.domain.models import RunConfig
from agentops_eval.infra.storage import RunStore, export_record
from agentops_eval.services.datasets import load_dataset
from agentops_eval.services.metrics import run_metrics
from agentops_eval.services.runner import run_evaluation

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    cases = load_dataset(ROOT / "datasets/campus.json")
    # Only this bundled, reviewed historical dataset opts into readable content.
    # The generic CLI keeps its content-minimizing default.
    record = asyncio.run(
        run_evaluation(cases, CampusEvidenceAdapter(), RunConfig(seed=42, sensitive_trace=True))
    )
    RunStore(ROOT / "runtime/campus.db").save(record)
    export_record(record, ROOT / "runtime/campus.json")
    manifest = json.loads((ROOT / "datasets/campus/sources/manifest.json").read_text())
    by_source = {source["id"]: source for source in manifest["documents"]}
    summary = {
        "dataset": "datasets/campus.json",
        "dataset_digest": record.dataset_digest,
        "run_id": record.run_id,
        "agent": record.agent,
        "created_at": record.created_at.isoformat(),
        "scope": "historical-source quote and refusal contracts; no model inference",
        "metrics": run_metrics(record),
        "cases": [],
    }
    for case, result in zip(cases, record.results, strict=True):
        citations = [a.value for a in case.assertions if a.kind == "source_quote"]
        summary["cases"].append(
            {
                "case_id": case.case_id,
                "passed": result.success,
                "failure": result.failure,
                "source_ids": citations,
                "source_sha256": {key: by_source[key]["source_sha256"] for key in citations},
                "required_scope": case.fixtures["scope"],
            }
        )
    target = ROOT / "reports/campus-regression.json"
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    metric = summary["metrics"]["task_success_rate"]
    lines = [
        "# 校园资料回归记录",
        "",
        f"本次引用与拒答检查：{metric['numerator']} / {metric['denominator']}。",
        "数据集 17 条：14 条原文引用检查，3 条资料缺失、校区或版本不匹配拒绝检查。",
        "词法基线从实际摘录中返回原文；没有调用 LLM，不表示模型回答准确率。",
        "token 和费用均为 null。毫秒耗时来自本地规则执行，不作模型性能指标。",
        "",
        f"数据 SHA-256：`{record.dataset_digest}`。",
        "",
        "| 用例 | 结果 | 来源 |",
        "| --- | --- | --- |",
    ]
    for row in summary["cases"]:
        lines.append(
            f"| {row['case_id']} | {'通过' if row['passed'] else '失败'} | "
            f"{', '.join(row['source_ids']) or '无适用来源，应拒绝'} |"
        )
    lines += ["", "完整分母与来源指纹见同目录 campus-regression.json。", ""]
    target.with_suffix(".md").write_text("\n".join(lines))
    print(f"Campus evidence regression: {metric['numerator']}/{metric['denominator']}")
    print("Saved runtime/campus.db and reports/campus-regression.{json,md}")
    if metric["numerator"] != metric["denominator"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
