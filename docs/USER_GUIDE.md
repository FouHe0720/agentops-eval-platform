# AgentOps Evaluation Platform 使用与实现手册

本手册覆盖本地安装、数据集、CLI、工作台、HTTP API、外部适配器、指标与源码。
所有命令在仓库根目录执行，使用 Bash。默认示例只执行确定性规则和内存工具，不调用模型。

## 1. 功能与端口

| 能力 | 内容 |
| --- | --- |
| 任务执行 | 并发、按任务总超时、Runner 重试、重复评测、能力跳过 |
| 数据契约 | EvalCase、AgentResponse、EvalResult、RunRecord 与层级 Trace |
| 评分 | 精确答案、字符串断言、原文引用、严格工具调用顺序 |
| 分析 | 明确分母的指标、失败归因、按案例聚类的配对比较与消融 |
| 持久化 | SQLite WAL、JSON、逐 span JSONL，统一隐私边界 |
| 工作台 | 运行选择、筛选、工具 Trace、版本对比、独立人工归因 |
| 业务示例 | 校园历史资料引用与拒答规则，独立于合成任务 |
| 外部接口 | 显式配置的 HTTP Agent 或 OpenAI-compatible 文本推理 |

多项目共存时推荐：Gateway 8101、Structured 8102、RAG 8103、AgentOps 8105、Research 8106。
Workspace 使用 stdio。端口只是运行约定，不表示这些仓库已经自动连接。
本项目 CLI、Makefile 和 Compose 的原始默认端口仍为 8000；下文显式使用 8105。

## 2. 安装与第一次运行

需要 Python 3.11+、uv 和 make。安装会下载依赖；离线基线运行不需要 GPU、模型或密钥。

```bash
git clone https://github.com/FouHe0720/agentops-eval-platform.git
cd agentops-eval-platform
make install
mkdir -p runtime/guide
.venv/bin/eval run --dataset datasets/regression.json --agent candidate_v2 \
  --db runtime/guide/evaluations.db --output runtime/guide/candidate.json
.venv/bin/eval serve --db runtime/guide/evaluations.db --port 8105
```

浏览器打开 `http://127.0.0.1:8105`，另一个终端检查：

```bash
curl -sS http://127.0.0.1:8105/health
curl -sS 'http://127.0.0.1:8105/runs?limit=10'
```

serve 前没有运行数据时，工作台显示空状态；health 成功不代表数据库已有案例。
前台服务用 Ctrl+C 退出。切换下面的数据库或启动容器前，先停止占用 8105 的旧服务。再次运行 eval run 会追加新的 run_id，不覆盖数据库历史。
同名 JSON/JSONL 导出会被替换，所以独立实验应使用不同 output 路径。

`make install` 等价于 `uv sync --frozen --extra dev`，依赖安装在 `.venv`。
也可用 `python3 -m venv .venv` 后安装 `.[dev]`，但后者按版本范围重新解析；复现优先用锁文件。
CLI 不自动读取 `.env`；离线使用无需复制或 source 它。

## 3. 三条常用运行路径

### 3.1 合成基准与受控消融

```bash
.venv/bin/python scripts/demo.py \
  --runtime-dir runtime/guide/full --reports-dir runtime/guide/reports
.venv/bin/eval serve --db runtime/guide/full/demo.db --port 8105
```

默认 100 cases × 3 repeats × 5 个配置：baseline_v1、candidate_v2，以及关闭 planner、基础工具描述、禁用工具内部重试。
输出目录包含五份运行、Trace、比较、消融摘要和 run_manifest。使用上面的独立目录不会改写仓库 reports。

`make demo` 使用默认 runtime/ 和 reports/，会更新已存在的同名公开摘要；需要保留它们时使用上述参数。
`scripts/demo.py` 还支持 --dataset、--runtime-dir、--reports-dir、--repeats；重复次数为 1–10，默认 3。
`make serve` 固定读取 runtime/demo.db，默认 8000；需要 8105 时使用显式 eval serve。

### 3.2 校园历史资料

