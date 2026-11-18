# S11 semantic-v2 topic map

先按 [domain-fit.md](domain-fit.md) 确认每个题位的主任务属于目标领域，再真实阅读基准提交的源码，必要时用内置浏览器查看页面。按业务对象而非文件数划分 topics：关键词能区分对象，evidence 指向真实文件或页面区域。模式词典、同 topic+模式唯一、同 topic 多席跨家族沿用生成规则。

```json
{
  "version": 1,
  "profile": "gsb-semantic-v2",
  "domain": "数据工程",
  "subdirection": "数仓与数据湖",
  "base_commit": "最终已推送的40位SHA",
  "difficulty_contract_version": 3,
  "dedup_contract_version": 1,
  "max_capacity": 20,
  "topics": [
    {
      "id": "orders",
      "name": "订单",
      "module": "订单处理",
      "keywords": ["订单", "订单项"],
      "evidence": ["src/orders.py"],
      "domain_objects": ["订单", "订单项"],
      "domain_results": ["导出文件", "字段完整"],
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
      "angle": "基于真实代码选择的独特切入点",
      "semantic_identity": {
        "version": 1,
        "object_key": "orders",
        "operation_key": "export",
        "outcome_keys": [
          "order_dataset_published",
          "export_traceable"
        ],
        "artifact_keys": [
          "order_dataset"
        ],
        "acceptance_entities": [
          "订单明细数据集"
        ]
      },
      "semantic_contrast": [],
      "difficulty_design": {
        "version": 3,
        "reasoning_challenge": {
          "constraints": [
            {
              "id": "C1",
              "kind": "transaction_consistency",
              "statement": "订单明细与来源批次必须同时可见",
              "failure_if_omitted": "下游会读到没有来源的明细"
            },
            {
              "id": "C2",
              "kind": "failure_recovery",
              "statement": "导出中断后只能补写缺失记录，不能覆盖上一份完整文件",
              "failure_if_omitted": "重试会回退已经可用的数据结果"
            }
          ],
          "why_interact": "恢复位置由已可见的来源批次决定，写入范围又决定下游可读取的明细版本。",
          "prompt_excerpt": "导出中断后保留上一份完整订单明细，重试只补写缺失记录，已发布批次不得回退。"
        },
        "module_integration": {
          "primary_module": "订单解析",
          "secondary_modules": [
            {
              "name": "分析导出中心",
              "role": "consumer",
              "artifact": "已发布订单明细数据集",
              "relationship": "导出中心读取发布后的同一批次并生成可追溯文件"
            }
          ],
          "data_artifact": "已发布订单明细数据集",
          "prompt_excerpt": "分析导出中心必须读取同一份已发布订单明细数据集并保留来源批次。"
        },
        "workload_design": {
          "version": 1,
          "expected_effective_tool_calls": 60,
          "call_floor_rationale": "需要完成订单解析、发布恢复、导出消费和两条独立验证路径",
          "work_slices": [
            {
              "module": "订单解析",
              "implementation": "补齐订单明细与来源批次写入",
              "data_result": "生成带来源批次的订单明细记录",
              "verification": "核对明细与来源批次同时可见"
            },
            {
              "module": "订单解析",
              "implementation": "补齐发布中断后的缺失记录定位",
              "data_result": "生成只包含未完成记录的恢复批次",
              "verification": "中断重试不会覆盖完整发布结果"
            },
            {
              "module": "分析导出中心",
              "implementation": "读取发布结果并生成导出文件",
              "data_result": "生成可追溯的订单明细导出文件",
              "verification": "导出文件与发布结果逐字段一致"
            },
            {
              "module": "分析导出中心",
              "implementation": "处理迟到更正后的导出重建",
              "data_result": "生成受影响业务窗口的补算清单",
              "verification": "补算清单可以回查完整发布结果"
            }
          ],
          "verification_paths": [
            "运行订单发布入口并验证中断恢复",
            "运行导出中心并比较导出文件与发布结果"
          ]
        },
        "design_tradeoff": {
          "decision": "恢复边界按采集时间还是业务时间确定",
          "decision_axis": "effective_time",
          "trigger": "迟到订单与历史更正落在已发布窗口",
          "options": ["按采集时间恢复", "按业务时间恢复"],
          "outcome_invariant": "恢复完成后下游只能看到完整订单明细数据集",
          "why_open": "两种边界都可行，题面只固定可观察结果，不替设计者选择内部键。",
          "context_excerpt": "迟到订单与历史更正落在已发布窗口时，恢复边界会改变补算范围，但下游始终只能读取一份完整订单明细数据集。",
          "context_anchors": [
            {
              "path": "src/orders.py",
              "start_line": 12,
              "end_line": 20,
              "supports": "trigger",
              "excerpt": "解析订单字段并生成明细记录"
            },
            {
              "path": "src/exports.py",
              "start_line": 8,
              "end_line": 15,
              "supports": "option_a",
              "excerpt": "导出文件保留来源批次和订单字段"
            }
          ],
          "option_analysis": [
            {
              "option": "按采集时间恢复",
              "benefit": "按采集顺序恢复可以缩小扫描范围",
              "cost": "迟到更正需要独立补算受影响业务窗口",
              "viable_when": "采集顺序可靠且存在迟到记录补偿机制",
              "preserves_constraint_ids": ["C1", "C2"],
              "correctness_effect": "按采集顺序推进会缩小扫描范围，但迟到更正必须独立补偿",
              "observable_result": "恢复文件按采集顺序生成，并附迟到业务窗口清单"
            },
            {
              "option": "按业务时间恢复",
              "benefit": "按业务窗口重算可以直接核对历史完整性",
              "cost": "历史窗口重新扫描会增加去重与计算成本",
              "viable_when": "业务时间可靠且可以承担历史窗口重算成本",
              "preserves_constraint_ids": ["C1", "C2"],
              "correctness_effect": "按业务窗口重算会扩大扫描范围，但能直接校正历史口径",
              "observable_result": "恢复文件包含受影响业务窗口并保留同一来源批次"
            }
          ],
          "both_options_valid": true,
          "choice_changes_correctness": true,
          "prompt_keeps_choice_open": true,
          "fixed_branch_excluded": true,
          "false_h3_guards": {
            "one_branch_violates_constraints": false,
            "fixed_resolution_in_prompt": false,
            "implementation_only": false,
            "retry_or_error_message_only": false,
            "display_only": false,
            "single_result_invariant_only": false,
            "source_selection_only": false,
            "equivalent_user_outcome": false
          },
          "false_h3_counterexample": "先规定迟到更正必须采用业务时间，再要求设计者选择恢复边界",
          "decision_impact": "恢复时间轴改变迟到订单的补算范围与下游文件内容",
          "implementation_not_prescribed": true,
          "prompt_excerpt": "迟到订单与历史更正落在已发布窗口时，恢复边界会改变补算范围，但下游始终只能读取一份完整订单明细数据集。"
        },
        "medium_counterexample": {
          "generic_task": "给订单列表增加导出按钮并保持列表和详情一致",
          "why_medium": "只做局部展示和保存，没有数据产物约束或需要判断的设计轴"
        },
        "hard_evidence": ["事务与落库一致性", "断点续做与失败链路"],
        "anti_patterns": {
          "single_surface_only": false,
          "secondary_observation_only": false,
          "solution_prescribed": false,
          "single_constraint": false,
          "hard_evidence_tail_only": false,
          "example_data_only": false,
          "cross_call_merge_only": false
        },
        "module_anchors": [
          {
            "path": "src/orders.py",
            "start_line": 12,
            "end_line": 20,
            "module": "订单解析",
            "supports": "primary",
            "excerpt": "解析订单字段并生成明细记录"
          },
          {
            "path": "src/exports.py",
            "start_line": 8,
            "end_line": 15,
            "module": "分析导出中心",
            "supports": "secondary",
            "excerpt": "导出文件保留来源批次和订单字段"
          }
        ]
      },
      "domain_fit": {
        "version": 2,
        "main_task": "解析订单数据并导出订单明细数据集",
        "business_result": "订单明细数据集保留来源批次和完整订单字段",
        "domain_chain": {
          "input": "订单数据",
          "operation": "解析订单数据",
          "output": "订单明细数据集",
          "observable": "导出文件保留来源批次和完整订单字段"
        },
        "prompt_main_request": "请解析订单数据并生成订单明细数据集，保证导出文件保留来源批次和完整订单字段。",
        "genericization": {
          "rewritten_with_generic_objects": "维护一张普通业务配置表",
          "same_task_without_domain_semantics": false
        },
        "source_evidence": ["src/orders.py", "src/exports.py"],
        "domain_anchors": [
          {
            "path": "src/orders.py",
            "start_line": 12,
            "end_line": 20,
            "supports": "operation",
            "excerpt": "解析订单字段并生成明细记录"
          },
          {
            "path": "src/exports.py",
            "start_line": 8,
            "end_line": 15,
            "supports": "output",
            "excerpt": "导出文件保留来源批次和订单字段"
          }
        ],
        "conclusion": "pass"
      }
    }
  ]
}
```

