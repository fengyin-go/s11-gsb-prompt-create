from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "preflight_batch.py"
SPEC = importlib.util.spec_from_file_location("preflight_batch", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class AggregatePreflightTest(unittest.TestCase):
    def test_overlay_keeps_frozen_type_and_marks_missing_seat(self) -> None:
        records = [{
            "子文件夹名称": "cc1-01-development",
            "任务类型": "功能开发",
            "提示词": "",
        }]
        merged, missing = MODULE.overlay_candidates(
            records,
            [
                {
                    "folder": "cc1-01-development",
                    "task_type": "功能开发",
                    "prompt": "新增采购单数据汇总。",
                },
                {
                    "folder": "cc1-02-bugfix",
                    "task_type": "Bugfix",
                    "prompt": "修复采购单数据缺失。",
                },
            ],
        )

        self.assertEqual(merged[0]["提示词"], "新增采购单数据汇总。")
        self.assertEqual(missing, ["cc1-02-bugfix"])

    def test_overlay_rejects_task_type_drift_and_duplicate_folders(self) -> None:
        records = [{
            "子文件夹名称": "cc1-01-development",
            "任务类型": "功能开发",
            "提示词": "",
        }]
        with self.assertRaisesRegex(ValueError, "不一致"):
            MODULE.overlay_candidates(
                records,
                [{
                    "folder": "cc1-01-development",
                    "task_type": "Bugfix",
                    "prompt": "修复错误。",
                }],
            )
        with self.assertRaisesRegex(ValueError, "重复"):
            MODULE.overlay_candidates(
                records,
                [
                    {
                        "folder": "cc1-01-development",
                        "prompt": "新增能力一。",
                    },
                    {
                        "folder": "cc1-01-development",
                        "prompt": "新增能力二。",
                    },
                ],
            )

    def test_one_candidate_reports_all_hit_gates_together(self) -> None:
        folder = "cc1-01-development"
        collector = MODULE.FindingCollector([folder])
        gate = {
            "problems": [
                f"{folder}[功能开发] 题面表达未通过：出现请求外壳",
                f"{folder}[功能开发] 自然度未通过：段落是验收拼装",
                f"{folder}[功能开发] 主题去重未通过：mode 重复",
            ],
            "prompt_style": {
                "problems": [f"{folder}[功能开发]：出现请求外壳"],
            },
            "prompt_naturalness": {
                "problems": [f"{folder}[功能开发]：段落是验收拼装"],
            },
            "domain": {
                "problems": [f"{folder}[功能开发]：未证明真实数据链路"],
            },
            "type_intent": {
                "problems": [f"{folder}[功能开发]：主工作不是新增能力"],
            },
            "difficulty_contract": {
                "problems": [f"{folder}[功能开发]：H3 两个方案结果等价"],
            },
            "theme": {
                "violations": [{
                    "kind": "mode_over_limit",
                    "labels": [f"{folder}[功能开发]"],
                }],
            },
            "difficulty": {
                "violations": [{
                    "label": f"{folder}[功能开发]",
                    "reasons": ["命中两项过于简单特征"],
                }],
            },
            "dedup": {
                "violations": [
                    {
                        "kind": "semantic_similarity",
                        "score": 0.4,
                        "left": f"{folder}[功能开发]",
                        "right": "cc1-02-bugfix[Bugfix]",
                    },
                    {
                        "kind": "semantic_identity_collision",
                        "score": 5,
                        "left": folder,
                        "right": "cc1-02-bugfix",
                    },
                ],
            },
        }

        MODULE._collect_write_gate(collector, gate)
        collector.add("g16", f"{folder}：缺少硬证据", candidate=folder)
        collector.add("g16_strict", f"{folder}：单一功能模块", candidate=folder)
        collector.add("cross_project", f"{folder} 与历史题骨架重复", candidate=folder)
        collector.add(
            "platform_forbidden",
            "命中平台雷同题库：待办清单",
            candidate=folder,
        )
        result = collector.result()

        self.assertFalse(result["ok"])
        expected = {
            "prompt_style",
            "prompt_naturalness",
            "domain",
            "type_intent",
            "difficulty_contract",
            "theme",
            "structural_difficulty",
            "semantic_intent",
            "batch_dedup",
            "g16",
            "g16_strict",
            "cross_project",
            "platform_forbidden",
        }
        self.assertTrue(expected.issubset(result["rule_counts"]))
        candidate_rules = {
            item["rule"]
            for item in result["candidates"][folder]["findings"]
        }
        self.assertTrue(expected.issubset(candidate_rules))


if __name__ == "__main__":
    unittest.main()
