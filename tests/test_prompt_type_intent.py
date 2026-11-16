"""Task-type intent must be visible in the opening of each prompt."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_prompt_type_intent as intent
import batch_prompt_workbook as workbook


def plan(
    sequence: int,
    slug: str,
    task_type: str,
    main_task: str,
    main_request: str,
) -> dict:
    return {
        "sequence": sequence,
        "slug": slug,
        "task_type": task_type,
        "family": {
            "development": "新增能力",
            "bugfix": "Bugfix",
            "optimization": "优化既有能力",
        }[slug],
        "domain_fit": {
            "main_task": main_task,
            "prompt_main_request": main_request,
        },
    }


class PromptTypeIntent(unittest.TestCase):
    def test_natural_openings_preserve_all_five_type_intents(self):
        prompts = {
            "功能开发": "当前缺少订单明细文件，新增订单导出能力并生成明细文件。",
            "Bugfix": "订单筛选错误导致结果为空。修复字段匹配并恢复原有明细。",
            "功能优化": "现有订单导出重复扫描影响性能。优化增量处理链路。",
            "代码重构": "订单处理逻辑重构，输入输出不变。",
            "依赖升级与迁移": "订单解析依赖从旧版本升级到新版本并兼容存量文件。",
        }
        for index, (task_type, prompt) in enumerate(prompts.items(), 1):
            with self.subTest(task_type=task_type):
                slug = intent.SLUG_BY_TYPE[task_type]
                topic_map = {"task_plan": [{
                    "sequence": index, "slug": slug, "task_type": task_type,
                    "family": intent.TYPE_FAMILY_TERMS[task_type][0],
                    "domain_fit": {"main_task": prompt, "prompt_main_request": prompt},
                }]}
                self.assertEqual(intent.prompt_errors(
                    [self.row(index, slug, task_type, prompt)], topic_map,
                ), [])

    def setUp(self):
        self.topic_map = {
            "task_plan": [
                plan(
                    1,
                    "development",
                    "功能开发",
                    "新增经营指标数据处理能力；当前尚未形成可追溯的达成率数据，"
                    "读取经营指标源记录，计算达成率并生成达成率数据集",
                    "请新增经营指标的数据处理能力：当前尚未形成可追溯的达成率数据；"
                    "读取经营指标源记录，"
                    "计算达成率并生成达成率数据集，保证结果可按指标编号追溯。",
                ),
                plan(
                    2,
                    "bugfix",
                    "Bugfix",
                    "修复筛选失效导致结果为空，聚合差异并恢复差异数据集",
                    "请修复现有筛选入口按错误字段匹配导致结果为空；"
                    "读取科目记录，聚合差异并恢复差异数据集，保证结果可核对。",
                ),
                plan(
                    3,
                    "optimization",
                    "功能优化",
                    "优化现有差异数据集链路，重复全量扫描导致结果不稳定，"
                    "聚合差异并稳定生成差异数据集",
                    "请优化现有差异数据集链路：重复全量扫描导致结果不稳定；"
                    "读取科目记录，聚合差异并稳定生成差异数据集，保证结果可核对。",
                ),
            ]
        }

    def row(self, sequence: int, slug: str, task_type: str, prompt: str) -> dict:
        return {
            "子文件夹名称": f"demo-{sequence:02d}-{slug}",
            "任务类型": task_type,
            "提示词": prompt,
        }

    def test_valid_type_specific_prompts_pass(self):
        rows = [
            self.row(
                1,
                "development",
                "功能开发",
                self.topic_map["task_plan"][0]["domain_fit"]["prompt_main_request"]
                + "下游刷新任务消费同一份数据。",
            ),
            self.row(
                2,
                "bugfix",
                "Bugfix",
                self.topic_map["task_plan"][1]["domain_fit"]["prompt_main_request"]
                + "重复提交只生效一次。",
            ),
            self.row(
                3,
                "optimization",
                "功能优化",
                self.topic_map["task_plan"][2]["domain_fit"]["prompt_main_request"]
                + "失败后从最后成功位置继续。",
            ),
        ]
        self.assertEqual(intent.hint_errors(self.topic_map), [])
        self.assertEqual(intent.prompt_errors(rows, self.topic_map), [])

    def test_bugfix_written_as_feature_development_is_rejected(self):
        fit = self.topic_map["task_plan"][1]["domain_fit"]
        fit["main_task"] = "聚合差异并生成差异数据集"
        fit["prompt_main_request"] = "请读取科目记录，聚合差异并生成差异数据集。"
        row = self.row(2, "bugfix", "Bugfix", fit["prompt_main_request"] + "保证结果可核对。")
        errors = intent.prompt_errors([row], self.topic_map)
        self.assertTrue(any("必须" in error and "修复" in error for error in errors))
        self.assertTrue(any("Bugfix" in error and "缺陷" in error for error in errors))

    def test_bugfix_must_put_repair_at_the_absolute_start(self):
        fit = self.topic_map["task_plan"][1]["domain_fit"]
        prompt = "请读取科目记录并恢复差异数据集。请修复筛选入口按错误字段匹配导致结果为空；保证结果可核对。"
        errors = intent.prompt_errors(
            [self.row(2, "bugfix", "Bugfix", prompt)],
            self.topic_map,
        )
        self.assertTrue(any("原样开头" in error or "任务类型不能藏在后句" in error for error in errors))

    def test_optimization_must_target_existing_capability(self):
        fit = self.topic_map["task_plan"][2]["domain_fit"]
        fit["main_task"] = "新增差异数据集并聚合差异"
        fit["prompt_main_request"] = "请优化差异数据集生成速度。"
        row = self.row(3, "optimization", "功能优化", fit["prompt_main_request"])
        errors = intent.prompt_errors([row], self.topic_map)
        self.assertTrue(any("现有或已有能力" in error for error in errors))
        self.assertTrue(any("main_task" in error for error in errors))

    def test_development_cannot_hide_new_capability_behind_data_processing_wording(self):
        fit = self.topic_map["task_plan"][0]["domain_fit"]
        fit["main_task"] = "读取经营指标源记录并生成达成率数据集"
        fit["prompt_main_request"] = "请读取经营指标源记录并生成达成率数据集。"
        row = self.row(
            1,
            "development",
            "功能开发",
            fit["prompt_main_request"] + "当前已有达成率数据。",
        )
        errors = intent.prompt_errors([row], self.topic_map)
        self.assertTrue(any("必须" in error and "新增" in error for error in errors))
        self.assertTrue(any("新增能力" in error or "修复或优化" in error for error in errors))

    def test_new_capability_must_state_the_missing_baseline(self):
        fit = self.topic_map["task_plan"][0]["domain_fit"]
        fit["main_task"] = "新增经营指标达成率数据处理能力"
        fit["prompt_main_request"] = "请新增经营指标达成率数据处理能力并生成达成率数据集。"
        row = self.row(
            1,
            "development",
            "功能开发",
            fit["prompt_main_request"] + "下游刷新任务消费该结果。",
        )
        errors = intent.prompt_errors([row], self.topic_map)
        self.assertTrue(any("当前能力尚未形成" in error for error in errors))

    def test_optimization_needs_existing_baseline_and_improvement_target(self):
        fit = self.topic_map["task_plan"][2]["domain_fit"]
        fit["main_task"] = "优化现有差异数据集链路"
        fit["prompt_main_request"] = "请优化差异数据集链路并生成差异数据集。"
        row = self.row(3, "optimization", "功能优化", fit["prompt_main_request"])
        errors = intent.prompt_errors([row], self.topic_map)
        self.assertTrue(any("现有或已有能力" in error for error in errors))
        self.assertTrue(any("优化目标" in error for error in errors))

    def test_family_must_match_declared_type(self):
        self.topic_map["task_plan"][1]["family"] = "新增能力"
        errors = intent.hint_errors(self.topic_map)
        self.assertTrue(any("family" in error and "不一致" in error for error in errors))

    def test_plan_and_workbook_type_mismatch_is_rejected(self):
        row = self.row(
            2,
            "bugfix",
            "功能开发",
            self.topic_map["task_plan"][1]["domain_fit"]["prompt_main_request"],
        )
        errors = intent.prompt_errors([row], self.topic_map)
        self.assertTrue(any("工作簿任务类型" in error for error in errors))

    def test_workbook_write_gate_surfaces_type_intent_failure(self):
        fit = self.topic_map["task_plan"][1]["domain_fit"]
        row = self.row(
            2,
            "bugfix",
            "Bugfix",
            "请读取科目记录并生成差异数据集，当前尚未形成可核对结果。",
        )
        with tempfile.TemporaryDirectory(prefix="s11-type-gate-") as temp:
            with (
                patch("check_repo_theme.check", return_value={"ok": True}),
                patch("check_prompt_difficulty.check", return_value={"ok": True}),
                patch("check_prompt_dedup.check", return_value={"ok": True}),
            ):
                gate = workbook.run_write_gates(
                    Path(temp),
                    [row],
                    repo=None,
                    topic_map=self.topic_map,
                )
        self.assertFalse(gate["ok"])
        self.assertFalse(gate["type_intent"]["ok"])
        self.assertTrue(
            any("任务类型语义未通过" in problem for problem in gate["problems"])
        )
        self.assertTrue(fit["prompt_main_request"].startswith("请修复"))


if __name__ == "__main__":
    unittest.main()
