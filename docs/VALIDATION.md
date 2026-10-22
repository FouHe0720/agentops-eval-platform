# 真实验证记录

日期：2026-10-22（Asia/Shanghai）。所有数字来自实际执行；本项目默认使用原创合成 fixtures，不是生产数据或真实模型成绩。

## 阶段验收

| 阶段 | 验收时实际结果 | Commit |
|---|---|---|
| V0 | 2 pytest passed；ruff；离线 CLI JSON 执行成功 | `140802d` |
| V1 | 116 pytest passed，无 warnings；ruff；100×3 配对评测成功 | `cb0c5c4` |
| V2 | 212 pytest passed，无 warnings；ruff；10-case regression；Docker 与浏览器验证 | `63da1d8` |
| V3 | 213 tests；正式报告、干净环境 Quick Start、文档/截图/隐私审查、发布后验 | 最终提交见 Git 历史与下方记录 |

## 环境与工程验证

- WSL Linux；系统 Python 3.12.3，uv 项目环境为 CPython 3.12.15；Git 2.43.0、uv 0.12.24、Docker 29.8.2。
- `make install`：锁定安装成功，项目级 `.venv`；未 sudo、未修改系统 Python/代理。
- `make lint test regression`：213 tests passed，无 warnings；Ruff 静态与格式检查通过；10-case 答案与工具序列均 10/10。
- `python scripts/generate_dataset.py` + `git diff --exit-code -- datasets/`：100/10 条数据逐字节再生，无差异。
- `uv lock --check --offline`：通过。
- `uv build`：sdist/wheel 构建成功，已检查 wheel 包含 `agentops_eval/api/dashboard.html`。
- 临时干净 checkout（不含原 `.venv`、数据库、API key）重做 `make install`、`make demo`、`make lint test regression`；结果见本次验收，无需系统/云前置。
- `scripts/privacy_check.py`：同时检查 index blobs 与工作树；6 个临时 Git 仓库测试包含“已暂存 secret，工作树已清理/删除”的回归。另人工检查 staged 文件列表、忽略规则、域名、环境变量与机器路径。

## 合成运行与报告

正式运行的 run_id、created_at、config、完整 metrics 在 [`run_manifest.json`](../reports/run_manifest.json)。固定 seed=42、100 cases、3 repeats，每种策略 300 个结果。

| 运行策略 | 任务成功 | 相对完整 candidate 的绝对差值 |
|---|---:|---:|
| baseline_v1 | 195 / 300 | −0.35 |
| candidate_v2 | 300 / 300 | 0 |
| no_planner | 222 / 300 | −0.26 |
| basic_tool_descriptions | 240 / 300 | −0.20 |
| no_tool_retries | 255 / 300 | −0.15 |

baseline → candidate 的 paired delta=0.35，100 个 case clusters、2000 次 bootstrap、95% CI=[0.26,0.44]。无模型调用，tokens/cost=null。耗时来自真实 monotonic clock，随机器与调度变化，不用于模型性能宣传。

[`example-trace.jsonl`](../reports/example-trace.jsonl) 从本次 candidate 的实际错误恢复 Case 导出；[`comparison.md`](../reports/comparison.md) 含逐 case/repeat 变化；[`ablations.md`](../reports/ablations.md) 含实际消融。

## Dashboard 浏览器验证

临时安装 Playwright 于项目 `.venv`，使用已有 Chromium headless shell；缺失的 nspr/nss 和 CJK 字体仅下载、提取到忽略的 `runtime/browser-libs/`，未系统安装。产品和 CI 不依赖浏览器测试包。

实际浏览器验证：读取持久化 Runs、默认兼容 A/B 配对、+35 pp 和区间显示、baseline 失败筛选 105/300、Trace 展开、人工归因写入后显示、390px 移动布局。无 JavaScript 页面错误；CSP 未禁用。浏览器截图来自实际 SQLite 数据，不是生成图或 Mock UI；最终截图按 Asia/Shanghai 显示时间。

- [指标总览](assets/dashboard.png)
- [真实比较结果](assets/comparison.png)
- [层级 Trace](assets/trace.png)
- [移动布局](assets/mobile.png)

浏览器中人工标注为演示写入，与原始规则评分分开保存；不改变合成报告指标。

## Docker 验证

- `docker compose config --quiet`、`docker build --check .`：通过，无 Dockerfile 警告。
- `docker build -t agentops-eval-platform:local .`：实际成功。
- 验证镜像 ID：`sha256:0ed08360f3ddd1011fdfe9a7bf4fa675d909dac5c5c7db839e7f60735230f938`（文档后续变化会改变重建 ID，非发布镜像摘要承诺）。
- `docker run --rm --network none ... scripts/regression.py`：10/10 通过。
- `docker run --rm --network none ... scripts/demo.py --dataset datasets/regression.json --repeats 1`：5 个 run、配对与三消融报告写入成功；默认 reports 目录可写。
- 容器 `id -u`：10001；容器 `/health` 与 `/` HTTP 200，HTML/CSP 打包检查成功。临时 smoke 容器已停止并删除。
- Compose 完整验收已通过：独立项目名及 named volume，regression、默认 100×3 Demo、`up --wait` healthy、API 读取 5 个持久化 Run、容器读取 5-run manifest、`down` 保留 volume。镜像来自上面的真实 Docker build。第一次使用的 `compose run --no-build` 参数在本机不支持，移除该参数后全部重新执行通过。

## 网络与模型验证边界

- Workspace/Research/OpenAI-compatible 实现：51 个 MockTransport 协议测试通过；网络超时、HTTP 错误、redirect 拒绝、能力范围、错误 trace、token usage/null 和凭证不回显已覆盖。
- 未调用真实模型、Workspace 或 Research 服务；没有凭证/目标服务配置，不计作在线验证。可按 [连接契约](ADAPTER_CONTRACT.md) 和 [DEMO](DEMO.md) 的显式入口进行后续实测。
- GitHub 远端 CI 与 PUBLIC 发布状态必须在真实推送后追加核验；页首 README badge 是实际 workflow 状态，不是手工“passed”标签。