```bash
.venv/bin/eval run --dataset datasets/campus.json --agent campus_evidence \
  --db runtime/guide/campus.db --output runtime/guide/campus.json --sensitive-trace
.venv/bin/eval serve --db runtime/guide/campus.db --port 8105
```

--sensitive-trace 在这条命令中用于保留已审查的问题与摘录，便于展开核对；它不适合直接沿用到私人数据。
校园集为 17 个案例、8 份历史来源：14 条原文引用，3 条未收录事项/校区/版本不符的拒答。
词法基线最多读取两份来源，返回原文，不生成政策解释。17/17 是规则检查，不是模型准确率。

`make campus-demo` 写入 runtime/campus.db，并更新 reports/campus-regression.json 和 .md。
`make campus-serve` 使用该数据库、默认 8000；前面的 CLI 方式适合独立目录和 8105。

### 3.3 快速回归

```bash
make regression
.venv/bin/python scripts/regression.py --dataset datasets/regression.json
```

回归脚本执行 candidate_v2，要求每条 status=completed、答案和工具序列均通过，失败时退出码为 1。
CLI eval run 本身完成一轮保存不等于所有案例成功，应检查 metrics/results；输入配置错误通常退出码为 2。

## 4. 数据集与自建案例

数据文件是 EvalCase 的 JSON 数组，文件上限 10,000,000 字节，包含 1–10,000 条不重复 case_id。

| 自带文件 | 用途 |
| --- | --- |
| datasets/smoke.json | 1 条最小检查 |
| datasets/regression.json | 合成全量固定选出的 10 条回归，不能当独立测试集 |
| datasets/synthetic.json | 100 条：算术 30、检索 30、恢复 20、长数组/异常输入 20 |
| datasets/campus.json | 独立的历史资料引用与拒答检查 |

最小自建数据：

```bash
cat > runtime/guide/custom.json <<'JSON'
[
  {
    "case_id":"sum-2-3",
    "task":"计算 2 与 3 的和，只返回结果。",
    "category":"arithmetic",
    "expected_answer":"5",
    "expected_tool_sequence":["calculator"],
    "fixtures":{"operation":"add","a":2,"b":3}
  }
]
JSON
.venv/bin/eval run --dataset runtime/guide/custom.json --agent candidate_v2 \
  --db runtime/guide/custom.db --output runtime/guide/custom-run.json --sensitive-trace
```

| EvalCase 字段 | 默认、约束与含义 |
| --- | --- |
| case_id | 必填；1–80 位字母、数字、下划线、连字符，整个数据集唯一 |
| task | 必填；1–20,000 字符 |
| category | arithmetic / retrieval / recovery / long_input |
| difficulty | easy，或 medium / hard |
| expected_answer | null；参考文本最多 20,000 字符，评分时两端 strip 后精确比较 |
| expected_tool_sequence | null；工具名有序数组，保留失败尝试和重复调用 |
| assertions | []；最多 100 条，每条 kind/value，value 最多 10,000 字符 |
| fixtures | {}；Adapter 可见的业务输入，不能塞入评分答案标记 |
| timeout | 2 秒；大于 0、不超过 120，覆盖案例所有 Runner 尝试 |
| tags | []；数据管理标签，不代表模型推断的难度 |

未知字段和非有限数值会被拒绝。没有答案参考且没有断言时 success 为 null，不进入任务成功率分母。
断言 contains/not_contains/equals 直接比较字符串；多条断言和 expected_answer 同时存在时全部满足才成功。
source_quote 用 value 指定 fixtures.documents 中的已知文档 ID，要求返回 `[ID]` 及其完整非空 text。
它不评价自由改写，也不识别附加的错误声明。校园案例另要求特定段落标记出现。

各确定性任务的 fixtures 不是任意格式：

| 类别 | candidate_v2 读取的输入 |
| --- | --- |
| arithmetic | operation=add/subtract/multiply，a、b |
| retrieval | files 内存字典、query 子串，及 ambiguous/multi_step 标记 |
| recovery | payload、failures_before_success；工具先失败指定次数再成功 |
| long_input | values 数组；invalid=true 走显式验证分支 |

内置 read_file 读取的是 fixture 字典，不读取宿主机文件。长数组上限由示例数据选择，不能据此宣称长上下文模型能力。

