"""Preview admission must not silently omit domain or difficulty checks."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_prompt_preview as preview


class PreviewAdmissionTest(unittest.TestCase):
    def test_missing_contracts_reject_the_previous_two_sample_shapes(self):
        for slug, task_type, prompt in (
            ("development", "功能开发", "当前没有达成率计算，新增当前值除以目标值的计算。全量或增量由你选择。"),
            ("bugfix", "Bugfix", "导出返回 422，修复路由匹配和参数映射，恢复已有筛选。"),
        ):
            with self.subTest(task_type=task_type):
                result = preview.check({
                    "records": [{"子文件夹名称": f"demo-01-{slug}", "任务类型": task_type, "提示词": prompt}],
                    "topic_map": {"domain": "数据工程", "difficulty_contract_version": 3,
                                  "task_plan": [{"sequence": 1, "slug": slug, "task_type": task_type,
                                                 "domain_fit": {"prompt_main_request": prompt}}]},
                }, Path.cwd())
                self.assertFalse(result["ok"])
                self.assertFalse(result["stages"]["domain"]["ok"])
                self.assertFalse(result["stages"]["difficulty"]["ok"])

    def test_each_stage_is_required_even_when_other_stages_pass(self):
        candidate = {"records": [{"提示词": "新增数据处理能力。"}], "topic_map": {}}
        targets = [
            "h3_selection.selection_errors",
            "prompt_style.prompt_errors",
            "check_prompt_naturalness.check",
            "check_prompt_type_intent.prompt_errors",
            "domain_fit.map_errors",
            "domain_fit.prompt_errors",
            "difficulty_contract.prompt_errors",
        ]
        from contextlib import ExitStack
        for failing in targets:
            with self.subTest(failing=failing), ExitStack() as stack:
                mocks = []
                for name in targets:
                    module, attr = name.split(".")
                    if name == "check_prompt_naturalness.check":
                        value = (
                            {"ok": False, "version": 1, "errors": ["blocked"]}
                            if name == failing
                            else {"ok": True, "version": 1, "errors": []}
                        )
                    else:
                        value = ["blocked"] if name == failing else []
                    mocks.append(stack.enter_context(
                        patch.object(getattr(preview, module), attr, return_value=value)
                    ))
                result = preview.check(candidate, Path.cwd())
                self.assertFalse(result["ok"])
                for mock in mocks:
                    mock.assert_called_once()

    def test_preview_requires_nonempty_text_and_topic_map(self):
        for candidate in ({}, {"records": [{"提示词": "候选题面"}]},
                          {"records": [{"提示词": ""}], "topic_map": {}}):
            self.assertFalse(preview.check(candidate, Path.cwd())["ok"])


if __name__ == "__main__":
    unittest.main()
