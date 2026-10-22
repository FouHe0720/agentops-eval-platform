# AgentOps Evaluation Platform 面试追问与回答

建议先按以下顺序阅读 5 个核心文件，再对照下面的 10 个问题走一次完整执行链路。

1. [`domain/models.py`](../src/agentops_eval/domain/models.py)：先理解 Case、Response、Trace、Result、Run 的数据边界。重点看未知字段、非有限数值、span 父子关系，以及 case × repeat 覆盖完整性的校验。
2. [`services/runner.py`](../src/agentops_eval/services/runner.py)：看一次评测如何串起能力筛选、并发、输入隔离、总执行预算、重试、返回校验和计分。这里决定失败是否只影响单个案例。
3. [`services/tracing.py`](../src/agentops_eval/services/tracing.py)：看上下文如何成为可查询的调用树，尤其是 `ContextVar` 如何避免并发工具调用互相污染父 span，以及取消如何转成正确状态。
4. [`services/metrics.py`](../src/agentops_eval/services/metrics.py)：逐项检查分母、空值和观测覆盖率；这是理解报表数字含义的入口。
5. [`services/comparison.py`](../src/agentops_eval/services/comparison.py)：最后检查比较是否公平，理解 `(case_id, repeat)` 配对、配置限制和以案例为单位的 bootstrap。

当前示例来自确定性 Agent 对合成 fixtures 的实际执行。HTTP 和 OpenAI-compatible 适配器的协议测试使用 `httpx.MockTransport`；这里没有声称已经调用在线 LLM，也没有把合成基准结果当作真实业务能力证明。可对照[数据集说明](../datasets/README.md)、[适配器契约](ADAPTER_CONTRACT.md)和[实际执行报告](../reports/results.json)。

## 1. Evaluation 和 Observability 分别解决什么问题？

**答：** Evaluation 把执行结果与明确标准比较，回答“在这组任务上是否完成目标、质量如何变化”。本项目用预期答案、字符串断言和预期工具序列评分，再按类别、难度和失败类型汇总。Observability 保存执行证据，回答“这次任务经历了什么、哪一步失败、时间花在哪里”。本项目通过 agent、model、tool、retry、final 等 span 记录过程。

两者在这里共享 `run_id`、`case_id` 和 repeat 身份：发现成功率下降后，可以定位具体结果并展开 Trace。一个 Agent 最终答对，也可能经历错误工具调用或多次失败重试；只看最终答案会漏掉这些成本和协议问题。反过来，Trace 中没有异常也不能证明答案正确，仍需参考标准。

项目已经实现离线评测与单机查询；尚未实现线上请求采样、告警、跨服务追踪采集或生产 SLO 管理。对应代码见 [`scoring.py`](../src/agentops_eval/services/scoring.py)、[`metrics.py`](../src/agentops_eval/services/metrics.py) 和 [`api/app.py`](../src/agentops_eval/api/app.py)。

## 2. 为什么要 Trace？日志加时间戳是否足够？

**答：** 日志擅长保存离散事实，但单凭时间先后很难恢复并发任务之间的调用归属。Trace 用 `span_id` 和 `parent_span_id` 保留因果结构；例如一次检索下同时启动两个工具调用，它们应当是兄弟节点，而不能因为执行交错就被串成父子关系。

[`TraceRecorder`](../src/agentops_eval/services/tracing.py) 用 `ContextVar` 保存每个异步上下文的父节点栈。span 开始时记录身份和时间，结束时补 duration 与状态。最终校验拒绝重复 span、混合 run、缺失父节点和前向父引用。远端 HTTP Trace 会先完整验证，再重映射 ID 后接到本地 adapter span 下。

父 span 的 duration 已包含子调用时间，不能把所有 duration 相加当作端到端延迟。远端导入 span 的 timestamp 是本地接收时间，duration 是远端报告值，当前 Dashboard 不等同于具有时钟校正的分布式追踪系统。本实现也没有接入 OpenTelemetry exporter。日志仍适合记录不需要调用树结构的诊断事件，但日志输出同样需要隐私边界。

## 3. 失败如何分解？规则归因是否能证明根因？

**答：** 当前把结果分为规划、工具选择、参数、工具执行、推理、无依据声明、超时和未分类等失败类型。它们来自明确的执行错误或规则信号，适合筛查与聚合；这不是对 Agent 思维过程的因果证明。

[`scoring.py`](../src/agentops_eval/services/scoring.py) 把任务成功和工具选择分别计算。比如参考答案是 `5`，期望调用 `calculator`，Agent 却调用 `search` 后仍回答 `5`：`success=True`，`tool_selection_correct=False`，`failure=tool_selection_error`。这保留了答案正确和工具协议违规两个事实。

归因优先保留 Runner 已确定的失败，再保留 adapter 的 `error_kind`；没有显式归因时，工具序列不匹配归为工具选择错误，答案检查不通过归为推理错误。Runner 将非法响应归为参数错误，将其他 `AdapterError` 归为工具执行错误；未知异常统一归为未分类，不保存异常原文。`unsupported_claim` 只是可接受的错误类型，目前没有独立的事实核验器自动识别它。人工复核用于补充规则盲区。

