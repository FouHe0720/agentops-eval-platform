# Synthetic evaluation datasets

这些是本项目人工设计模板、固定 seed 参数采样生成的**合成基准**，不是生产数据、真实用户请求或第三方 benchmark。所有文件名、文件内容、服务 payload 均由生成器构造，不读取主机文件、不访问网络、不包含个人信息。`files` 只是内存字典；`read_file` 只读取该字典中的值。

| 文件 | Case 数量 | 用途 |
| --- | ---: | --- |
| `synthetic.json` | 100 | 全量离线评测、版本比较、planner / 工具描述 / 重试消融 |
| `regression.json` | 10 | 从全量集合固定选取的四类回归子集，用于 CI |
| `smoke.json` | 1 | V0 最小端到端检查 |

完整集合包括 arithmetic 30、retrieval 30、recovery 20、long_input 20 条。所有记录均符合 `EvalCase` 契约；`expected_answer` 是独立 oracle 生成的完整答案，`expected_tool_sequence` 使用**严格顺序比较，保留重试产生的重复调用**。实际 Agent 仅根据 task / fixtures 执行工具，预期结果只供 scorer 使用。

- **Arithmetic**：加减乘各 10 条，包含零、负数抵消、大整数、精确二进制小数和固定 seed 随机数。
- **Retrieval**：对 fixture 文件名做区分大小写的子串匹配，按字典插入顺序读取，内容以 `|` 连接。覆盖无匹配、单匹配、三匹配、Unicode、空文件、多行内容与大小写干扰文件。20 条标记 ambiguous、15 条标记 multi_step；标签不代表外部 Agent 的真实难度估计。
- **Recovery**：5 条健康服务、15 条首调用失败后成功的瞬态错误；payload 覆盖文本、Unicode、JSON 文本、空字符串。预期调用分别为 `["fetch"]` 和 `["fetch", "fetch"]`。默认 candidate 的 `tool_retries=1` 能处理全部样本；本集合不宣称验证耗尽多次重试的能力。
- **Long input**：16 条数组求和，长度为 0–256，覆盖空数组、抵消、零、大整数、负数和混合数值；另有 4 条显式 `invalid=true` 的畸形输入。它们验证预先标记的验证路径，不宣称测试自动识别所有非法输入，也不构成模型上下文窗口压力测试。

生成与验证（项目根目录，先按主 README 安装项目）：

```bash
.venv/bin/python scripts/generate_dataset.py --seed 42
.venv/bin/pytest -q tests/test_datasets.py
```

生成器只覆盖 `synthetic.json` 与 `regression.json`；默认 seed 为 42，无时间戳或机器路径。`--output-dir /tmp/agentops-dataset-check` 可生成到独立目录以作字节比较。模板与 Python 随机数实现不变时输出逐字节可复现。回归集合直接取自完整集合，不能作为独立测试集解释。

在默认配置下，candidate_v2 应解出全部样本。baseline_v1 的失败来自真实机制：歧义工具选择时只列出文件名、没有工具重试；不根据 case ID 注入失败。关闭 planner 会影响 multi_step 检索及超过 10 元素的数组，基本工具描述会影响歧义检索，关闭工具重试会影响瞬态错误。候选满分只说明当前确定性模板契约通过，不能推断真实 Agent 的泛化能力。

数据及生成器均为本项目原创，按仓库根目录的 MIT License 自由使用、修改和分发；不引入第三方数据授权约束。更换模板或增加真实数据后应分别记录来源、授权、分布变化，并重新生成 digest，避免把不同数据集的分数直接比较。
