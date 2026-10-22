# 可用于简历的项目描述

以下三条基于仓库已完成的实现，不包含虚构的用户规模、业务收益或线上性能。可根据岗位选择其中两至三条：

- 设计并实现独立于 Agent 框架的离线评测平台，以 Pydantic 定义任务、结果与 Trace 契约，构建 100 条可复现的四类合成任务与 10 条 CI 回归集，支持并发隔离、任务总超时、有限重试及重复评测。
- 实现 Agent / model / tool 的层级 Trace、SQLite 持久化与脱敏 JSON/JSONL 导出，提供具有明确分母的任务成功率、工具选择/执行指标、延迟分位数及失败规则归因，并通过 FastAPI Dashboard 支持筛选、Trace 检查和独立人工标注。
- 实现相同数据与配置约束下的 paired A/B、以 case 为簇的 bootstrap 置信区间和 planner / 工具描述 / 重试三类真实消融，提供确定性 Agent、HTTP 与 OpenAI-compatible 适配器、锁文件安装、回归 CI 及非 root Docker 演示流程。

面试时可用已执行的离线实验解释评测方法：100 条合成 case 各重复 3 次，baseline 195/300、candidate 300/300，差值 35 个百分点，case-cluster bootstrap 95% CI 为 `[0.26, 0.44]`。应同时解释：失败来自示例 Agent 的歧义工具选择与瞬态错误处理差异；合成基准由项目模板构造，满分不证明真实业务或模型泛化能力，不能把该数值包装为线上成功率提升。

演示命令与报告位置见 [DEMO.md](DEMO.md)，适配器接入范围与尚未进行的真实在线验证见 [ADAPTER_CONTRACT.md](ADAPTER_CONTRACT.md)。
