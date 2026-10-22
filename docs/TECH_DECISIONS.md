# 技术决策

1. **Python 3.11+ / asyncio**：`asyncio.timeout` 给每个 case 的全部 runner 尝试一个总预算；Semaphore 限制并发。内置 Agent 只读进程内合成 fixtures，不接触磁盘/网络。异步取消是协作式的，不能把第三方阻塞代码当作安全沙箱；不可信代码应运行在独立容器/进程并经 HTTP 接入。
2. **SQLite + JSON/JSONL**：单机展示不需要 PostgreSQL/Redis；短连接、参数化查询、WAL、唯一 run ID。结构化 run 保存为已校验 JSON，易迁移；JSONL 保留 case/repeat 与父 span。暂不做跨机调度或流式写入，进程被强杀时进行中的 run 不保证保留。
3. **FastAPI + 原生 HTML/JS**：API 和 Dashboard 同源、本地启动、不需要 Node 构建或外部 CDN；图表只来自已持久化数据。保留 OpenAPI。默认仅监听 loopback，公网部署需增加认证、TLS、访问控制。
4. **无 Judge 真值**：规则比较答案与断言，工具采用包含失败重试在内的严格调用顺序；无参考不计成功率，未实现能力跳过。工具协议错误与答案成功可以并存，指标分别显示。无法确定错误原因保持 unclassified，可附人工标注而不覆盖原始结果。
5. **Paired case-cluster bootstrap**：相同 dataset SHA256、case/repeat、seed、运行配置；重复执行不是独立样本，先每 case 聚合 delta 再 bootstrap。少于 10 个 case 不给 CI。消融需要显式允许且只能变 planner/tool description/tool retry 三个参数。
6. **内容最小化**：adapter 不接收 expected fields；默认落库和导出丢弃 task/fixtures/答案/任意 metadata 内容；token/cost 仅记录上游实际提供的观测。显式 sensitive trace 也进行常见密钥模式脱敏，不能保证任意秘密自动识别。
7. **依赖与环境**：系统 Python 缺少 ensurepip，使用已有 uv 的项目级 `.venv`，未 sudo、未系统安装。pyproject 维护版本范围，uv.lock 固定本次解析；不使用 GPU 或模型权重。
8. **真实消融的边界**：baseline_v1 采用基础工具选择且不做瞬态工具重试；candidate_v2 使用歧义消解和有限重试。Planner 开关改变多步执行策略。这些是可追踪的确定性执行路径差异，不宣称等同于真实 LLM 规划提升。runner retry 与工具内部 retry 分别计数。

官方参考：[Pydantic validation](https://docs.pydantic.dev/latest/concepts/models/)、[HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/)、[FastAPI async tests](https://fastapi.tiangolo.com/advanced/async-tests/)。