示例只展示字段，不是可直接建 20 题的完整计划；topic 座位与 task_plan 必须按实际容量补齐。`max_capacity` 是包含新增主体座位在内的绝对上限。新正式批次的 `task_plan` 必须填满最终有效容量；已有批次恢复时保留原题位。`domain`、`subdirection`、每个 topic 的 `domain_objects`/`domain_results`、每个题位的 `domain_fit` 都是建仓、写表和交接的硬输入。`domain_fit.source_evidence` 必须指向基准提交里真实存在的源码或页面依据，不能用仓库标题、README 口号或计划以后才实现的文件。slug/type 使用 SKILL.md 五类一一对应。sequence 为正整数且唯一，可保留撤题空号；目录和仓库同名，均为 `<原仓库名>-<sequence两位数>-<slug>`；工作簿编号 = start-index + sequence - 1。每条 family/angle 必填，同 topic 不重复 family，family 必须在 allowed_families 内（未指定 allowed_families 时不限）。每 topic seat_capacity 为 1..3，单仓默认上限 50，每模式默认 3。

最终题面除命中 topic、`domain_objects` 和 `domain_results` 外，还必须逐字写入 `domain_chain` 的输入、处理、输出、可观察结果以及前置的 `prompt_main_request`。脚本会核对源码锚点、连续原文和主请求位置；只出现业务名词、把领域句放在末尾或替换对象后仍是通用服务任务，都会在写表前被拦下。细节见 [domain-fit.md](domain-fit.md)。