### 数据重建和校园来源

```bash
.venv/bin/python scripts/generate_dataset.py --seed 42 --output-dir runtime/guide/generated
.venv/bin/python scripts/build_campus_dataset.py --output runtime/guide/campus-rebuilt.json
```

合成生成器只输出 synthetic.json 与 regression.json。固定模板和 seed 可逐字节重建。
校园生成器从 datasets/campus/sources/manifest.json 和 corpus/ 校验摘录 SHA-256 与段落一致性，再输出数据。
manifest 保留原 DOCX 相对路径、原文件指纹、摘录指纹、年份、明确年级和原始 w:p 一基序号。
2024 版计算机/人工智能培养方案的 cohort 为 null，不能推断成 2024 级；年份和学生年级不是同一字段。
第三方原文不适用代码 MIT 许可。资料是 2023/2024 年历史摘录，不是当前政策。

## 5. eval run 全部参数

```bash
.venv/bin/eval run --help
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| --dataset | 必填 | 原始 JSON 数据路径 |
| --agent | baseline | baseline、baseline_v1、candidate_v2、campus_evidence、workspace、research、openai |
| --output | runtime/run.json | 导出 JSON，旁边自动生成同 stem 的 .jsonl |
| --db | runtime/evaluations.db | SQLite 路径，自动创建父目录及表 |
| --limit | 不限制 | 正整数，按 seed 抽取至多 N 个案例 |
| --category | 不筛选 | 四个 category 之一 |
| --difficulty | 不筛选 | easy / medium / hard |
| --case-id | 不筛选 | 可重复传入多个 ID，必须存在于当前筛选集合 |
| --seed | 42 | 整数；选择子集及比较/重采样配置 |
| --repeats | 1 | 1–10；每案例重复次数 |
| --concurrency | 4 | 1–32；同时执行案例数量 |
| --max-retries | 0 | 0–3；Runner 重新进入整个 Adapter 的额外次数 |
| --tool-retries | 1 | 0–3；供确定性候选/远端协议使用的内部工具重试设置 |
| --no-planner | 不开启此开关 | 传入后 planner=false，默认 true |
| --tool-descriptions | improved | basic / improved，改变确定性歧义工具选择策略 |
| --sensitive-trace | false | 显式保留更多正文，仍进行常见凭证模式脱敏 |

baseline 是 candidate_v2 的别名；比较历史策略时使用明确的 baseline_v1。
筛选顺序是 category/difficulty → case-id → limit；空选择被拒绝。limit 使用固定 seed 抽样，再按原数据顺序执行。

```bash
.venv/bin/eval run --dataset datasets/campus.json --agent campus_evidence \
  --category retrieval --difficulty hard --limit 2 --seed 42 --repeats 2 \
  --db runtime/guide/subset.db --output runtime/guide/subset.json
.venv/bin/eval run --dataset datasets/regression.json --agent candidate_v2 \
  --case-id arithmetic-001 --case-id arithmetic-003 \
  --db runtime/guide/selected.db --output runtime/guide/selected.json
```

## 6. compare、消融与 inspect

先生成两个完全相同任务/配置的运行：

```bash
.venv/bin/eval run --dataset datasets/regression.json --agent baseline_v1 \
  --seed 42 --repeats 2 --output runtime/guide/baseline.json --db runtime/guide/paired.db
.venv/bin/eval run --dataset datasets/regression.json --agent candidate_v2 \
  --seed 42 --repeats 2 --output runtime/guide/candidate.json --db runtime/guide/paired.db
.venv/bin/eval compare --baseline runtime/guide/baseline.json \
  --candidate runtime/guide/candidate.json --output runtime/guide/paired.json
```

compare 参数只有 --baseline、--candidate、--output 和 --allow-ablation。
前两项必填；output 默认 reports/results.json，会同时写同 stem 的 Markdown，建议显式写独立目录。
读取运行 JSON 上限为 50,000,000 字节；不读取 SQLite 路径作为比较输入。

```bash
.venv/bin/eval run --dataset datasets/regression.json --agent candidate_v2 \
  --seed 42 --repeats 2 --no-planner \
  --output runtime/guide/no-planner.json --db runtime/guide/paired.db
