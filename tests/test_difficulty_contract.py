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
            "恢复边界请根据现有数据语义自行设计，但下游不得看到半成品。"
        )
        self.design = {
            "version": 2,
            "reasoning_challenge": {
                "constraints": [
                    {
                        "kind": "transaction_consistency",
                        "statement": "订单明细与来源批次必须同时可见",
                        "failure_if_omitted": "下游会读到没有来源的明细",
                    },
                    {
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
            "design_tradeoff": {
                "decision": "恢复边界按采集时间还是业务时间确定",
                "options": ["按采集时间恢复", "按业务时间恢复"],
                "outcome_invariant": "恢复完成后下游只能看到完整订单数据集",
                "why_open": "两种边界都可行，题面只固定可观察结果，不选择内部键。",
                "context_excerpt": "导出中断后保留上一份完整订单数据集，重试只补写缺失记录",
                "context_anchors": [{"path": "src/orders.py", "start_line": 2, "end_line": 2,
                                     "excerpt": "解析订单数据并生成已发布订单数据集"}],
                "option_analysis": [
                    {"option": "按采集时间恢复", "benefit": "顺序读取采集批次可以减少历史扫描范围",
                     "cost": "迟到订单需另行补算否则会遗漏业务窗口",
                     "viable_when": "采集顺序稳定且存在迟到记录补偿路径"},
                    {"option": "按业务时间恢复", "benefit": "直接覆盖受影响的业务窗口便于核对结果",
                     "cost": "历史更正会扩大重新扫描与去重处理范围",
                     "viable_when": "业务时间可靠且可以承担历史窗口重算成本"},
                ],
                "decision_impact": "恢复时间轴改变迟到订单的补算范围和扫描成本",
                "implementation_not_prescribed": True,
                "prompt_excerpt": (
                    "恢复边界请根据现有数据语义自行设计，但下游不得看到半成品。"
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
            "difficulty_contract_version": 2,
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
                    "difficulty_contract_version": 2,
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
            "恢复边界请根据现有数据语义自行设计", "恢复边界采用固定实现"
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
        tradeoff["prompt_excerpt"] = excerpt
        tradeoff["context_excerpt"] = excerpt
        record = copy.deepcopy(self.record)
        record["提示词"] = record["提示词"].replace(
            "恢复边界请根据现有数据语义自行设计，但下游不得看到半成品。", excerpt + "。"
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

    def test_difficulty_note_cannot_override_g16(self):
        from check_prompt_g16 import check

        prompt = "给台账加一个导出按钮：把当前列表另存成文件，导出失败要说清原因。"
        note = "难度复核: 多模块整合 + 关键设计取舍"
        result = check([("legacy", "功能开发", prompt)], {"legacy": note})
        self.assertFalse(result["ok"])
        self.assertEqual(result["justified"], [])


if __name__ == "__main__":
    unittest.main()
