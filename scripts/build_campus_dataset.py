"""Build an independent campus dataset from reviewed, versioned source excerpts."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "datasets/campus/sources"
# Human-authored questions and source references; no generated model answers.
SPECS = [
    ("selection-window", "2024级新生选课时间和选课阶段是什么？", "course-selection-2024", 6),
    ("selection-plan", "2024级选课时教学计划课程号在哪里查询？", "course-selection-2024", 18),
    (
        "selection-general",
        "2024级新生这次选课需要选择全校通选课程和通识核心课程吗？",
        "course-selection-2024",
        20,
    ),
    (
        "english-exception",
        "2024级新生英语水平分级测试参加对象包括翻译学院吗？",
        "english-placement-2024",
        6,
    ),
    (
        "english-duration",
        "2024级英语分级测试考试时间共多久，英语考试有听力吗？",
        "english-placement-2024",
        13,
    ),
    (
        "navigation-quota",
        "2024级智能导航特色班面向谁招生，择优录取多少名？",
        "navigation-admission-2024",
        14,
    ),
    (
        "navigation-weight",
        "2024级智能导航特色班笔试与面试成绩各占多少？",
        "navigation-admission-2024",
        16,
    ),
    (
        "data-science-scope",
        "2024级数据科学实验班高考选科要求是什么？",
        "data-science-admission-2024",
        110,
    ),
    (
        "data-science-certificate",
        "2024级数据科学实验班毕业生颁发什么学历和学位证书？",
        "data-science-admission-2024",
        126,
    ),
    (
        "law-quota",
        "2024级计算法学实验班招生计划人数是多少？",
        "computational-law-admission-2024",
        12,
    ),
    (
        "law-weight",
        "2024级计算法学实验班笔试和面试成绩计算比例是多少？",
        "computational-law-admission-2024",
        27,
    ),
    ("cs-duration", "2024版计算机科学与技术专业的弹性修业年限是多少？", "cs-curriculum-2024", 68),
    ("ai-duration", "2024版人工智能专业的弹性修业年限是多少？", "ai-curriculum-2024", 65),
    ("physics-credits", "2023级物理学专业毕业学分是多少？", "physics-curriculum-2023", 32),
]


def build() -> list[dict]:
    manifest = json.loads((SOURCES / "manifest.json").read_text())
    documents = []
    for source in manifest["documents"]:
        raw = (SOURCES / "corpus" / source["file_name"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["excerpt_sha256"]:
            raise ValueError("source excerpt digest mismatch")
        excerpt = raw.decode()
        if any(
            f"## 原文第 {p['index']} 段\n\n{p['text']}\n" not in excerpt
            for p in source["paragraphs"]
        ):
            raise ValueError("source paragraph mismatch")
        documents.append(
            {
                "id": source["id"],
                "title": source["title"],
                "campus": source["campus"],
                "cohort": source["cohort"],
                "year": source["year"],
                "source_sha256": source["source_sha256"],
                "text": "\n".join(
                    f"[{source['id']}:p{p['index']}] {p['text']}" for p in source["paragraphs"]
                ),
            }
        )
    cases = []
    for name, question, source_id, paragraph in SPECS:
        year = 2023 if source_id.endswith("2023") else 2024
        cases.append(
            {
                "case_id": f"campus-{name}",
                "task": question,
                "category": "retrieval",
                "difficulty": "medium",
                "expected_answer": None,
                "expected_tool_sequence": None,
                "assertions": [
                    {"kind": "source_quote", "value": source_id},
                    {"kind": "contains", "value": f"[{source_id}:p{paragraph}]"},
                ],
                "fixtures": {"documents": documents, "scope": {"campus": "威海", "year": year}},
                "tags": [
                    "campus",
                    "historical-sources",
                    "quote-contract",
                    f"source-version-{year}",
                ],
            }
        )
    for name, question, scope in [
        (
            "unknown-topic",
            "校园网账号忘记密码，在哪里重置？",
            {"campus": "威海", "year": 2024},
        ),
        ("wrong-campus", "2024级新生选课时间是什么？", {"campus": "青岛", "year": 2024}),
        ("current-policy", "2026级新生选课时间是什么？", {"campus": "威海", "year": 2026}),
    ]:
        cases.append(
            {
                "case_id": f"campus-{name}",
                "task": question,
                "category": "retrieval",
                "difficulty": "hard",
                "expected_answer": None,
                "expected_tool_sequence": ["search_campus_sources"],
                "assertions": [
                    {"kind": "contains", "value": "当前资料没有可核对的依据"},
                    {"kind": "not_contains", "value": "[course-selection-2024]"},
                ],
                "fixtures": {"documents": documents, "scope": scope},
                "tags": ["campus", "historical-sources", "refusal"],
            }
        )
    return cases


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "datasets/campus.json")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote {len(build())} cases to {args.output.name}")


if __name__ == "__main__":
    main()