## 4. 为什么同一份报告中的指标分母不相同？

**答：** 每项指标需要不同的证据。缺少参考或观测值时应显示 `null` 和实际分母，不能把它们当作成功、失败或零成本。实现见 [`metrics.py`](../src/agentops_eval/services/metrics.py)。

| 指标 | 当前分母与缺失处理 |
| --- | --- |
| 任务成功率 | 非 skipped 且 `success` 非空的结果；没有答案或断言参考时为 `null`；有参考的 error/timeout 计为失败 |
| 工具选择准确率 | 非 skipped 且配置了预期工具序列的结果；error/timeout 也比较已经发生的完整有序调用序列 |
| 工具执行成功率 | 实际 `tool_call` span 数量；只有状态 `ok` 算成功，失败与超时调用保留在分母中 |
| 平均工具步数 | 非 skipped 结果数；分子包含失败和重试产生的实际工具调用 |
| 延迟、超时率 | 非 skipped 结果；延迟从拿到并发信号量后计时，不包含等待并发名额的排队时间 |
| 重复稳定性 | 至少有两次可评分结果的 case 数；同一 case 的成功布尔值全部一致才算稳定 |
| token / cost | 只汇总实际有值的 model-call 观测，同时报告已观测调用数与总模型调用数；未知值保留 `null` |

例如四个已执行案例中，一个成功、两个有参考但失败、一个没有答案参考，则任务成功率为 `1/3`；若四个都有工具参考，工具准确率分母仍是 4。重复稳定也不等于质量高：每次都失败的 case 同样稳定。部分调用有 token 数据时，总量只是已观测部分，不能据此推断完整账单。

## 5. 如何防止 benchmark contamination 和读取 expected 答案作弊？

**答：** [`runner.py`](../src/agentops_eval/services/runner.py) 每次调用 adapter 前深拷贝 Case，并清空 `expected_answer`、`expected_tool_sequence` 和 assertions；配置也逐次深拷贝，避免一次执行修改后污染其他案例或后续重试。评分使用 Runner 保留的原始参考。确定性 Agent 在 [`deterministic.py`](../src/agentops_eval/adapters/deterministic.py) 中只根据 fixtures 执行计算、检索、恢复和聚合操作。

外部 HTTP 请求进一步采用字段白名单，只发送 task、category、fixtures，以及协议允许的 seed 和实验配置，不发送评分参考、标签、case id 或评分结果。OpenAI-compatible 的 user message 也仅含 task、category 和 fixtures。进程内 adapter 接口仍能收到 case id 与标签，不能把它等同于强隔离的远端输入协议。

这些措施防止常见的运行时答案泄漏，不能解决训练集污染或恶意进程内代码读取宿主文件。fixtures 本身也必须审查，不能暗藏评分答案。公开的合成数据容易被硬编码适配，因此它适合验证评测工程和控制变量；真实能力评估还需要独立保留集、数据来源与时间切分、版本管理和人工抽检。默认脱敏后的存档保留既有评分，但已删除答案参考，不能直接拿它重新评分；重新执行需要原始受控数据集。

## 6. 为什么重复三次不能把样本量直接当成三倍？比较如何保证公平？

**答：** 同一个 case 的多次执行共享任务、输入和参考，往往相关。把它们当作完全独立样本会夸大有效样本量，得到过窄的置信区间。

[`compare_runs`](../src/agentops_eval/services/comparison.py) 先按 `(case_id, repeat)` 配对，计算每对的成功差 `candidate - baseline`；排除任一方 skipped 或没有成功标签的配对。然后对同一 case 的有效差值求平均，最后对各 case 等权平均。bootstrap 重采样的单位也是 case 聚合后的差值，默认采样 2,000 次，用固定实验 seed 得到可复现区间。有效 case 少于 10 个时省略区间并给出提示。不同 case 的有效重复次数不一致时，这个差值可能不同于两份汇总成功率直接相减。

公平比较要求原始 dataset digest 和序列化案例定义一致，case × repeat 覆盖完整，seed、并发、Runner 重试等配置相同。消融需要显式开启 `allow_ablation`，仅放开 planner、tool descriptions、tool retries 三个因素，并返回实际变化字段。缺失配对不能伪装为失败或直接忽略。区间描述的是当前基准案例下的抽样不确定性，不代表真实业务总体保证；固定 seed 也不意味着所有外部模型服务都支持或遵守确定性设置。

## 7. timeout 是否能终止任意 Agent？async 并发是否就是沙箱？

**答：** 当前 timeout 是协作式异步执行边界。Runner 在取得并发名额后开始 case 计时，为每次尝试计算剩余预算，并用 `asyncio.timeout` 覆盖 adapter 调用；重试共享该执行预算。adapter 返回及响应校验后还会检查是否已经超时，避免同步阻塞或吞掉取消后返回的答案被记成成功。

