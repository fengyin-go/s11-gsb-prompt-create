# 平台验收准备度

本规则来自 2026-11-15 `dfzr10007` 的 39 条真实提交。初次质检后只有 18 条直接通过，
21 条进入废弃或待返修；其中 13 条是题面难度不足，8 条是轨迹或 Feedback 问题。

难度问题的逐题复盘见 [difficulty-hardening.md](difficulty-hardening.md)。本页处理剩余三类：

| 类别 | 题位 | 平台判词 | 出题交接能做什么 |
|---|---|---|---|
| Feedback 错归因 | 05、12 | 把缺兼容包、`pkill`/退出码 144 等环境或工具现象当模型扣分依据 | 交接时列出环境噪声排除项，并只声明模型自身可负责的行为 |
| Feedback 电报体 | 19、22 | 压缩短语、过程速记，用户需要回读才能理解 | 为每题冻结关键业务约束句和术语翻译，要求 Feedback 用完整主谓句 |
| 泛化测试结论 | 25、36 | 只报“15 个测试通过”“后端回归和前端类型检查通过” | 要求每个真实测试映射到需求与具体业务行为 |
| 跨会话记忆（历史记录） | 28、31 | 模型读取其他会话的答案笔记后照着改代码 | 保留历史证据，不转化为题面的上下文来源限制或声明 |

## review_contract

`handoff-metadata.tasks.<task>` 必须包含：

```json
{
  "review_contract": {
    "version": 1,
    "business_checks": [
      {
        "requirement_id": "R1",
        "behavior": "执行某动作后用户能看到的业务结果",
        "evidence_kind": "http",
        "success_signal": "可核对的状态、数据字段或文件内容"
      }
    ],
    "test_behaviors": [
      {
        "requirement_ids": ["R1"],
        "selector": "真实测试名或稳定选择器",
        "behavior": "该测试检查的具体业务行为"
      }
    ],
    "key_business_constraint": "必须逐字出现在题面中的关键约束句",
    "environment_exclusions": [
      "dependency_install",
      "gateway_network",
      "port_conflict",
      "process_cleanup",
      "tool_availability"
    ],
    "model_process_focus": [
      "只写模型自身代码、验证或判断造成的失败与修正"
    ],
    "style_focus": "用完整主谓句写业务行为、落差和用户可观察结果",
    "language_terms": [
      {"source": "内部缩写", "public": "用户能理解的动作或字段"}
    ]
  }
}
```

硬约束：

- `business_checks` 必须覆盖全部 requirements，不能把通用页面检查复制给每个需求。
- 每个 check 必须包含业务行为、证据类型和成功信号；`test_behaviors` 只能映射真实测试。
- `key_business_constraint` 必须逐字出现在最终题面，供后续语言复核。
- 环境排除项至少包含依赖安装、网关、端口、进程清理和工具可用性五类。
- `model_process_focus` 不得写网关、依赖、`pkill` 或退出码等环境噪声。
- 不生成或要求 `context_isolation`；历史元数据中的该字段不触发题面追加，也不是交接必填项。
- 最终题面不得出现限定模型上下文、会话、记忆或答案来源的声明；验收契约闸和题面表达闸都会拦截。
- `language_terms` 用于把内部缩写翻译成用户语言；空列表只允许题面本身没有任何需要翻译的术语。

`check_review_contract.py` 会在交接前校验，`prepare_handoff.py` 会再次执行并生成：

- `<task>/review-contract.json`：冻结的反馈生产契约；
- `task.json.review_contract` 与 `review_contract_sha256`：供运行侧读取和核对。

## 写作与验收纪律

- 环境故障不能自动算模型扣分；只有模型错报成功、绕开必需验证或把失败留在产物中才计入。
- 测试结论必须说“哪个测试检查了哪个业务行为”，不能只报总数或测试层级。
- 过程句用完整、自然的主谓宾，不写“同笔落印”“刷新水位链”这类压缩术语。
- 题面只保留业务目标和约束，不夹带模型上下文来源、指定文件、工具或内部代码结构要求。
