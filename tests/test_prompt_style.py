"""Tests for direct-requirement prompt wording."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import batch_prompt_workbook as workbook
import prompt_style


class PromptStyleTest(unittest.TestCase):
    def record(self, prompt: str) -> dict[str, str]:
        return {
            "子文件夹名称": "orders-01-development",
            "任务类型": "功能开发",
            "提示词": prompt,
        }

    def test_rejects_request_narration_wrappers(self):
        cases = (
            "本需求需要新增订单导出。",
            "这次需求需要新增订单导出。",
            "本次任务要实现订单导出。",
            "这个需求需要保证订单字段完整。",
            "这次我需要把订单导出到文件。",
            "我需要实现订单导出。",
            "请你帮我增加订单导出。",
        )
        for prompt in cases:
            with self.subTest(prompt=prompt):
                self.assertTrue(prompt_style.prompt_errors([self.record(prompt)]))

    def test_allows_direct_requirement_and_business_time_language(self):
        prompts = (
            "请读取订单记录并导出订单明细文件，保证字段完整。",
            "请只处理本次结算周期内已确认的订单记录。",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(prompt_style.prompt_errors([self.record(prompt)]), [])

    def test_write_gate_surfaces_prompt_style_failure(self):
        with tempfile.TemporaryDirectory(prefix="s11-prompt-style-") as temp:
            parent = Path(temp)
            with (
                patch("check_repo_theme.check", return_value={"ok": True}),
                patch("check_prompt_difficulty.check", return_value={"ok": True}),
                patch("check_prompt_dedup.check", return_value={"ok": True}),
            ):
                gate = workbook.run_write_gates(
                    parent,
                    [self.record("这次我需要新增订单导出。")],
                    repo=None,
                    topic_map={},
                )
        self.assertFalse(gate["ok"])
        self.assertFalse(gate["prompt_style"]["ok"])
        self.assertTrue(any("题面表达未通过" in item for item in gate["problems"]))


if __name__ == "__main__":
    unittest.main()
