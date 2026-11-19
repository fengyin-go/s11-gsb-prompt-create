from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import difficulty_contract


HERE = Path(__file__).resolve().parent
REJECTIONS = json.loads(
    (HERE / "fixtures" / "platform-difficulty-rejections-20261115.json").read_text(
        encoding="utf-8"
    )
)["cases"]


class DifficultyContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="difficulty-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        (self.repo / "src").mkdir()
        (self.repo / "src/orders.py").write_text(
            "def build_orders():\n"
            "    # 解析订单数据并生成已发布订单数据集\n"
            "    return []\n",
            encoding="utf-8",
        )
        (self.repo / "src/exports.py").write_text(
            "def export_orders():\n"
            "    # 从已发布订单数据集生成导出文件并保留来源批次\n"
            "    return []\n",
            encoding="utf-8",
        )
        self.prompt = (
            "请解析订单数据并生成已发布订单数据集。"
            "导出中断后保留上一份完整订单数据集，重试只补写缺失记录，"
            "已发布批次不得回退。"
            "分析导出中心必须读取同一份已发布订单数据集并保留来源批次。"
            "迟到订单与历史更正落在已发布窗口时，补算的生效顺序会影响下游读取，"
            "补算期间只能发布一份完整订单数据集，不能把新旧窗口结果混在同一文件中。"
        )
        self.design = {
            "version": 3,
            "reasoning_challenge": {
                "constraints": [
                    {
                        "id": "C1",
                        "kind": "transaction_consistency",
                        "statement": "订单明细与来源批次必须同时可见",
                        "failure_if_omitted": "下游会读到没有来源的明细",
                    },
                    {
                        "id": "C2",
                        "kind": "failure_recovery",
                        "statement": "中断后只能补写缺失记录，不能覆盖完整版本",
                        "failure_if_omitted": "重试会回退已经可用的数据结果",
                    },
                ],
                "why_interact": "恢复位置由已可见批次决定，写入范围又决定下游读取的版本。",
                "prompt_excerpt": (
                    "导出中断后保留上一份完整订单数据集，重试只补写缺失记录，"
                    "已发布批次不得回退。"
                ),
            },
            "module_integration": {
                "primary_module": "订单解析",
                "secondary_modules": [
                    {
                        "name": "分析导出中心",
                        "role": "consumer",
                        "artifact": "已发布订单数据集",
                        "relationship": "导出中心读取发布后的同一批次并生成可追溯文件",
                    }
                ],
                "data_artifact": "已发布订单数据集",
                "prompt_excerpt": (
                    "分析导出中心必须读取同一份已发布订单数据集并保留来源批次。"
                ),
            },
            "workload_design": {
                "version": 1,
                "expected_effective_tool_calls": 60,
                "call_floor_rationale": (
                    "需要跨两个模块完成实现、数据回填、导出联调和两条独立验证路径"
                ),
                "work_slices": [
                    {
                        "module": "订单解析",
                        "implementation": "补齐订单版本与来源批次的归一处理",
                        "data_result": "生成带来源批次的可发布订单数据集",
                        "verification": "核对同一订单不会生成重复发布版本",
                    },
                    {
                        "module": "订单解析",
                        "implementation": "补齐中断后的缺失记录定位和补写",
                        "data_result": "生成只包含未完成记录的恢复批次",
                        "verification": "中断后重试不会覆盖上一完整批次",
                    },
                    {
                        "module": "分析导出中心",
                        "implementation": "让导出中心读取已发布版本和来源批次",
                        "data_result": "生成可追溯的订单导出文件",
                        "verification": "导出文件与发布版本逐字段一致",
                    },
                    {
                        "module": "分析导出中心",
                        "implementation": "处理迟到更正后的导出重建",
                        "data_result": "生成受影响业务窗口的补算清单",
                        "verification": "补算清单与完整发布快照可以相互追溯",
                    },
                ],
                "verification_paths": [
                    "运行订单发布服务并调用恢复入口验证断点续做",
                    "运行导出中心并比较导出文件与发布快照",
                ],
            },
            "design_tradeoff": {
                "decision": "恢复边界按采集时间还是业务时间确定",
                "decision_axis": "effective_time",
                "trigger": "迟到订单与历史更正落在已发布窗口",
                "options": ["按采集时间恢复", "按业务时间恢复"],
                "outcome_invariant": "恢复完成后下游只能看到完整订单数据集",
                "why_open": "两种边界都可行，题面只固定可观察结果，不选择内部键。",
                "context_excerpt": (
                    "迟到订单与历史更正落在已发布窗口时，补算的生效顺序会影响下游读取，"
                    "补算期间只能发布一份完整订单数据集，不能把新旧窗口结果混在同一文件中。"
                ),
                "context_anchors": [
                    {
                        "path": "src/orders.py",
                        "start_line": 2,
                        "end_line": 2,
                        "supports": "trigger",
                        "excerpt": "解析订单数据并生成已发布订单数据集",
                    },
                    {
                        "path": "src/orders.py",
                        "start_line": 3,
                        "end_line": 3,
                        "supports": "option_a",
                        "excerpt": "return []",
                    },
                    {
                        "path": "src/orders.py",
                        "start_line": 3,
                        "end_line": 3,
                        "supports": "option_b",
                        "excerpt": "return []",
                    },
                ],
                "option_analysis": [
                    {"option": "按采集时间恢复", "benefit": "顺序读取采集批次可以减少历史扫描范围",
                     "cost": "迟到订单需另行补算否则会遗漏业务窗口",
                     "viable_when": "采集顺序稳定且存在迟到记录补偿路径",
                     "preserves_constraint_ids": ["C1", "C2"],
                     "correctness_effect": "按采集顺序推进会缩小扫描范围，但迟到更正必须单独补偿",
                     "observable_result": "补算文件先落采集序列，再生成独立的业务窗口更正清单"},
                    {"option": "按业务时间恢复", "benefit": "直接覆盖受影响的业务窗口便于核对结果",
                     "cost": "历史更正会扩大重新扫描与去重处理范围",
                     "viable_when": "业务时间可靠且可以承担历史窗口重算成本",
                     "preserves_constraint_ids": ["C1", "C2"],
                     "correctness_effect": "按业务窗口重算会扩大扫描范围，但能直接校正历史业务口径",
                     "observable_result": "补算文件按受影响业务窗口生成完整快照并保留来源批次"},
                ],
                "both_options_valid": True,
                "choice_changes_correctness": True,
                "prompt_keeps_choice_open": True,
                "fixed_branch_excluded": True,
                "false_h3_guards": {
                    "one_branch_violates_constraints": False,
                    "fixed_resolution_in_prompt": False,
                    "implementation_only": False,
                    "retry_or_error_message_only": False,
                    "display_only": False,
                    "single_result_invariant_only": False,
                    "source_selection_only": False,
                    "equivalent_user_outcome": False,
                },
                "false_h3_counterexample": (
                    "题面先写迟到更正必须采用业务时间，再追加由设计者选择恢复边界"
                ),
                "decision_impact": "恢复时间轴改变迟到订单的补算范围和扫描成本",
                "implementation_not_prescribed": True,
                "prompt_excerpt": (
                    "迟到订单与历史更正落在已发布窗口时，补算的生效顺序会影响下游读取，"
                    "补算期间只能发布一份完整订单数据集，不能把新旧窗口结果混在同一文件中。"
                ),
            },
            "medium_counterexample": {
                "generic_task": "给订单列表增加导出按钮并保持列表和详情一致",
                "why_medium": "只做局部展示和保存，没有数据产物约束或设计判断",
            },
            "hard_evidence": ["事务与落库一致性", "断点续做与失败链路"],
            "anti_patterns": {
                name: False
                for name in (
                    "single_surface_only",
                    "secondary_observation_only",
                    "solution_prescribed",
                    "single_constraint",
                    "hard_evidence_tail_only",
                    "example_data_only",
                    "cross_call_merge_only",
                )
            },
            "module_anchors": [
                {
                    "path": "src/orders.py",
                    "start_line": 2,
                    "end_line": 2,
                    "module": "订单解析",
                    "supports": "primary",
                    "excerpt": "解析订单数据并生成已发布订单数据集",
                },
                {
                    "path": "src/exports.py",
                    "start_line": 2,
                    "end_line": 2,
                    "module": "分析导出中心",
                    "supports": "secondary",
                    "excerpt": "从已发布订单数据集生成导出文件并保留来源批次",
                },
            ],
        }
        self.plan = {
            "sequence": 1,
            "slug": "development",
            "task_type": "功能开发",
            "topic": "orders",
            "family": "新增能力",
            "angle": "订单发布与导出",
            "difficulty_design": self.design,
        }
        self.topic = {
            "difficulty_contract_version": 3,
            "task_plan": [self.plan],
        }
        self.record = {
            "子文件夹名称": "orders-01-development",
            "任务类型": "功能开发",
            "提示词": self.prompt,
        }

    def errors(self, topic=None, record=None):
        return difficulty_contract.prompt_errors(
            [record or self.record],
            topic or self.topic,
            self.repo,
        )

    def test_valid_contract_passes(self):
        self.assertEqual(self.errors(), [])

    def test_all_20261115_platform_difficulty_rejections_fail_without_contract(self):
        self.assertEqual(len(REJECTIONS), 13)
        for case in REJECTIONS:
            with self.subTest(case=case["task_id"]):
                topic = {
                    "difficulty_contract_version": 3,
                    "task_plan": [{
                        "sequence": case["sequence"],
                        "task_type": case["task_type"],
                        "difficulty_design": None,
                    }],
                }
                record = {
                    "子文件夹名称": case["task_id"],
                    "任务类型": case["task_type"],
                    "提示词": case["prompt"],
                }
                errors = difficulty_contract.prompt_errors([record], topic, self.repo)
                self.assertTrue(any("difficulty_design" in error for error in errors))
                self.assertTrue(case["missing_features"])
                self.assertTrue(case["platform_summary"])

    def test_same_hard_constraint_class_twice_is_rejected(self):
        topic = copy.deepcopy(self.topic)
        constraints = topic["task_plan"][0]["difficulty_design"]["reasoning_challenge"][
            "constraints"
        ]
        constraints[1]["kind"] = constraints[0]["kind"]
        self.assertTrue(any("两种不同约束类型" in error for error in self.errors(topic)))

    def test_display_only_second_module_is_rejected(self):
        topic = copy.deepcopy(self.topic)
        topic["task_plan"][0]["difficulty_design"]["module_integration"][
            "secondary_modules"
        ][0]["role"] = "display"
        self.assertTrue(any("role 必须是真实数据流角色" in error for error in self.errors(topic)))

    def test_prompt_excerpt_must_reach_final_prompt(self):
        record = copy.deepcopy(self.record)
        record["提示词"] = record["提示词"].replace(
            "补算的生效顺序会影响下游读取", "补算采用固定业务时间边界"
        )
        self.assertTrue(any("prompt_excerpt 不在最终题面中" in error for error in self.errors(record=record)))

    def test_ordered_solution_without_open_tradeoff_is_rejected(self):
        topic = copy.deepcopy(self.topic)
        design = topic["task_plan"][0]["difficulty_design"]
        design["design_tradeoff"]["prompt_excerpt"] = "按步骤执行"
        record = copy.deepcopy(self.record)
        record["提示词"] = (
            "请解析订单数据并生成已发布订单数据集。"
            "第一步先校验，第二步再解析，第三步逐项写入，然后依次导出。"
            "导出中断后保留上一份完整订单数据集，重试只补写缺失记录，"
            "已发布批次不得回退。分析导出中心必须读取同一份已发布订单数据集并保留来源批次。"
            "按步骤执行。"
        )
        errors = self.errors(topic, record)
        self.assertTrue(any("题面步骤写得太死" in error for error in errors))

    def test_tradeoff_excerpt_requires_a_real_open_choice(self):
        topic = copy.deepcopy(self.topic)
        design = topic["task_plan"][0]["difficulty_design"]
        design["design_tradeoff"]["prompt_excerpt"] = "已发布批次不得回退"
        record = copy.deepcopy(self.record)
        record["提示词"] = (
            "请解析订单数据并生成已发布订单数据集。"
            "导出中断后保留上一份完整订单数据集，重试只补写缺失记录，"
            "已发布批次不得回退。"
            "分析导出中心必须读取同一份已发布订单数据集并保留来源批次。"
            "已发布批次不得回退。"
        )
        errors = self.errors(topic, record)
        self.assertTrue(any("必须留下真实选择权" in error for error in errors))

    def test_natural_business_tension_does_not_require_delegation(self):
        topic = copy.deepcopy(self.topic)
        tradeoff = topic["task_plan"][0]["difficulty_design"]["design_tradeoff"]
        excerpt = "迟到订单与历史更正落在已发布窗口，补算期间导出仍读取完整订单数据集"
        tradeoff["trigger"] = "迟到订单与历史更正落在已发布窗口"
        tradeoff["prompt_excerpt"] = excerpt
        tradeoff["context_excerpt"] = excerpt
        record = copy.deepcopy(self.record)
        record["提示词"] = (
            "请解析订单数据并生成已发布订单数据集。"
            "导出中断后保留上一份完整订单数据集，重试只补写缺失记录，已发布批次不得回退。"
            "分析导出中心必须读取同一份已发布订单数据集并保留来源批次。"
            + excerpt
            + "。"
        )
        self.assertEqual(self.errors(topic, record), [])

    def test_unrelated_tension_cannot_prove_the_context_axis(self):
        self.assertFalse(difficulty_contract._has_tradeoff_evidence(
            "同一商品采用两种不同价格，库存分配发生冲突", "订单恢复中断后保留完整数据集"
        ))

    def test_fixed_resolution_is_not_natural_open_evidence(self):
        excerpt = "历史订单检查点发生冲突时，以最新检查点为准"
        self.assertFalse(difficulty_contract._has_tradeoff_evidence(excerpt, excerpt))

    def test_period_coverage_is_a_business_tension(self):
        excerpt = "基准周期与对比周期可能天数不同，同一维度也可能只在其中一期出现"
        self.assertTrue(difficulty_contract._has_tradeoff_evidence(excerpt, excerpt))

    def test_free_implementation_choice_without_costs_is_not_h3(self):
        topic = copy.deepcopy(self.topic)
        tradeoff = topic["task_plan"][0]["difficulty_design"]["design_tradeoff"]
        tradeoff.update({
            "decision": "全量重算还是按变更更新由设计者选择",
            "options": ["全量重算", "按变更更新"],
            "prompt_excerpt": "全量重算还是按变更更新由你选择，但结果必须正确",
        })
        for key in ("option_analysis", "context_excerpt", "context_anchors", "decision_impact"):
            tradeoff.pop(key)
        record = copy.deepcopy(self.record)
        record["提示词"] += tradeoff["prompt_excerpt"]
        errors = self.errors(topic, record)
        self.assertTrue(any("实际代价" in error for error in errors))
        self.assertTrue(any("真实业务条件" in error for error in errors))
        self.assertTrue(any("源码依据" in error for error in errors))

    def test_tradeoff_context_must_reach_prompt_and_match_source(self):
        topic = copy.deepcopy(self.topic)
        tradeoff = topic["task_plan"][0]["difficulty_design"]["design_tradeoff"]
        tradeoff["context_excerpt"] = "系统需要处理百万条迟到事件并校正历史窗口"
        tradeoff["context_anchors"][0]["excerpt"] = "不存在的百万条迟到事件源码"
        errors = self.errors(topic)
        self.assertTrue(any("context_excerpt 不在最终题面" in error for error in errors))
        self.assertTrue(any("excerpt 不在声明源码行内" in error for error in errors))

    def test_20261115_platform_accepted_choice_phrase_is_recognized(self):
        excerpt = "两版结论冲突时由你决定采用哪份"
        self.assertTrue(difficulty_contract.OPEN_CHOICE_RE.search(excerpt))
        self.assertEqual(
            difficulty_contract._tradeoff_conflict_errors(
                "审计清单与详情记录产生两版结论。" + excerpt + "。",
                excerpt,
            ),
            [],
        )

    def test_same_axis_cannot_be_fixed_elsewhere(self):
        topic = copy.deepcopy(self.topic)
        design = topic["task_plan"][0]["difficulty_design"]
        design["design_tradeoff"].update({
            "decision": "历史订单检查点与当前订单检查点发生冲突时谁优先",
            "options": ["历史检查点优先", "当前检查点优先"],
            "prompt_excerpt": "历史检查点与当前检查点冲突时，由你判断优先级",
        })
        record = copy.deepcopy(self.record)
        record["提示词"] = (
            "请解析订单数据并生成已发布订单数据集。"
            "窗口选择冲突时，以最新有效检查点作为裁决依据，旧检查点继续保存原始范围。"
            "导出中断后保留上一份完整订单数据集，重试只补写缺失记录，"
            "已发布批次不得回退。"
            "分析导出中心必须读取同一份已发布订单数据集并保留来源批次。"
            "历史检查点与当前检查点冲突时，由你判断优先级。"
        )
        errors = self.errors(topic, record)
        self.assertTrue(any("又在另一句被固定为一种结果" in error for error in errors))

    def test_every_h3_option_must_preserve_all_h1_constraints(self):
        topic = copy.deepcopy(self.topic)
        analysis = topic["task_plan"][0]["difficulty_design"]["design_tradeoff"][
            "option_analysis"
        ]
        analysis[1]["preserves_constraint_ids"] = ["C1"]
        errors = self.errors(topic)
        self.assertTrue(
            any("没有覆盖全部 H1 约束" in error and "C2" in error for error in errors)
        )

    def test_new_config_fixed_branch_is_not_h3(self):
        topic = copy.deepcopy(self.topic)
        tradeoff = topic["task_plan"][0]["difficulty_design"]["design_tradeoff"]
        excerpt = (
            "检查点缓存中的旧配置与刚启用的状态后端配置冲突时，"
            "恢复写入侧只能读取一个可核对的状态后端配置版本数据，"
            "不能把旧状态大小写回当前记录。"
        )
        tradeoff["trigger"] = "检查点缓存中的旧配置与刚启用的状态后端配置冲突"
        tradeoff["context_excerpt"] = excerpt
        tradeoff["prompt_excerpt"] = excerpt
        record = copy.deepcopy(self.record)
        record["提示词"] = (
            "请解析订单数据并生成已发布订单数据集。"
            "导出中断后保留上一份完整订单数据集，重试只补写缺失记录，已发布批次不得回退。"
            "分析导出中心必须读取同一份已发布订单数据集并保留来源批次。"
            + excerpt
        )
        errors = self.errors(topic, record)
        self.assertTrue(any("已经预先选定分支" in error for error in errors))

    def test_protected_snapshot_only_branch_is_not_h3(self):
        topic = copy.deepcopy(self.topic)
        tradeoff = topic["task_plan"][0]["difficulty_design"]["design_tradeoff"]
        excerpt = (
            "未被快照清单引用的目录仍被过期检查点引用时，"
            "清理结果校验只能遵循一套最终确认的状态快照数据清单，"
            "不能把待恢复快照当成可删除文件，处理来源发生冲突。"
        )
        tradeoff["trigger"] = "未被快照清单引用的目录仍被过期检查点引用"
        tradeoff["context_excerpt"] = excerpt
        tradeoff["prompt_excerpt"] = excerpt
        record = copy.deepcopy(self.record)
        record["提示词"] = (
            "请解析订单数据并生成已发布订单数据集。"
            "导出中断后保留上一份完整订单数据集，重试只补写缺失记录，已发布批次不得回退。"
            "分析导出中心必须读取同一份已发布订单数据集并保留来源批次。"
            + excerpt
        )
        errors = self.errors(topic, record)
        self.assertTrue(any("已经预先选定分支" in error for error in errors))

    def test_completion_precedence_positive_sample_remains_open(self):
        excerpt = (
            "清理回执与恢复事件同时更新同一检查点时，作业处理时必须采用一套已确认的"
            "检查点记录版本数据，不能既把路径释放又准备新的恢复，这就是需要裁决的版本冲突。"
        )
        self.assertEqual(difficulty_contract._fixed_branch_evidence(excerpt), [])
        self.assertTrue(difficulty_contract._has_tradeoff_evidence(excerpt, excerpt))

    def test_workload_floor_rejects_three_slice_prompt(self):
        topic = copy.deepcopy(self.topic)
        workload = topic["task_plan"][0]["difficulty_design"]["workload_design"]
        workload["work_slices"] = workload["work_slices"][:3]
        errors = self.errors(topic)
        self.assertTrue(any("至少要有 4 个工作切片" in error for error in errors))

    def test_workload_needs_no_call_estimate(self):
        topic = copy.deepcopy(self.topic)
        workload = topic['task_plan'][0]['difficulty_design']['workload_design']
        workload.pop('expected_effective_tool_calls', None)
        workload.pop('call_floor_rationale', None)
        self.assertEqual(self.errors(topic), [])

    def test_source_only_h3_with_equivalent_outcomes_is_rejected(self):
        topic = copy.deepcopy(self.topic)
        tradeoff = topic["task_plan"][0]["difficulty_design"]["design_tradeoff"]
        tradeoff["options"] = ["以关闭请求触发停止", "以暂停回执确认停止"]
        analysis = tradeoff["option_analysis"]
        analysis[0].update({
            "option": "以关闭请求触发停止",
            "correctness_effect": "关闭请求到达后作业停止，后续offset不再推进",
            "observable_result": "作业实例显示已停止，消费位点保持不变",
        })
        analysis[1].update({
            "option": "以暂停回执确认停止",
            "correctness_effect": "暂停回执到达后作业停止，后续offset不再推进",
            "observable_result": "作业实例显示已停止，消费位点保持不变",
        })
        errors = self.errors(topic)
        self.assertTrue(any("只是来源或机制不同" in error for error in errors))

    def test_new_h3_false_guards_are_required(self):
        topic = copy.deepcopy(self.topic)
        guards = topic["task_plan"][0]["difficulty_design"]["design_tradeoff"][
            "false_h3_guards"
        ]
        guards.pop("source_selection_only")
        guards.pop("equivalent_user_outcome")
        errors = self.errors(topic)
        self.assertTrue(any("source_selection_only" in error for error in errors))
        self.assertTrue(any("equivalent_user_outcome" in error for error in errors))

    def test_difficulty_note_cannot_override_g16(self):
        from check_prompt_g16 import check

        prompt = "给台账加一个导出按钮：把当前列表另存成文件，导出失败要说清原因。"
        note = "难度复核: 多模块整合 + 关键设计取舍"
        result = check([("legacy", "功能开发", prompt)], {"legacy": note})
        self.assertFalse(result["ok"])
        self.assertEqual(result["justified"], [])


if __name__ == "__main__":
    unittest.main()
