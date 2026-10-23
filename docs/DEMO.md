# 离线演示与接入指南

以下命令在 WSL 的项目根目录运行，使用 Bash、Python 3.11+、uv 和 make。安装依赖或首次构建镜像需要下载公开软件包；安装完成后的默认评测使用确定性 Agent 和内存 fixtures，不需要模型、GPU、API key 或付费服务。环境与验证记录见 [VALIDATION.md](VALIDATION.md)。

## 1. 安装与最小回归

```bash
make install
make lint test regression
```

`make install` 执行 `uv sync --frozen --extra dev`，按提交的 `uv.lock` 安装到项目 `.venv`。不使用 make 时，等价命令为：

```bash
uv sync --frozen --extra dev
.venv/bin/python scripts/regression.py
```

回归脚本实际执行 `datasets/regression.json` 的 10 个 case，要求每条答案正确且工具序列正确；成功退出 0，评测回归退出 1，输入配置错误退出 2。普通 `eval run` 的退出 0 表示运行与保存完成，任务是否成功应查看输出 metrics，不能替代回归门禁。

## 2. 筛选任务、固定 seed、重复运行

执行 4 条固定 seed 抽样的困难检索任务，每条重复 3 次：

```bash
.venv/bin/eval run \
  --dataset datasets/synthetic.json \
  --agent candidate_v2 \
  --category retrieval --difficulty hard --limit 4 \
  --seed 42 --repeats 3 --concurrency 4 \
  --output runtime/filtered.json --db runtime/filtered.db
```

筛选先应用 category / difficulty / case ID，再从结果中按 seed 抽样。重复运行会生成新的 run ID 和真实耗时；相同 seed 并不意味着运行时间相同。CLI 的 `--agent baseline` 是 `candidate_v2` 的兼容别名；比较实验应明确写 `baseline_v1` 和 `candidate_v2`。

按 ID 选择两种不同类别的任务，观察恢复任务中两次 `fetch` 调用和一次工具重试：

```bash
.venv/bin/eval run \
  --dataset datasets/synthetic.json --agent candidate_v2 \
  --case-id arithmetic-001 --case-id recovery-002 \
  --seed 42 --repeats 3 --tool-retries 1 --max-retries 0 \
  --output runtime/selected.json --db runtime/selected.db
```

`--tool-retries` 控制确定性候选 Agent 的工具内重试；`--max-retries` 控制 Runner 对可重试 adapter 异常的重试，两者分别记录。JSONL 与 JSON 同目录同文件名生成；默认隐藏任务正文、fixtures 和最终答案，保留 case ID、分数、工具名、状态、耗时与层级 Trace。

## 3. 完整 A/B 与三种消融

```bash
make demo
```

`scripts/demo.py` 默认使用 `datasets/synthetic.json` 的 100 个 case、seed 42、每条 3 次重复。它执行 baseline_v1、candidate_v2，以及 candidate 的三个单配置消融，共 5 个 run、1,500 条结果。每个 run 都保存到 SQLite，导出经同一隐私边界处理的 JSON / JSONL；报告中的分数来自这些执行结果。

| 产物 | 内容 |
| --- | --- |
| `runtime/demo.db` | 5 个 run 的 SQLite 持久化记录；重跑追加新 run |
| `runtime/baseline.json`、`runtime/candidate.json` | 本次 A/B 的脱敏记录，旁边有对应 `.jsonl` |
| `runtime/no_planner.json` | 关闭 planner 的候选记录 |
| `runtime/basic_tool_descriptions.json` | 使用 basic 工具描述的候选记录 |
| `runtime/no_tool_retries.json` | 关闭工具重试的候选记录 |
| `reports/comparison.md`、`reports/results.json` | A/B 汇总、完整逐 case/repeat 配对变化、metrics、bootstrap CI |
| `reports/ablations.md`、`reports/ablations.json` | 三种消融的汇总与 metrics，JSON 省略重复的逐条 rows |
| `reports/run_manifest.json`、`reports/example-trace.jsonl` | 本次 5 个 Run 的身份/配置/指标，及 candidate 的真实层级 Trace 示例 |

