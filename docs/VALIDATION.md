# 验证记录

## 2026-10-23：校园资料与回归

| 检查 | 结果 |
| --- | --- |
| `make lint test` | Ruff lint/format 通过；222 项 pytest 通过 |
| `make regression` | 合成子集 10/10，答案与工具序列均通过 |
| `make campus-demo` | 历史资料引用与拒答规则 17/17 |
| `make demo` | 5 个配置 × 100 cases × 3 repeats；基线 195/300，候选 300/300，配对区间 [0.26, 0.44] |
| 数据重建 | 校验 Markdown 指纹和段落一致性，输出可逐字节重建 |
| 引用评分 | 伪造引用、空原文、畸形来源、范围过滤与评分参考隔离检查通过 |

校园基线没有模型调用，token/费用为 null。17/17 是确定性原文引用与拒答检查，不是模型问答准确率；两组数据独立运行。逐案摘要见 [`campus-regression.json`](../reports/campus-regression.json)，来源见 [`manifest.json`](../datasets/campus/sources/manifest.json)。

固定校园 Demo 对已审查摘录显式保留正文，以便工作台展示问题和原文。通用 CLI 默认仍删除正文。SQLite、完整 JSON/JSONL 与本地运行文件不随仓库发布。

工作台浏览器检查：1440 × 1200 桌面与 390 × 844 移动均无整页横向溢出、无 JavaScript 异常。展开第一条 Trace 可核对 `course-selection-2024:p6`，hard 筛选为 3 条，恢复后为 17 条。截图来自已保存的校园运行：

- [桌面工作台](assets/campus-dashboard.png)
- [移动工作台](assets/campus-mobile.png)

本轮没有调用真实 LLM 或远端 Agent，也未重跑 Docker 和远端 CI。

## 2026-10-22：合成实验

环境：WSL Linux，CPython 3.12.15 项目环境，uv 0.12.24，Docker 29.8.2。使用锁文件安装、213 项测试、Ruff、数据重建、构建包和干净环境 Quick Start 均通过；wheel 包含 Dashboard HTML。

固定 seed=42、100 cases、3 repeats，每个策略 300 个结果。运行身份和配置见 [`run_manifest.json`](../reports/run_manifest.json)，重跑会更新运行 ID 与实际耗时。

| 策略 | 任务成功 | 相对完整候选的差值 |
| --- | ---: | ---: |
| baseline_v1 | 195 / 300 | −0.35 |
| candidate_v2 | 300 / 300 | 0 |
| no_planner | 222 / 300 | −0.26 |
| basic_tool_descriptions | 240 / 300 | −0.20 |
| no_tool_retries | 255 / 300 | −0.15 |

基线与候选的 paired delta=0.35，100 个 case clusters、2,000 次 bootstrap 的 95% 区间为 [0.26, 0.44]。没有模型调用；本地执行耗时随机器与调度变化，不表示模型性能。失败来源是确定性的工具选择与瞬态恢复机制。

[`example-trace.jsonl`](../reports/example-trace.jsonl) 保存实际恢复任务的 Trace；逐案变化见 [`comparison.md`](../reports/comparison.md)，单变量消融见 [`ablations.md`](../reports/ablations.md)。

## 容器、浏览器与 CI 历史记录

- Docker build、Compose 配置检查、禁网容器内 regression/Demo、非 root UID 10001、health 与 Dashboard 均通过。
- Compose regression、100 × 3 Demo、启动健康检查、持久化 Run 查询与关闭流程通过；named volume 在 down 后保留。
- 合成工作台已检查兼容 A/B 配对、差值和区间、失败筛选、Trace、独立人工归因及移动布局。动态内容使用 textContent，浏览器检查未禁用 CSP。
- HTTP 与 OpenAI-compatible 协议由 MockTransport 检查，覆盖超时、HTTP 错误、redirect 拒绝、能力范围、Trace、usage/null 和凭证不回显；没有把它记作在线模型验证。
- [首次远端 CI](https://github.com/FouHe0720/agentops-eval-platform/actions/runs/39727220508) 对提交 `9733246ac92cee11300ac7a798341ee594bea28d` 运行 Python 3.11/3.12/3.13，每个版本 213 项测试、Ruff、回归、数据重建和隐私审核通过。后续提交以实际 workflow 为准。

## 可重复命令

```bash
make install
make lint test regression
make campus-demo
.venv/bin/python scripts/privacy_check.py
```

完整合成运行使用 `make demo`；它会更新当前合成摘要和运行身份，不改写校园报告。隐私脚本同时检查索引和工作树，但模式扫描不能保证识别所有秘密，公开变更仍需审阅。
