# 校园资料回归记录

本次引用与拒答检查：17 / 17。
数据集 17 条：14 条原文引用检查，3 条资料缺失、校区或版本不匹配拒绝检查。
词法基线从实际摘录中返回原文；没有调用 LLM，不表示模型回答准确率。
token 和费用均为 null。毫秒耗时来自本地规则执行，不作模型性能指标。

数据 SHA-256：`4052698e5c7f6692c46e4cf143142525de70d0a846299a73f462d08ced5c9c97`。

| 用例 | 结果 | 来源 |
| --- | --- | --- |
| campus-selection-window | 通过 | course-selection-2024 |
| campus-selection-plan | 通过 | course-selection-2024 |
| campus-selection-general | 通过 | course-selection-2024 |
| campus-english-exception | 通过 | english-placement-2024 |
| campus-english-duration | 通过 | english-placement-2024 |
| campus-navigation-quota | 通过 | navigation-admission-2024 |
| campus-navigation-weight | 通过 | navigation-admission-2024 |
| campus-data-science-scope | 通过 | data-science-admission-2024 |
| campus-data-science-certificate | 通过 | data-science-admission-2024 |
| campus-law-quota | 通过 | computational-law-admission-2024 |
| campus-law-weight | 通过 | computational-law-admission-2024 |
| campus-cs-duration | 通过 | cs-curriculum-2024 |
| campus-ai-duration | 通过 | ai-curriculum-2024 |
| campus-physics-credits | 通过 | physics-curriculum-2023 |
| campus-unknown-topic | 通过 | 无适用来源，应拒绝 |
| campus-wrong-campus | 通过 | 无适用来源，应拒绝 |
| campus-current-policy | 通过 | 无适用来源，应拒绝 |

完整分母与来源指纹见同目录 campus-regression.json。