同一合成数据与默认配置下，已实际运行得到 baseline 195/300、candidate 300/300，绝对成功率差为 0.35，即 35 个百分点；case-cluster bootstrap 95% CI 为 `[0.26, 0.44]`。三种消融相对完整 candidate 的成功率差分别为 planner 关闭 −0.26、basic 工具描述 −0.20、工具重试关闭 −0.15。这些结果描述确定性合成模板及其真实工具机制，不代表生产 Agent 的能力提升或模型泛化表现。具体 run ID、分母和本次耗时以重新生成的报告为准；没有模型调用时 token/cost 为 null。

更快地演示同一流程，可指定 10 条回归子集与独立输出目录：

```bash
.venv/bin/python scripts/demo.py \
  --dataset datasets/regression.json --repeats 3 \
  --runtime-dir runtime/small-demo --reports-dir runtime/small-demo/reports
```

## 4. 重新计算公平比较与显式消融

完整 demo 完成后，从其导出记录重新生成 A/B 比较：

```bash
.venv/bin/eval compare \
  --baseline runtime/baseline.json \
  --candidate runtime/candidate.json \
  --output runtime/replayed-comparison.json
```

默认比较要求相同数据 digest、相同可见 case 定义、相同 case/repeat 覆盖、相同全部配置和 seed。工具序列按严格顺序比较，重复调用计入序列；成功率与工具选择准确率有独立分母。bootstrap 以 case 为抽样单元，将同一 case 的重复结果保留在同一簇；少于 10 个适用 case 簇时会省略 CI 并给出提示。

消融必须显式允许配置差异，下面的差值方向是“关闭 planner 的候选减去完整候选”：

```bash
.venv/bin/eval compare \
  --baseline runtime/candidate.json \
  --candidate runtime/no_planner.json \
  --allow-ablation \
  --output runtime/replayed-ablation.json
```

`--allow-ablation` 只允许 planner、tool_descriptions、tool_retries 变化；seed、重复次数、并发与 Runner 重试等设置仍须相同。两个 compare 命令都会同时写同名 `.json` 和 `.md`。

## 5. 读取 SQLite、查看 Dashboard 与 API

先列出数据库里的 run，再从本次导出文件动态读取 ID，避免引用历史 run：

```bash
.venv/bin/eval inspect --db runtime/demo.db
candidate_run_id=$(.venv/bin/python -c 'import json; from pathlib import Path; print(json.loads(Path("runtime/candidate.json").read_text())["run_id"])')
baseline_run_id=$(.venv/bin/python -c 'import json; from pathlib import Path; print(json.loads(Path("runtime/baseline.json").read_text())["run_id"])')
.venv/bin/eval inspect "$candidate_run_id" --db runtime/demo.db
```

也可以直接使用 Python 标准库读取 SQLite，无需安装 sqlite3 命令：

```bash
.venv/bin/python - <<'PY'
import sqlite3
with sqlite3.connect("file:runtime/demo.db?mode=ro", uri=True) as connection:
    for row in connection.execute("SELECT run_id, agent, created_at FROM runs ORDER BY created_at DESC LIMIT 5"):
        print(row)
PY
```

在终端 A 启动服务：

```bash
make serve
```

浏览器打开 `http://127.0.0.1:8000`。页面提供 run / 难度 / 类别 / 失败类别筛选、指标分母、失败细节、可展开的 Trace Timeline 和 A/B 配对比较；数据从当前 SQLite 读取。`make demo` 最后运行的是 `no_tool_retries`，因此最新 run 不是完整 candidate；演示满分候选时应按导出中的 ID 选择相应 run。

在终端 B 的项目根目录执行以下命令；此处再次读取变量，使两个终端无需共享 shell 状态：

```bash
candidate_run_id=$(.venv/bin/python -c 'import json; from pathlib import Path; print(json.loads(Path("runtime/candidate.json").read_text())["run_id"])')
baseline_run_id=$(.venv/bin/python -c 'import json; from pathlib import Path; print(json.loads(Path("runtime/baseline.json").read_text())["run_id"])')
curl --fail-with-body -sS http://127.0.0.1:8000/health
curl --fail-with-body -sS 'http://127.0.0.1:8000/runs?limit=5'
curl --fail-with-body -sS "http://127.0.0.1:8000/metrics?run_id=$candidate_run_id"
curl --fail-with-body -sS "http://127.0.0.1:8000/runs/$baseline_run_id?category=retrieval&difficulty=hard&failures_only=true"
curl --fail-with-body -sS "http://127.0.0.1:8000/compare?baseline=$baseline_run_id&candidate=$candidate_run_id"
```