.venv/bin/eval compare --baseline runtime/guide/candidate.json \
  --candidate runtime/guide/no-planner.json --allow-ablation --output runtime/guide/ablation.json
```

允许消融只放开 planner、tool_descriptions、tool_retries。这个开关不允许换数据、seed、并发、repeats 或隐私配置。
三种开关改变确定性示例的实际执行路径，不是实测 LLM Prompt 改进。

```bash
.venv/bin/eval inspect --db runtime/guide/paired.db
EVAL_RUN_ID=$(.venv/bin/python -c 'import json; print(json.load(open("runtime/guide/candidate.json"))["run_id"])')
.venv/bin/eval inspect "$EVAL_RUN_ID" --db runtime/guide/paired.db
```

inspect 只接收可选位置参数 run_id 和 --db。无 ID 时列出运行，指定 ID 时输出该运行指标。
它不恢复已脱敏的任务或重新评分。

## 7. 工作台与所有 HTTP 接口

```bash
.venv/bin/eval serve --db runtime/guide/paired.db --host 127.0.0.1 --port 8105
```

serve 参数：--db 默认 runtime/evaluations.db，--host 默认 127.0.0.1，--port 默认 8000、允许 1–65535。
API 和工作台同源，不包含账号系统。GET 数据来自 SQLite，不触发新评测。

| 方法与路径 | 参数与返回 |
| --- | --- |
| GET / | 本地 Dashboard HTML |
| GET /health | status=ok、schema_version=1 |
| GET /runs | limit=100，允许 1–1000；返回 runs 摘要 |
| GET /runs/{run_id} | 运行、过滤后的 results、result_count 与重新聚合的 metrics |
| GET /metrics | 可选 run_id；省略取最新运行，无记录时 404 |
| GET /compare | 必需 baseline/candidate 运行 ID，可选 allow_ablation=false |
| GET /runs/{run_id}/annotations | 返回该运行独立标注数组 |
| POST /runs/{run_id}/annotations | 保存或替换指定 case/repeat 的 failure 标注 |
| GET /openapi.json | OpenAPI JSON；/docs 和 /redoc 未启用 |

```bash
curl -sS 'http://127.0.0.1:8105/runs?limit=10'
curl -sS "http://127.0.0.1:8105/runs/${EVAL_RUN_ID}"
curl -sS "http://127.0.0.1:8105/runs/${EVAL_RUN_ID}?category=arithmetic&difficulty=easy&failures_only=false"
curl -sS "http://127.0.0.1:8105/metrics?run_id=${EVAL_RUN_ID}"
curl -sS http://127.0.0.1:8105/openapi.json
```

/runs/{id} 的其他筛选参数为 failure 和 failures_only；difficulty/category 与 CLI 枚举相同。
failures_only 同时包含 success=false、有 failure 信号、error/timeout 状态的结果；成功答案也可能有工具协议错误。
result_count.total 是整轮结果数，filtered 是当前数量；顶部指标只统计过滤后的 results。

```bash
EVAL_BASELINE_ID=$(.venv/bin/python -c 'import json; print(json.load(open("runtime/guide/baseline.json"))["run_id"])')
curl -sS "http://127.0.0.1:8105/compare?baseline=${EVAL_BASELINE_ID}&candidate=${EVAL_RUN_ID}"
curl -sS "http://127.0.0.1:8105/runs/${EVAL_RUN_ID}/annotations"
curl -sS -X POST "http://127.0.0.1:8105/runs/${EVAL_RUN_ID}/annotations" \
  -H 'Content-Type: application/json' \
  --data-binary '{"case_id":"arithmetic-001","repeat":0,"failure":"unclassified"}'