`difficulty_contract_version` 和 `task_plan[].difficulty_design` 是独立的第二个硬契约。H1/H2/H3 的题面摘录必须逐字出现在最终提示词，第二模块必须是真实数据流角色，设计取舍必须至少保留两个方案。v3 要求两个方案都覆盖全部 H1 约束编号，分别产生不同的正确性影响和可观察结果；题面只能留下共同不变量，不能预先规定哪个分支获胜。`context_anchors` 至少两条，一条支撑触发条件，一条支撑至少一个方案边界。完整字段、`dfzr10012` 的 `14` 正样本与 `10/11` 反样本见 [difficulty-hardening.md](difficulty-hardening.md)。

`dedup_contract_version: 1` 和 `task_plan[].semantic_identity` 是独立的语义查重契约。
`object_key` 必须等于 topic id；`operation_key` 使用
`scripts/semantic_dedup.py` 的有限枚举；`outcome_keys` 至少两个，`artifact_keys` 至少一个，
`acceptance_entities` 至少一个。正式机会卡中的这两个字段必须与 topic map 完全一致。

同一 topic 下，`object_key + operation_key` 相同且 outcome、artifact、acceptance 任一有
交集时直接拒绝。不同任务类型不是差异。非冲突的同 topic 题位必须通过
`semantic_contrast` 指向同主题 peer，写至少两个不同维度，并至少包含 `operation` 或
`observable_result`。示例：

```json
{
  "peer_sequence": 5,
  "differences": [
    {
      "dimension": "operation",
      "self": "导出筛选后的执行日志",
      "peer": "恢复失败执行日志的重试记录"
    },
    {
      "dimension": "observable_result",
      "self": "用户得到可追溯的导出文件",
      "peer": "用户得到独立的重试记录"
    }
  ]
}
```

没有能力写出一条新的主业务动作或新的用户可观察结果时，只能把 topic 的第二座位降为 1；
需要更多题量时换 topic 或换仓库，不能把同一重试/恢复问题换成另一种任务类型重写。

类型策略 `type_policy` 默认 `capacity-first`：30/30/30/5/5 只是建议，不因配比缩减最高有效容量。只有用户明确指定固定比例才设 `strict-ratio` 并由 s11_policy.allocate_counts 校验。validate_topic_map.py 始终校验 schema、合法类型、topic 席位与基准 SHA。扩容保留旧条目的 sequence、type、topic、family、angle、任务号与仓库，只向末尾追加。

数据页面只作为通用工程任务的演示载体时，不计为合格领域题位或第二个业务面。例如在血缘和圈选页验证 Vite 升级、路由前缀和静态资源发布，仍是前端迁移。业务名、页面数、并发/幂等词和困难闸门命中均不能代替真实领域任务；不合格题位先替换再计算有效容量。
