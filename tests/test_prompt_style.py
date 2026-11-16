"""Tests for direct-requirement prompt wording."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import batch_prompt_workbook as workbook
import prepare_handoff
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
            "当前尚未形成订单导出能力，新增可核对的订单明细文件。",
            "订单筛选后返回空页，修复错误字段匹配并恢复明细。",
            "现有订单导出重复扫描，优化增量处理性能。",
            "订单处理逻辑需要重构，保持输入输出不变。",
            "订单解析依赖升级到目标版本，兼容已有数据格式。",
            "只依据当前订单版本计算金额，不读取已归档订单。",
            "仅限使用已确认的订单计算本月销售额。",
            "不得读取其他租户的订单数据。",
            "刷新任务读取经营指标的完整计算结果，失败时继续使用上一批成功结果。",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(prompt_style.prompt_errors([self.record(prompt)]), [])

    def test_design_delegation_is_not_a_business_requirement(self):
        for prompt in (
            "两版口径由你权衡优先项。", "恢复边界请自行选择。",
            "来源优先级由设计者决定。", "全量或增量均可，结果正确即可。",
        ):
            with self.subTest(prompt=prompt):
                self.assertTrue(any("显式委托设计取舍" in error for error in
                                    prompt_style.prompt_errors([self.record(prompt)])))
        self.assertEqual(prompt_style.prompt_errors([self.record(
            "同一字段有两个来源口径，发布结果应记录冲突项与采用依据，下游读取同一冻结版本。"
        )]), [])

    def test_rejects_context_restrictions_not_business_boundaries(self):
        prompts = (
            "只依据当前仓库、本轮题面和实际运行结果完成任务，不读取其他会话、答案笔记或历史对话缓存。",
            "仅基于当前仓库完成开发。",
            "只允许使用当前工作区的信息。",
            "不得参考其他会话中的答案。",
            "不要使用答案笔记或历史对话。",
            "禁止跨会话记忆。",
            "Only use the current repository to implement this feature.",
            "Do not read other sessions or answer notes.",
            "仅限使用本项目代码完成开发。",
            "只能根据本仓库的信息完成修改。",
            "不要参考之前的对话。",
            "忽略历史会话中的结论。",
            "上下文隔离：使用本地代码完成开发。",
            "当前仓库是唯一信息来源。",
            "不得借助外部上下文或历史答案。",
            "仅以当前代码为准完成修改。",
            "Use only the current workspace for this task.",
            "Ignore previous conversations when implementing this feature.",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertTrue(any("上下文来源限制" in error for error in prompt_style.prompt_errors([self.record(prompt)])))

    def test_rejects_vague_scaffold_instead_of_real_business_requirements(self):
        for phrase in (
            "结果要关联另一个入口，同时保留后续取数所需的状态。",
            "结果数据集遇到两版来源时如何取舍，由你决定。",
        ):
            with self.subTest(phrase=phrase):
                self.assertTrue(any(
                    "空泛模板拼接" in error
                    for error in prompt_style.prompt_errors([self.record(phrase)])
                ))

    def test_write_gate_rejects_context_statement(self):
        with tempfile.TemporaryDirectory(prefix="s11-context-style-") as temp:
            with (
                patch("check_repo_theme.check", return_value={"ok": True}),
                patch("check_prompt_difficulty.check", return_value={"ok": True}),
                patch("check_prompt_dedup.check", return_value={"ok": True}),
            ):
                gate = workbook.run_write_gates(
                    Path(temp), [self.record("新增订单导出。不得读取其他会话的答案笔记。")],
                    repo=None, topic_map={},
                )
        self.assertFalse(gate["ok"])
        self.assertFalse(gate["prompt_style"]["ok"])

    def test_write_and_handoff_gates_reject_paraphrases(self):
        for tail in ("仅限使用本项目代码完成开发。", "忽略历史会话。", "结果要关联另一个入口。"):
            with self.subTest(tail=tail), tempfile.TemporaryDirectory(prefix="s11-style-gates-") as temp:
                parent = Path(temp)
                rows = [self.record("新增订单导出。" + tail)]
                with (
                    patch("check_repo_theme.check", return_value={"ok": True}),
                    patch("check_prompt_difficulty.check", return_value={"ok": True}),
                    patch("check_prompt_dedup.check", return_value={"ok": True}),
                ):
                    self.assertFalse(workbook.run_write_gates(
                        parent, rows, repo=None, topic_map={},
                    )["prompt_style"]["ok"])
                with patch("check_prompt_g16.check", return_value={"ok": True}):
                    with self.assertRaisesRegex(ValueError, "题面表达闸门未通过"):
                        prepare_handoff.validate_generation(parent, rows, parent / "origin")

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