```

最后一条仅演示标注格式，不代表该题实际失败；人工标注不会改写规则指标或配对结果。
请求严格只接受 case_id、repeat（0–9）、failure；结果必须存在，未知字段或错误类型返回 422。
failure 枚举：planning_error、tool_selection_error、argument_error、tool_execution_error、reasoning_error、unsupported_claim、timeout、unclassified。
同一 run/case/repeat 再次保存替换原标注，没有标注者身份和标注版本历史。

POST 必须为 application/json；有 Origin 时必须与当前服务的 scheme/host/port 一致，curl 不带 Origin 可以调用。
错误结构为 `{"error":{"code":"...","message":"..."}}`：400 comparison_invalid、403 origin_forbidden、404 not_found、405 method_not_allowed、415 unsupported_media_type、422 invalid_request、500 internal_error。
页面带 CSP、nosniff、frame deny 和 no-store；这些头不代替身份认证。

## 8. 指标、Trace 与公平比较

| 指标 | 定义 |
| --- | --- |
| total_results/evaluated/skipped | 总结果数、非 skipped 数、不支持类别数 |
| task_success_rate | 有答案或断言参考的已执行结果；返回 value/numerator/denominator |
| tool_selection_accuracy | 有预期工具序列的已执行结果，严格有序比较 |
| tool_execution_success | 状态为 ok 的 tool_call / 全部 tool_call |
| average_steps | 全部工具调用数 / 已执行结果数，含失败和重试 |
| latency_ms | 非 skipped 案例的 p50/p95/mean，线性插值分位数 |
| timeout_rate | timeout 结果数 / 已执行结果数 |
| stability | 至少两次可评分的案例中，success 布尔值全部一致的比例 |
| token_usage/cost | 仅汇总有观测的 model_call，同时保留观测覆盖数 |
| failure_counts | 规则 failure 分布，可以与成功答案同时出现 |
| runner_retries/tool_retry_events | 整个 Adapter 重试与工具内部重试分别计数 |

没有适用分母为 null，不补零。没有 model_call 时 token/cost 为 null，而不是免费推理的实测账单。
延迟从取得 Semaphore 后计时，不包含等待并发名额；错误与超时也进入延迟分母。
反复失败也可以稳定；稳定率不能替代任务成功率。

TraceEvent 包含 run_id、span_id、parent_span_id、event_type、timestamp、duration_ms、tool_name、status、token_usage、cost 和 metadata。
event_type 为 agent/model_call/tool_call/tool_result/final/retry；status 为 ok/error/timeout。
父节点必须先出现，拒绝重复 span ID 和混合 run。父 span 耗时包含子调用，不能全部求和作为端到端时间。
ContextVar 保存异步父节点栈；取消时先标记失败，再由 Runner 将自身 deadline 对应的 span 转为 timeout。

比较要求原始 dataset_digest、序列化 cases、case/repeat 覆盖和配置相同。
算法按 case/repeat 配对，排除 skipped/无评分对，再对同一个 case 的差值取平均，最后案例等权平均。
bootstrap 对案例聚类重采样 2,000 次，保留 repeats 的相关性；少于 10 个有效案例不提供区间。
重复运行同一个问题不增加独立问题数。CLI/API 没有 bootstrap_samples 参数，Python 函数可设置 100–10,000。

## 9. 外部 Adapter 与全部环境变量

仅在明确选择 workspace/research/openai 时使用以下配置；CLI 不读取 `.env`。
构造函数 endpoint/base_url/model/api_key 参数优先于对应环境变量，timeout 默认 30 秒、实际取它与 case timeout 的较小值。

| 环境变量 | 适用对象 | 默认 |
| --- | --- | --- |
| WORKSPACE_AGENT_ENDPOINT | workspace 的完整 HTTP POST 地址 | 未配置 |
| WORKSPACE_AGENT_API_KEY | workspace 可选 Bearer Key | 无 |
| RESEARCH_AGENT_ENDPOINT | research 的完整 HTTP POST 地址 | 未配置 |
| RESEARCH_AGENT_API_KEY | research 可选 Bearer Key | 无 |
| OPENAI_BASE_URL | openai 的兼容 API 根地址，通常含 /v1 | 未配置 |
| OPENAI_MODEL | openai 的实际模型名称 | 未配置 |
| OPENAI_API_KEY | openai 可选 Bearer Key | 无 |

没有 EVAL_PORT、EVAL_DB 或 .env 自动加载选项；端口/数据库通过 serve/run 参数指定。
HTTP URL 只接受 http/https，不接受账号密码或 fragment；OpenAI base URL 还不接受 query。
客户端不跟随重定向、不读取代理环境变量，响应体最多 1 MiB。

| CLI agent | 记录名与能力 |
| --- | --- |
| baseline / candidate_v2 | candidate_v2；四类合成任务 |
| baseline_v1 | baseline_v1；四类合成任务，基础选择且无内部恢复 |
| campus_evidence | campus_evidence；retrieval |
| workspace | workspace_http；arithmetic/retrieval/long_input |
| research | research_http；retrieval |
| openai | openai_compatible；arithmetic/long_input，没有工具执行循环 |

HTTP workspace/research 向指定 endpoint POST 下列结构；不是自动发现兄弟仓库接口：

```json
{
  "task":"计算输入值之和",
  "category":"arithmetic",
  "fixtures":{"a":2,"b":3},
  "seed":42,
  "config":{"planner":true,"tool_descriptions":"improved","tool_retries":1}
}
```

返回只允许 answer 和可选 trace：

```json
{
  "answer":"5",
  "trace":[
    {"span_id":"root","event_type":"agent"},
    {"span_id":"call","parent_span_id":"root","event_type":"tool_call","tool_name":"calculator","duration_ms":1.2}
  ]
}
```

answer 最多 20,000 字符，trace 最多 1,000 项；非空 trace 只能一个根，父节点先出现。
远端 span 允许 span_id、parent_span_id、run_id、event_type、tool_name、status、duration_ms、token_usage、cost。
ID/工具名限 1–80 位字母数字及 `_ . : -`；run_id 可省略，出现时最多 128 字符且一致。
工具调用/结果必须有 tool_name；duration/cost 必须有限非负，token_usage 为非负整数或 null。
未知字段包括任意 metadata 会使响应被拒绝。完整校验后再重映射 ID，远端根接到本地 Adapter span。
这只是远端报告的 Trace，不是平台独立证实的操作；无 Trace 只能评分答案，不能证明执行过工具。

OpenAI Adapter 向 base URL 追加 /chat/completions，仅发 model 和 system/user messages。
user message 包含 task/category/fixtures，不发送预期答案、工具定义或兼容性未知的 seed。
要求 choices[0].message.content 为字符串，非空 tool_calls 被拒绝。
usage.total_tokens 优先，否则仅在 prompt/completion 都完整时求和；缺失保留 null，cost 始终 null。

已准备本地兼容服务时才执行：

```bash
export OPENAI_BASE_URL='http://127.0.0.1:11434/v1'
export OPENAI_MODEL='qwen2.5:0.5b'
.venv/bin/eval run --dataset datasets/regression.json --agent openai \
  --category arithmetic --limit 3 --output runtime/guide/llm.json --db runtime/guide/llm.db
