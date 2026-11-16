"""Naturalness regression for coherent S11 prompt prose."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_prompt_naturalness as naturalness


GOOD_SAMPLING = (
    "只复核高分票据会漏估低分异常。请增加分层随机抽检，绑定训练版本的待标注全集，"
    "各分层抽取数量可设，保存每票入选概率与抽样依据。人工结论汇入抽检后，"
    "按入选概率校正总体混淆矩阵和异常率，未结案或空抽分层明确提示无法估计，"
    "不把未结案当正常。结论更正触发重算，旧结果及其依据仍可回看，重试不重复累计。"
    "结案与重算并发时只发布对应同一批结论的完整结果，失败后恢复沿用原抽样。"
    "页面可发起抽检、填写结论、查看进度和下载明细。用可枚举小总体核对校正估计，"
    "覆盖更正与重算竞争。"
)
GOOD_CLASS_MERGE = (
    "班级改名和合班后，日报仍用旧代码，汇总把同一归属拆开。请增加按日期生效的归并方案，"
    "支持连续改名，拒绝环路及同日多归属。每个原班级日事实只归入一个最终班级，"
    "同日同授课方式的事实数据先聚合人次再计算表达式，方式冲突报出来源。"
    "方案从草稿到发布，发布中断可续跑，重试不重复合计。发布与事实更正并发时，"
    "试算只采用发布前或发布后的完整归属和事实。历史快照及导出保留原依据，"
    "新试算冻结归并链并可独立复算。页面可编辑生效区间、预览冲突和发布。"
)


def record(label: str, prompt: str) -> dict[str, str]:
    return {"子文件夹名称": label, "任务类型": "功能开发", "提示词": prompt}


class NaturalnessTest(unittest.TestCase):
    def test_production_reference_prompts_are_accepted(self):
        result = naturalness.check([
            record("sampling", GOOD_SAMPLING),
            record("class-merge", GOOD_CLASS_MERGE),
        ])
        self.assertTrue(result["ok"], result)

    def test_mechanical_cross_module_appendix_is_rejected(self):
        prompt = GOOD_SAMPLING + (
            "另一个业务模块的抽样清单必须同步生成总体异常率台账，并保持同一份抽样依据。"
        )
        result = naturalness.check([record("bad", prompt)])
        self.assertFalse(result["ok"])
        self.assertTrue(any("通用跨模块追加句" in error for error in result["errors"]))

    def test_repeated_sentence_across_batch_is_rejected(self):
        repeated = "同一批次并发提交时只有一次写入成功，重复请求必须拒绝。"
        rows = [record(f"task-{index}", GOOD_SAMPLING + repeated) for index in range(5)]
        result = naturalness.check(rows)
        self.assertFalse(result["ok"])
        self.assertTrue(result["duplicate_sentences"])

    def test_condition_checklist_is_rejected(self):
        prompt = (
            "当前抽检结果无法校正总体异常率，需要增加分层抽样。"
            "必须读取全集。必须保存权重。必须重算结果。必须同步仓储。"
            "必须拒绝重复提交。必须保留历史版本。必须恢复失败任务。必须发布完整结果。"
        )
        result = naturalness.check([record("checklist", prompt)])
        self.assertFalse(result["ok"])

    def test_generic_second_paragraph_is_rejected(self):
        prompt = GOOD_SAMPLING + (
            "\n\n另一个业务模块的抽样台账必须同步生成总体异常率台账，"
            "并让抽样依据仍可回查。"
        )
        result = naturalness.check([record("appendix", prompt)])
        self.assertFalse(result["ok"])
        self.assertTrue(any("通用跨模块套话" in error for error in result["errors"]))

    def test_explicit_design_decision_is_rejected(self):
        prompt = GOOD_SAMPLING + (
            "历史抽样口径和新结果冲突时需要判定沿用旧分层，还是按新全集重建。"
        )
        result = naturalness.check([record("decision", prompt)])
        self.assertFalse(result["ok"])
        self.assertTrue(any("显式设计裁决指令" in error for error in result["errors"]))

    def test_batch_boilerplate_phrase_is_rejected(self):
        rows = [
            record(f"task-{index}", GOOD_SAMPLING + "抽样依据仍可回查。")
            for index in range(4)
        ]
        result = naturalness.check(rows)
        self.assertFalse(result["ok"])
        self.assertTrue(result["repeated_boilerplate"])


if __name__ == "__main__":
    unittest.main()
