# Implementation Plan

## 目标与边界
独立、CPU/离线优先的 Agent 评测工程；所有指标来自真实执行，合成数据与真实业务明确区分。不修改相邻仓库。真实 API 仅在配置后启用。

## 阶段与完成条件
- **V0**：Pydantic 契约、确定性 Agent、Runner、JSON 导出、CLI、最小测试；离线 smoke 运行成功后提交。
- **V1**：100 条固定 seed 合成 cases、层级 Trace、SQLite、分母明确的指标、规则归因、paired bootstrap、HTTP/LLM 适配器；核心测试通过后提交。
- **V2**：FastAPI Dashboard、筛选/Trace/对比、人工标注、隐私边界、CI、Makefile、Docker；完整工程检查通过后提交。
- **V3**：从干净状态运行 Quick Start、三次重复与消融、生成真实报告、文档、隐私审核、GitHub PUBLIC 发布与复核。

## 文件结构
`src/agentops_eval/{domain,adapters,services,infra,api}`；`datasets/` 合成基准；`tests/` 单元与集成测试；`scripts/` 可复现 demo；`reports/` 仅审查过的合成数据执行结果；`docs/` 架构、演示、面试与验证。

## 验收
ruff + pytest + 10-case deterministic regression；完整 100-case × 3 对比及真实消融；API/CLI 与持久化一致；无 token/cost 伪造；Docker/网络验证按真实状态记录。发布前验证目标根目录、GitHub 账号、远端历史与敏感信息。

## 实际进度
- V0 已验收：2 tests + CLI smoke，commit `140802d`。
- V1 已验收：116 tests + 100×3 成对执行，commit `cb0c5c4`。
- V2 已验收：212 tests + UI/API/隐私/容器，commit `63da1d8`。
- V3 已验收：213 tests；独立空环境安装/Demo/服务、Compose 全流程通过；commit `9733246` 已公开推送，Python 3.11/3.12/3.13 远端 CI 全通过。具体后验见 `docs/VALIDATION.md`。