```

没有工具循环，算术答案即使正确，严格工具序列仍可能不符；不据此声称模型执行了 calculator。
HTTP Agent 使用 export WORKSPACE_AGENT_ENDPOINT/RESEARCH_AGENT_ENDPOINT 后执行对应 --agent；endpoint 必须真能实现上述协议。
Workspace 兄弟项目默认 stdio，不能直接塞进 HTTP endpoint；需要自行实现桥接。Research 的 8106 也不自动等于本协议地址。
Gateway 8101 使用自定义 /v1/chat，不能直接当作这里的 OpenAI /chat/completions；当前没有内置转换层。
未配置远端时记录安全错误；429/5xx/传输错误可由 Runner 重试，但默认 max-retries=0，写操作需远端保证幂等。

ResearchOS 已有兼容入口 `http://127.0.0.1:8106/api/agentops/research`，使用它自己的离线材料。
该入口要求 retrieval、空 fixtures、planner=true、tool_descriptions=improved、tool_retries 为 0 或 1，task 为 3–500 字符。
因此不能将 datasets/campus.json 中的 documents fixtures 直接发给它，也不能对它运行关闭 planner 的消融。
在对应服务已经启动后，可创建无答案参考的协议观测案例：

```bash
cat > runtime/guide/research.json <<'JSON'
[{"case_id":"research-memory","task":"研究Agent Memory中的短期与长期记忆","category":"retrieval","fixtures":{},"timeout":60}]
JSON
export RESEARCH_AGENT_ENDPOINT='http://127.0.0.1:8106/api/agentops/research'
.venv/bin/eval run --dataset runtime/guide/research.json --agent research \
  --tool-retries 0 --max-retries 0 --db runtime/guide/research.db --output runtime/guide/research-run.json
```

