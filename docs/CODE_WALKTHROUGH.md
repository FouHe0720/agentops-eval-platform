# 代码导读

平台将任务执行、Trace、评分和持久化分开。合成任务与校园历史资料使用同一个 Runner，但采用不同 Adapter 和评分参考。

| 文件 | 职责 |
| --- | --- |
| `domain/models.py` | Case、Response、Trace、Result、Run 的数据约束 |
| `services/runner.py` | 能力筛选、并发、输入隔离、总预算和执行重试 |
| `services/tracing.py` | 异步上下文中的父子 span 和取消状态 |
| `services/scoring.py` | 答案、断言、原文引用和工具序列评分 |
| `services/metrics.py` | 各指标的分母、缺失值与观测覆盖 |
| `services/comparison.py` | 配对校验及按 case 聚类的 bootstrap |
| `infra/privacy.py` | SQLite 与导出共用的正文最小化和模式脱敏 |
| `adapters/campus.py` | 根据范围与词法重合检索已审查的资料摘录 |

## 一次执行

Runner 复制数据集和配置，每次调用 Adapter 前再复制案例，并移除 expected_answer、expected_tool_sequence 和 assertions。Adapter 执行工具、产生 Trace 并返回 AgentResponse；评分器使用 Runner 保留的原始参考。

HTTP Adapter 进一步采用字段白名单，只提交 task、category、fixtures、seed 和允许的执行配置，不发送评分字段、标签或 case ID。进程内 Adapter 仍能访问案例 ID 和标签，因此这里是常见泄漏防护，不是恶意代码隔离。进程内对象也不能被视为文件系统或网络沙箱。

## Trace 与评分

Trace 用 span_id 与 parent_span_id 保留调用归属。TraceRecorder 用 ContextVar 保存异步父节点栈，避免并发工具互相成为父子节点。返回边界校验重复 span、混合 run、缺失父节点与前向父引用。父 span 已包含子调用时间，不能把所有 span 耗时相加当作端到端延迟。

任务成功和工具选择分别评分。答案正确但工具序列不符时，成功标签仍可为 true，工具选择标签为 false。工具序列包含失败尝试和重试，不在评分前删去失败工具调用。

校园 `source_quote` 断言要求已知文档编号及对应完整原文；另一条断言要求指定段落标记。缺失或畸形的来源数据返回未通过，不中断同轮任务。它检查引用输出规则，不能判断自由改写是否正确，也不自动发现引用之外的矛盾声明。

失败归因来自明确错误、Adapter 信号和评分规则。未知情况保留 unclassified。unsupported_claim 是可接受的错误类型，平台没有通用事实核验器；人工归因独立保存，不覆盖原始规则结果。

## 指标分母

| 指标 | 分母与缺失处理 |
| --- | --- |
| 任务成功率 | 已执行且有答案或断言参考的结果；无参考时为 null |
| 工具选择准确率 | 已执行且配置预期工具序列的结果 |
| 工具执行成功率 | 实际 tool_call 数量，失败和超时保留在分母中 |
| 平均工具步数 | 已执行结果数，分子含失败与重试工具调用 |
| 延迟和超时率 | 已执行结果；计时从取得并发名额后开始 |
| 重复稳定率 | 至少两次可评分结果的 case 数；全部失败也可能稳定 |
| token/费用 | 只累加有值的 model-call 观测，并保留观测覆盖数量 |

校园基线没有模型调用，所以 token 和费用为 null。14 条可回答任务没有约定唯一工具路径，只有 3 条拒答任务配置预期序列，因此工具选择分母为 3。不同指标不能共用一个分母解释。

## 比较与重复

比较要求相同的完整 dataset digest、案例/重复覆盖、seed 与运行配置。显式消融只放开 planner、tool descriptions 和 tool retries 三个参数。

比较先按 `(case_id, repeat)` 配对，再对每个案例的成功差值取平均，最后对案例等权汇总。bootstrap 重采样的单位也是案例，而不是每次执行。默认 2,000 次，少于 10 个有效案例时不提供区间。重复同一个问题不能增加独立问题数量；固定 seed 也不保证外部模型确定性。

## 执行预算与重试

Semaphore 控制同时执行的案例数量。取得名额后，案例全部尝试共用一个 asyncio 总预算。Adapter 返回与响应校验后仍检查是否超时，防止同步阻塞或吞掉取消的实现被误记为成功。

asyncio 超时是协作式取消，不能硬终止不返回的同步代码。运行不可信 Agent 需要独立进程或容器、资源限额及可终止的 worker。

Runner 重试会重新进入整个 Adapter，不等于单个工具内部重试。输入深拷贝不能撤销远端副作用。HTTP 断线时远端可能已完成操作，因此有写入副作用的服务应定义幂等键和补偿语义；平台默认远端重试次数为 0。

## 持久化与查询

Run 完成后一次写入 SQLite，进程强杀不保证保存进行中结果。SQLite、JSON 和 JSONL 共用隐私边界，默认删除任务、fixtures、答案参考、断言、标签及最终答案正文；脱敏后记录能审计已有评分，但不能独立重评分。

固定校园 Demo 仅针对仓库内已审查摘录显式保留正文，使工作台能显示问题和原文。通用 CLI 默认仍删除正文；该设置不能直接沿用到新增个人资料。

本地工作台读取 SQLite，用 textContent 渲染动态文本。人工归因存于单独 annotations 表，没有标注者身份或历史版本。默认监听 loopback，公网运行需要补齐鉴权、访问控制和审计。

来源与运行方式见 [CAMPUS_SCENARIO](CAMPUS_SCENARIO.md)，协议边界见 [ADAPTER_CONTRACT](ADAPTER_CONTRACT.md)，检查记录见 [VALIDATION](VALIDATION.md)。