如果 adapter 在事件循环里执行长时间同步计算，它会阻塞整个循环；超时回调只有重新获得调度机会才可能运行。若代码永远不返回，也没有可取消的 await，这个 Runner 无法硬终止它。Semaphore 只控制同时执行的案例数量，也不限制 CPU、内存、文件或网络权限。

[`tracing.py`](../src/agentops_eval/services/tracing.py) 先记录取消状态，再由 Runner 将自己的 deadline 取消对应的 span 标成 timeout；用户对整个评测的外部取消继续向上传播。要运行不可信 Agent，需要另加进程或容器隔离、资源限额、网络与文件访问策略，以及能够终止 worker 的执行器，不能把现有 Python 接口当作安全沙箱。

## 8. 重试为何可能带来新的错误？深拷贝是否保证幂等？

**答：** 深拷贝保证本地输入与配置隔离，不能撤销远端已经发生的副作用。一次 HTTP 请求可能已在远端完成写入，只是在响应返回途中断线；Runner 重试会重新进入整个 adapter，可能再次执行任务。

[`http.py`](../src/agentops_eval/adapters/http.py) 将 429、5xx 和传输错误标成可重试；Runner 是否重试取决于 `max_retries`，默认是 0，并受共享预算限制。当前没有幂等键、远端事务、退避抖动或“恰好一次”保证，因此接入有写入副作用的服务前，必须由服务协议定义幂等键、去重和补偿语义。不能仅根据状态码宣称重试安全。

还要区分 Runner 重试和 Agent 内部工具重试：前者重入整个 `execute`，后者只重做某个工具步骤。当前指标分别记录二者；工具选择检查比较所有尝试产生的完整有序 `tool_call`，不会把失败尝试从 Trace 中删掉。进程内 adapter 对象在并发 case 之间共享，adapter 自身的可变状态仍需保证并发安全。

## 9. HTTP 适配器有能力声明，就能证明远端具备该能力吗？成本如何记录？

**答：** 能力声明表示本实现愿意提交哪些类别的任务，不能替代对目标服务的能力验证。Workspace HTTP 声明 arithmetic、retrieval、long_input；Research HTTP 只声明 retrieval；OpenAI-compatible 只有纯文本推理，声明 arithmetic、long_input。Runner 将其他类别记为 skipped。尤其不能把一次纯文本模型请求解释成实际执行了检索工具或错误恢复循环。

HTTP 服务需自行落实请求中的实验设置。OpenAI-compatible 当前只发所配置模型与 messages，不发送兼容性未知的 seed，也没有工具执行循环。HTTP Trace 来自远端报告，先做结构校验与 ID 映射，仍不等于平台独立证实了每个远端操作。详见[适配器契约](ADAPTER_CONTRACT.md)与 [`openai_compatible.py`](../src/agentops_eval/adapters/openai_compatible.py)。

token 只取服务实际返回的 usage；只有 prompt 与 completion 都完整时才用两者之和，缺失时为 `null`，不按字符数推算。纯文本适配器的 cost 始终为 `null`，不根据模型名猜测价格。通用 HTTP Trace 可以携带远端报告的 cost，但当前没有统一币种、价格版本和账单核对，扩展成本分析前必须补足这些语义。现有测试验证的是模拟传输下的协议边界，没有证明任何在线服务已经接通。

## 10. 隐私、人工标注和平台化扩展如何取舍？

**答：** [`privacy.py`](../src/agentops_eval/infra/privacy.py) 在持久化和导出处统一做内容最小化。默认清除 task 正文、fixtures、答案参考、断言、标签和最终 answer；Trace metadata 仅保留受字段语义与类型限制的少量信息。工具名和其余字符串也经过常见凭证与路径脱敏。SQLite、JSON、JSONL 共享这一边界，避免只保护 Dashboard 而泄漏导出文件。

开启 `sensitive_trace` 才保留更多业务正文，但常见 Bearer、Basic、Cookie、JSON key/value、带引号密码和路径仍会脱敏。正则属于启发式检测，不能保证发现所有密钥或个人信息；原始内存对象仍含执行所需内容，也不能因此直接写进其他日志。公开任何带正文的产物前仍需审查。

人工标注通过 [`api/app.py`](../src/agentops_eval/api/app.py) 写入 [`storage.py`](../src/agentops_eval/infra/storage.py) 的独立 annotations 表，以 run、case、repeat 标识结果，不覆盖原始规则失败类型或指标。这有助于把“看失败”变成可查询的复核流程，但当前只有每个结果的最新归因，没有标注者身份、历史版本或一致性评估，也未自动生成训练数据。

当前定位是本地单机平台：默认监听本机，Dashboard 对动态内容使用 `textContent`，API 返回有限错误消息，并限制跨源写入。这些措施不能替代身份认证。进一步平台化时，应按需求加入鉴权与租户隔离、标注审计、worker 队列和硬执行隔离、受控的数据保留策略；随后再扩展业务评分器与人工一致性校验、生产数据回流、监控告警和可核对的成本体系。扩展时继续保留当前分母、配置对齐和缺失值语义，避免功能变多后数字失去可比性。