该例只观察协议和 Trace；没有参考答案或断言，任务成功率为 null，不构成质量测试。
ResearchOS 配置 API Key 时，在调用平台的 shell 设置 RESEARCH_AGENT_API_KEY。此连接的配置限制来自兼容入口本身，不是所有 Research HTTP 服务的通用限制。


## 10. 持久化、隐私与生命周期

SQLite 使用 WAL 和短连接，runs 保存完整已校验 RunRecord JSON，annotations 独立存储。
run 全部完成后一次落库，不做流式 checkpoint；进程强杀可能丢失进行中结果。
JSON 是运行记录，JSONL 每行一个 span，额外带 case_id/repeat 便于恢复调用归属。
RunRecord 要求案例唯一、完整 case × repeat 覆盖、结果/Trace 属于同一 run。

| 输出模型 | 字段 |
| --- | --- |
| AgentResponse | answer（最多 20,000 字符）、error_kind（可选 failure 枚举） |
| EvalResult 身份 | run_id、case_id、category、difficulty、repeat（非负） |
| EvalResult 状态 | status=completed/error/timeout/skipped；answer 可空；success、tool_selection_correct 可为 null；failure 可空 |
| EvalResult 观测 | latency_ms 非负、attempts 非负、trace.events 数组 |
| RunRecord | schema_version=1、run_id、agent、dataset_digest、cases、config、created_at、results |
| TraceEvent 观测 | timestamp（生成时为 UTC）、非负 duration_ms、可选 token_usage/cost、metadata 字典 |

这些字段构成 JSON 导出与 `/runs/{id}` 的主体；HTTP 运行详情另附过滤计数和 metrics，默认隐私处理会改变其中的正文。

默认落库/导出移除 task 正文、fixtures、expected_answer、assertions、tags 与最终 answer；Trace metadata 仅保留受限安全字段。
原始 dataset_digest 在脱敏前计算，用于比较；脱敏记录不能独立重建输入或重新评分。
--sensitive-trace 显式保留正文，仍通过常见 Header、密钥赋值、Bearer、私有路径等模式脱敏。
正则不能识别所有秘密；内存仍有执行所需原文，不应绕开公共持久化边界另写日志。

需要一致备份时使用 SQLite backup API，不只复制运行中的主 .db 文件而忽略 WAL：

```bash
.venv/bin/python - <<'PY'
import sqlite3
with sqlite3.connect('runtime/guide/paired.db') as source:
    with sqlite3.connect('runtime/guide/paired-backup.db') as target:
        source.backup(target)
PY
```

## 11. Docker、测试与排错

```bash
docker build -t agentops-eval-platform:local .
docker volume create agentops-guide-data
docker run --rm --network none -v agentops-guide-data:/app/runtime \
  agentops-eval-platform:local .venv/bin/python scripts/demo.py \
  --runtime-dir runtime --reports-dir runtime/reports
docker run --rm --name agentops-guide -p 127.0.0.1:8105:8000 \
  -v agentops-guide-data:/app/runtime agentops-eval-platform:local
```

镜像默认以非 root UID 10001 读取 runtime/demo.db 并监听容器 8000；上面宿主映射为 8105。
现有 Compose 固定 `127.0.0.1:8000:8000`，不会读取 PORT 改端口；多项目共存使用上面的 docker run 或自己的 Compose override。
Compose 的 named volume 保留数据库，down 不删除它。首次构建需拉取镜像和依赖；禁网只适用于安装完成后的离线任务。
运行远端 Adapter 时要另行传入已配置环境变量，不要把 localhost 误认为宿主机。

```bash
make lint test regression
.venv/bin/python scripts/privacy_check.py
uv lock --check --offline
uv build
```