人工归因示例：从本次 baseline 的第一个失败结果读取 case ID / repeat，示范将其标记为 `unclassified` 待人工复核。实际审核时应先读失败细节与 Trace，再选择适当的 failure 枚举。

```bash
annotation_body=$(.venv/bin/python - <<'PY'
import json
from pathlib import Path
record = json.loads(Path("runtime/baseline.json").read_text())
failed = next(result for result in record["results"] if result["success"] is False)
print(json.dumps({"case_id": failed["case_id"], "repeat": failed["repeat"], "failure": "unclassified"}))
PY
)
curl --fail-with-body -sS \
  -H 'Content-Type: application/json' \
  --data "$annotation_body" \
  "http://127.0.0.1:8000/runs/$baseline_run_id/annotations"
curl --fail-with-body -sS "http://127.0.0.1:8000/runs/$baseline_run_id/annotations"
```

人工标注是独立的 SQLite annotation 层，不覆盖自动分类，也不重写原始分数。服务面向本地使用，默认只监听 loopback；`Ctrl+C` 停止终端 A 的服务，结果仍保留在数据库中。

## 6. Docker Compose

先停止本地 8000 端口上的服务，再执行以下步骤。容器以 UID 10001 运行，持久化数据保存在 Compose 的 `evaluation-data` named volume。先生成数据，再启动 Dashboard，可直接看到真实结果：

```bash
docker compose build
docker compose run --rm --no-deps evaluation \
  .venv/bin/python scripts/regression.py
docker compose run --rm --no-deps evaluation \
  .venv/bin/python scripts/demo.py --reports-dir runtime/reports
docker compose up -d --wait --wait-timeout 60
docker compose ps
curl --fail-with-body -sS http://127.0.0.1:8000/health
curl --fail-with-body -sS 'http://127.0.0.1:8000/runs?limit=5'
```

这里把报告写到 `runtime/reports`，它与数据库及导出文件一起保存在 named volume，而不是随一次性容器退出丢失。可从服务容器读出报告摘要和运行身份：

```bash
docker compose exec evaluation id
docker compose exec evaluation .venv/bin/python -c 'import json; from pathlib import Path; value=json.loads(Path("runtime/reports/results.json").read_text()); print(value["absolute_success_delta"], value["bootstrap_ci_95"])'
docker compose logs --tail 30 evaluation
docker compose down
```

`docker compose down` 保留 named volume，重新启动后记录仍可读取；上述命令不删除 volume。镜像构建、容器离线 regression / demo、非 root 运行身份以及 HTTP health / Dashboard 均已在开发环境实际验证。Compose 的端口映射仅绑定宿主机 `127.0.0.1:8000`。

## 7. 外部 Agent / 模型接入与公开前审查

HTTP Workspace / Research 和 OpenAI-compatible 的环境变量、能力范围、请求响应结构、超时、Trace 导入与缺失凭证处理见 [ADAPTER_CONTRACT.md](ADAPTER_CONTRACT.md)；配置变量总表见 [`.env.example`](../.env.example)。CLI 不会自动读取 `.env`，需要显式向进程导出环境变量。纯文本模型适配器没有工具执行循环，不能据其文本答案声称工具选择正确；目前外部契约通过 MockTransport 测试，未宣称真实在线服务或付费模型已接入。

重新生成合成数据及执行发布前审查：

```bash
.venv/bin/python scripts/generate_dataset.py --seed 42
git diff --exit-code -- datasets/
.venv/bin/python scripts/privacy_check.py
```

生成器的来源、MIT 授权、分布与能力边界见 [datasets/README.md](../datasets/README.md)。隐私脚本检查暂存区 blob 与未忽略的工作树文件；它是启发式检查，公开前仍应审阅实际准备提交的文件。`runtime/`、数据库和真实 `.env` 不进入公开仓库。
