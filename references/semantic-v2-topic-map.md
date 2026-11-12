# S11 semantic-v2 topic map

先真实阅读基准提交的源码，必要时用内置浏览器查看页面。按业务对象而非文件数划分 topics：关键词能区分对象，evidence 指向真实文件或页面区域。模式词典、同 topic+模式唯一、同 topic 多席跨家族沿用生成规则。

```json
{
  "version": 1,
  "profile": "gsb-semantic-v2",
  "base_commit": "最终已推送的40位SHA",
  "max_capacity": 20,
  "topics": [
    {
      "id": "orders",
      "name": "订单",
      "module": "订单处理",
      "keywords": ["订单", "订单项"],
      "evidence": ["src/orders.py"],
      "seat_capacity": 2,
      "allowed_families": ["新增能力", "缺陷修复"]
    }
  ],
  "task_plan": [
    {
      "sequence": 1,
      "slug": "development",
      "task_type": "功能开发",
      "topic": "orders",
      "family": "新增能力",
      "angle": "基于真实代码选择的独特切入点"
    }
  ]
}
```

示例只展示字段，不是可直接建 20 题的完整计划；topic 座位与 task_plan 必须按实际容量补齐。slug/type 使用 SKILL.md 五类一一对应。sequence 为正整数且唯一，可保留撤题空号；目录和仓库同名，均为 `<原仓库名>-<sequence两位数>-<slug>`；工作簿编号 = start-index + sequence - 1。每条 family/angle 必填，同 topic 不重复 family，family 必须在 allowed_families 内（未指定 allowed_families 时不限）。每 topic seat_capacity 为 1..3，单仓默认上限 50，每模式默认 3。任务条数不得超过容量与 max_capacity。

类型策略 `type_policy` 默认 `capacity-first`：30/30/30/5/5 只是建议，不因配比缩减最高有效容量。只有用户明确指定固定比例才设 `strict-ratio` 并由 s11_policy.allocate_counts 校验。validate_topic_map.py 始终校验 schema、合法类型、topic 席位与基准 SHA。扩容保留旧条目的 sequence、type、topic、family、angle、任务号与仓库，只向末尾追加。