测试覆盖数据契约、Runner、取消/重试、评分、比较、隐私、SQLite/API 和离线报告。
浏览器是可选人工检查工具，不是核心或 CI 前置。不要把协议模拟测试记成真实 LLM 验证。

| 现象 | 排查 |
| --- | --- |
| eval 命令不存在 | 是否 make install；使用 .venv/bin/eval 或 .venv/bin/python -m agentops_eval |
| 数据集加载失败 | 顶层必须数组、ID 唯一、无未知字段，文件和字段长度不超限 |
| 筛选后报错 | 当前 category/difficulty/case-id 组合可能为空 |
| 全部 skipped | Adapter capabilities 与任务类别是否匹配，openai 不接受 retrieval |
| CLI 退出成功但任务失败 | 检查 results.status、failure 和 metrics，不只看进程退出码 |
| compare 拒绝 | dataset digest、case/repeat、seed、配置或隐私设置不同 |
| 区间为 null | 少于 10 个共同可评分的 case clusters |
| 地址已占用 | 停止当前服务，或显式更换 serve --port 并同步客户端地址 |
| 工作台无数据 | serve 的 --db 与 run 的 --db 是否相同 |
| 看不到问题/答案 | 默认内容最小化；只对已审查数据显式选择 --sensitive-trace |
| 标注 415/403 | Content-Type 与 Origin 是否符合本服务地址 |
| 远端 invalid_response | 核对 answer、字段白名单、单根 Trace、类型与大小限制 |
| 远端超时 | case timeout 默认仅 2 秒，可能先于 Adapter 默认 30 秒；在数据中合理设置 |
| 同步 Adapter 卡住 | asyncio 无法硬终止阻塞代码，需独立执行进程 |

## 12. 目录、执行链路与技术选择

```text
src/agentops_eval/
├── cli.py / __main__.py     run、compare、inspect、serve 与命令入口
├── domain/models.py        案例、配置、Trace、结果与运行约束
├── adapters/
│   ├── base.py             AgentAdapter 协议和安全 AdapterError
│   ├── deterministic.py    合成工具执行及版本差异
│   ├── campus.py           范围过滤、中文二元字符/英文词检索
│   ├── http.py             外部 Agent 协议与远端 Trace 校验
│   └── openai_compatible.py 纯文本模型请求与 usage 解析
├── services/
│   ├── datasets.py         JSON 加载、唯一性与完整数据 digest
│   ├── runner.py           并发、参考隔离、预算、重试与评分组装
│   ├── tracing.py          ContextVar span 父子关系与取消状态
│   ├── scoring.py          答案/工具/引用检查和规则归因
│   ├── metrics.py          分母、分位数、稳定性与观测覆盖
│   └── comparison.py       严格配对与 case-cluster bootstrap
├── infra/privacy.py        统一正文最小化与模式脱敏
├── infra/storage.py        SQLite 事务、标注、JSON/JSONL 导出
└── api/                    同源 HTTP API 与原生 HTML/JS 工作台
datasets/                   合成数据、独立校园数据与来源清单
scripts/                    数据生成、回归、Demo、隐私检查
reports/                    已审查运行摘要，不含默认私有数据库
runtime/                    本地数据库、导出和临时报告，默认忽略
tests/                      单元、协议、API 与集成检查
docs/                       使用、架构、契约和验证说明
```

一次运行：加载/筛选数据 → 深拷贝与能力筛选 → 取得并发名额 → 总预算内执行 Adapter → 校验响应/Trace → 原始参考评分 → 聚合指标 → 统一隐私处理 → SQLite 和导出。
Adapter 输入不带评分参考，Runner 的原始副本仍供 scorer 使用。工具内部恢复与整个 Adapter 重试分开记录。
校园检索先按校区/年份范围过滤，再按中文二元字符和英文词覆盖率排序；阈值和最多两份来源是可检查的确定性规则，不是语义模型。
SQLite、原生 HTML 和标准 JSON 让本地运行无需 Node、Redis 或队列服务；代价是没有分布式调度、租户隔离、运行中恢复和硬资源沙箱。
当前没有通用事实 Judge、自动线上采样、生产告警或标准化账单校验。外部服务的能力和 Trace 必须另外验证，不能由适配器名称推断。
