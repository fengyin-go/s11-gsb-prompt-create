"""Task-type intent must be visible in the opening of each prompt."""

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_prompt_type_intent as intent


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
        "domain_fit": {
            "main_task": main_task,
            "prompt_main_request": main_request,
        },
    }


class PromptTypeIntent(unittest.TestCase):
    def setUp(self):
        self.topic_map = {
            "task_plan": [
                plan(
                    1,
                    "development",
                    "功能开发",
                    "新增经营指标数据处理能力，计算达成率并生成达成率数据集",
                    "请新增经营指标的数据处理能力：读取经营指标源记录，"
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
                    "优化现有差异数据集链路，聚合差异并稳定生成差异数据集",
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

    def test_plan_and_workbook_type_mismatch_is_rejected(self):
        row = self.row(
            2,
            "bugfix",
            "功能开发",
            self.topic_map["task_plan"][1]["domain_fit"]["prompt_main_request"],
        )
        errors = intent.prompt_errors([row], self.topic_map)
        self.assertTrue(any("工作簿任务类型" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
