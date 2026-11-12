from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "check_cross_project_prompts.py"
)
SPEC = importlib.util.spec_from_file_location("check_cross_project_prompts", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class CrossProjectPromptCheckTest(unittest.TestCase):
    def test_reused_overview_skeleton_is_rejected(self) -> None:
        candidate = {
            "label": "wx10002/codegen-1",
            "task_type": "功能开发",
            "prompt": (
                "请为光伏电站档案（光伏电站、电站编号）增加一个可独立进入的概览视图，"
                "集中呈现电站编号、电站名称、装机容量、并网日期以及运行电站、停运电站。"
                "页面需要同时服务列表和详情，空数据或读取失败时保留当前条件并说明原因；"
                "多次打开时，显示数量与当前记录不能冲突，也不能影响原有操作。"
            ),
        }
        peer = {
            "label": "wx10001/codegen-1",
            "task_type": "功能开发",
            "prompt": (
                "请为检测样品接收（检测样品、样品编号）增加一个可独立进入的概览视图，"
                "集中呈现样品编号、样品名称、委托单位、样品类型以及今日接收、待接收样品。"
                "页面需要同时服务列表和详情，空数据或读取失败时保留当前条件并说明原因；"
                "多次打开时，显示数量与当前记录不能冲突，也不能影响原有操作。"
            ),
        }

        result = MODULE.compare_prompts(candidate, peer)

        self.assertFalse(result["ok"])
        kinds = {item["kind"] for item in result["violations"]}
        self.assertIn("text_overlap", kinds)
        self.assertIn("long_fragment", kinds)
        self.assertIn("same_solution_shape", kinds)

    def test_rewritten_aggregate_panel_is_accepted(self) -> None:
        candidate = {
            "label": "wx10002/codegen-1",
            "task_type": "功能开发",
            "prompt": (
                "在光伏电站档案列表旁增加按所属区域和运维负责人汇总的负荷面板。"
                "面板给出每位负责人管理的电站数、并网运行数、停运维护数、总装机容量，"
                "点某位负责人后列表只保留其名下电站，清空该选择即恢复全部记录。"
                "名称或日期条件变化时汇总和列表必须同步，某区域没有值班负责人时显示明确说明，"
                "读取中断时保留上一次汇总并提示重试。"
            ),
        }
        peer = {
            "label": "wx10001/codegen-1",
            "task_type": "功能开发",
            "prompt": (
                "请为检测样品接收（检测样品、样品编号）增加一个可独立进入的概览视图，"
                "集中呈现样品编号、样品名称、委托单位、样品类型以及今日接收、待接收样品。"
                "页面需要同时服务列表和详情，空数据或读取失败时保留当前条件并说明原因；"
                "多次打开时，显示数量与当前记录不能冲突，也不能影响原有操作。"
            ),
        }

        result = MODULE.compare_prompts(candidate, peer)

        self.assertTrue(result["ok"], result["violations"])

    def test_platform_corpus_blocks_a5_low_similarity_template(self) -> None:
        shared_tail = (
            "流程。存量台账按业务日期回填，缺失的取值单独标注。"
            "另外，怎么统一由你决定，两种做法选哪种都可以。"
        )
        candidate = {
            "label": "1006229-bug-29",
            "task_type": "Bug修复",
            "prompt": "光伏电站档案接入流程" * 50 + shared_tail,
        }
        peer = {
            "label": "1006228-bug-28",
            "task_type": "Bug修复",
            "prompt": "入廊作业申请审批流程" * 50 + shared_tail,
        }

        result = MODULE.compare_platform_prompt(candidate, peer)

        self.assertFalse(result["ok"], result)
        self.assertLess(result["similarity"], MODULE.DEFAULT_PLATFORM_MAX_SIMILARITY)
        kinds = {item["kind"] for item in result["violations"]}
        self.assertIn("platform_common_fragment", kinds)
        self.assertNotIn("platform_text_similarity", kinds)

    def test_platform_corpus_loads_and_skips_self_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "platform-corpus.json"
            prompt = "给台账增加一条按编号去重的校验，重复提交只落一条。"
            path.write_text(json.dumps({
                "profile": "gsb-platform-prompt-corpus-v1",
                "account": "tester",
                "fetched_at": "2026-10-08T00:00:00+08:00",
                "items": [{
                    "id": 42,
                    "repo_id": "owner/100001-codegen-1",
                    "task_type": "功能开发",
                    "status": "QC_PASSED",
                    "prompt": prompt,
                }],
            }, ensure_ascii=False), encoding="utf-8")
            peers, meta = MODULE.load_platform_corpus(path)
            candidate = {
                "label": "100001-codegen-1",
                "task_type": "功能开发",
                "prompt": prompt,
            }

            result = MODULE.check_platform_corpus([candidate], peers)

        self.assertEqual(meta["record_count"], 1)
        self.assertTrue(result["ok"], result)

    def test_platform_forbidden_topic_blocks_todo_list(self) -> None:
        violations = MODULE.check_forbidden_topics([{
            "label": "100001-codegen-1",
            "task_type": "功能开发",
            "prompt": "把处理完的记录放回待办清单，运营概览也要跟着更新。",
        }])

        self.assertEqual(len(violations), 1)
        self.assertIn("雷同题库", violations[0]["message"])
        self.assertEqual(violations[0]["term"], "待办清单")

    def test_calendar_shape_is_not_bulk_list(self) -> None:
        candidate = {
            "label": "wx10002/codegen-2",
            "task_type": "功能开发",
            "prompt": (
                "把巡检计划从单张表格扩展成排班日历：同一巡检人员在同一日期出现多段路线时"
                "标出冲突，同一路线连续排给不同班组时给出提示，点击任一日期可只看当天任务。"
                "修改计划日期后日历和列表要同步，跨月浏览时当前筛选不能丢失，"
                "没有排班的日期显示为空，读取失败时说明原因并保留原日历。"
            ),
        }
        peer = {
            "label": "wx10001/codegen-2",
            "task_type": "功能开发",
            "prompt": (
                "在检测任务单（检测任务、任务编号）现有列表上做批量作业，"
                "支持多选后一次提交分配任务、开始检测、提交复核，每一条都要返回结果，"
                "不能因为其中一条失败就让其余记录悄悄丢失。还要处理重复提交、跨页选择和无数据，"
                "提交后重新查看或刷新时，清单、数量指标与操作状态需保持统一。"
            ),
        }

        result = MODULE.compare_prompts(candidate, peer)

        self.assertTrue(result["ok"], result["violations"])


if __name__ == "__main__":
    unittest.main()
