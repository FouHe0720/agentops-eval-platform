# 外部 Agent 连接契约

默认 baseline/candidate 完全离线。外部适配器只有在明确选择并配置后才会请求网络；
未配置时抛出安全的 `AdapterError("not_configured")`，不会影响离线评测。
模块位于 `agentops_eval.adapters.http` 和 `agentops_eval.adapters.openai_compatible`。

| Adapter | 必需配置 | 可选 Bearer key | 适用类别 |
| --- | --- | --- | --- |
| `WorkspaceHTTPAgentAdapter` | `WORKSPACE_AGENT_ENDPOINT` | `WORKSPACE_AGENT_API_KEY` | arithmetic、retrieval、long_input |
| `ResearchAgentAdapter` | `RESEARCH_AGENT_ENDPOINT` | `RESEARCH_AGENT_API_KEY` | retrieval |
| `OpenAICompatibleAdapter` | `OPENAI_BASE_URL`、`OPENAI_MODEL` | `OPENAI_API_KEY` | arithmetic、long_input |

构造参数 `endpoint`（OpenAI 为 `base_url`、`model`）、`api_key`、`timeout` 可覆盖环境变量。
无需 key 的自建服务可省略 key；显式传 `api_key=""` 可覆盖已有环境变量。
HTTP URL 必须使用 http/https，不接受嵌入账号密码或 fragment。适配器不跟随重定向，
不读取环境代理。默认 HTTP timeout 为 30 秒，实际取配置值与 case timeout 的较小值；
Runner 另有覆盖重试在内的总 case 时间预算。配置和密钥只在进程内使用。

能力声明是本实现愿意评测的范围，不是对任意目标服务能力的保证。目标服务必须实现该范围；
Research 不接受算术/恢复任务，纯文本 OpenAI 适配器没有工具循环，不接受 retrieval/recovery。
Runner 将不支持的类别记为 skipped，并从适用分母中排除。HTTP 服务也应独立校验输入。

## Workspace / Research HTTP

向 endpoint 原样发一次 `POST`，使用 `Content-Type: application/json`：

```json
{"task":"Add the fixture values","category":"arithmetic","fixtures":{"a":2,"b":3},"seed":42,"config":{"planner":true,"tool_descriptions":"improved","tool_retries":1}}
```

任务输入只有 `task`、`category`、`fixtures` 三个字段；另发送本次 `seed` 和
`config={planner, tool_descriptions, tool_retries}` 以支持可重复实验与消融。
远端必须自行落实这些设置；协议传递设置本身不代表服务已遵守，接入后需验证。
绝不发送 sensitive_trace、`expected_answer`、
`expected_tool_sequence`、assertions、评测标签、case id 或评分结果。fixtures 必须是任务本身可见的输入，
不得把评分答案放入 fixtures。适配器不会将本机文件或宿主环境隐式提供给远端。

成功响应是 JSON 对象，仅允许 `answer`（必需，最多 20,000 字符）和 `trace`（可省略，最多 1,000 项）：

```json
{
  "answer":"5",
  "trace":[
    {"span_id":"remote-root","event_type":"agent"},
    {"span_id":"remote-call","parent_span_id":"remote-root","event_type":"tool_call","tool_name":"calculator","duration_ms":1.2},
    {"span_id":"remote-result","parent_span_id":"remote-call","event_type":"tool_result","tool_name":"calculator"}
  ]
}
```

每个 span 只接受以下字段，未知字段（包括任意 metadata、prompt、header 或工具原始输出）会使整个响应被拒绝：

| 字段 | 契约 |
| --- | --- |
| `span_id` | 必需；1–80 字符，限字母、数字、`_ . : -`；全 trace 唯一 |
| `parent_span_id` | 可选；父 span 必须先出现；非空 trace 必须且只能有一个根 |
| `run_id` | 可选；最多 128 字符；所有出现的值必须一致 |
| `event_type` | agent / model_call / tool_call / tool_result / final / retry |
| `tool_name` | tool_call/tool_result 必需；与 span_id 相同字符约束 |
| `status` | ok / error / timeout，默认 ok |
| `duration_ms` | 有限非负数，默认 0；由远端报告，不替代本地端到端计时 |
| `token_usage` | 真实观测的非负整数，未知省略/null，禁止估算或伪造 |
| `cost` | 真实观测的有限非负金额，未知省略/null；平台不假定币种或价格 |

完全校验后才导入 trace，run id 与全部 span id 会重新生成/映射到本地 run，
远端根成为本地 adapter agent span 的子节点。循环、前向引用、孤儿、重复 id、混合 run、
多根、负数、无穷数、类型错误或未知字段均记为 invalid_response，不导入部分 trace。
远端 span 时间戳不受信任；本地导入时间戳表示接收时间。
无 trace 的响应仍可用于答案评分，但没有工具执行证据，不能据此声称工具选择正确。

## OpenAI-compatible

向 `${OPENAI_BASE_URL}/chat/completions` 发真实 HTTP POST；base URL 应包含服务所需的 `/v1`。
请求仅包含所配置的 model 和 system/user messages。user message 是上述 task/category/fixtures
三字段的 JSON；不提供预期答案或工具定义，不伪装执行外部工具。
本实现不向兼容性未知的模型服务发送 seed，也不宣称模型输出具有确定性。

响应要求 `choices[0].message.content` 为字符串；非空 tool_calls 会被拒绝。
`usage.total_tokens` 如存在则原样记录；若只提供 prompt_tokens 与 completion_tokens，记录其和。
usage 缺失或不完整时 token_usage 为 null；所有计数必须为非负整数。
不根据字符数推算 tokens，不根据模型名推算价格，cost 始终 null。
每次 HTTP 推理对应真实的 `model_call` span，作为 Runner agent span 子节点。

## 错误与隐私

响应体上限 1 MiB。非 2xx 为 `http_error`；429/5xx 与 transport_error 可由 Runner 重试，
HTTP timeout 对应本地 TimeoutError/timeout 状态；畸形响应为 invalid_response。
API key、请求头、原始请求/响应体、服务端异常文本与 URL 均不写入 trace/error。
本地 request/adapter span 的失败状态会保留；错误码不会携带服务端内容。
最终 answer 是用户要求保存的输出，外部服务自行放入 answer 的敏感内容仍需由调用者审查。

当前只通过 httpx MockTransport 完成协议、超时、错误、能力范围和脱敏测试；
未声称接入任何 workspace-agent、deep-research-agent 或在线 LLM 服务。
