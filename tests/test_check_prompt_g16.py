from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "check_prompt_g16.py"
SPEC = importlib.util.spec_from_file_location("check_prompt_g16", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)

CORPUS = json.loads((HERE / "fixtures" / "g16-corpus.json").read_text(encoding="utf-8"))
BY_NAME = {case["子文件夹名称"]: case for case in CORPUS["cases"]}
PLATFORM_REJECTIONS = json.loads(
    (HERE / "fixtures" / "platform-g16-g19-rejections-20261007.json").read_text(
        encoding="utf-8"
    )
)["cases"]
# 2026-09-29 那批语料主体（cc10016 / cc10017）；2026-10-01 又补进 cc10018 / cc10019 的真实回执。
LEGACY = [c for c in CORPUS["cases"] if c["项目"] in ("cc10016", "cc10017")]
NEW_20261001 = [c for c in CORPUS["cases"] if c["项目"] in ("cc10018", "cc10019")]
DISCARDED = [c for c in LEGACY if c["平台结论"].startswith("废弃")]
PASSED = [c for c in LEGACY if c["平台结论"] == "质检通过"]
G16_DISCARDED_NEW = [c for c in NEW_20261001 if c["平台结论"] == "废弃 G16"]
G19_DISCARDED = [c for c in NEW_20261001 if c["平台结论"] == "废弃 G19"]
G18_DISCARDED = [c for c in NEW_20261001 if c["平台结论"] == "废弃 G18"]
# 2026-09-30 曾放宽、2026-10-01 撤回的两类：语料里这 6 条必须重新被 G16 拦下。
BUG_FEATURE_DISCARDS = [
    c for c in DISCARDED if c["任务类型"] in ("Bugfix", "功能优化")
]

# 旧「三项特征计数」规则本来就拦、平台却判通过的 13 条：闸门上线之前写好、后来补跑复检时
# 才过闸门的题面。硬证据层不许再新增任何一条。
COUNT_RULE_OVER_BLOCKS = {
    "100165-codegen-5",
    "100166-feature-6",
    "100169-feature-9",
    "1001615-codegen-15",
    "1001618-bug-18",
    "1001619-bug-19",
    "1001620-bug-20",
    "1001625-bug-25",
    "1001626-bug-26",
    "1001627-bug-27",
    "1001628-bug-28",
    "1001629-bug-29",
    "1001630-bug-30",
}
# 2026-10-01 撤回放宽后，被拦的通过题回到这 13 条（与闸门上线时一致）：不再新增。
COUNT_RULE_OVER_BLOCKS_NOW = COUNT_RULE_OVER_BLOCKS


class PromptDifficultyFloorTest(unittest.TestCase):
    """用平台真实回执标定下限闸门。

    语料分两批：cc10016 / cc10017 在 2026-09-29 难度新规则下的 15 条废弃 + 33 条通过，
    以及 2026-10-01 补进的 cc10018 / cc10019 真实回执（1 条废弃 G16、2 条废弃 G19、
    1 条废弃 G18）。断言要同时守住两个方向——废弃的一条都不能再放行，通过的一条都不能
    被硬证据层新增拦住。
    """

    def test_legacy_g16_discards_are_all_blocked(self) -> None:
        self.assertEqual(len(DISCARDED), 15)
        for case in DISCARDED:
            with self.subTest(case=case["子文件夹名称"]):
                verdict = MODULE.evaluate(case["提示词"], case["任务类型"])
                self.assertFalse(
                    verdict["g16_ok"],
                    f"{case['项目']}#{case['编号']}（平台 {case['平台结论']}）没被拦下："
                    f"特征={list(verdict['strong_features'])}，硬证据={verdict['hard_features']}",
                )

    def test_word_gate_cannot_catch_the_single_module_codegen_case(self) -> None:
        """已知词面盲区：cc10019 #1 的「单一预案库」形状过得了 G16 词面闸。

        平台判词是「单一预案库功能…无架构取舍」，靠的是语义；词面读到的
        「预案列表与概览的条数要一致」正好命中弱证据，所以这条**不能**靠 G16 词面拦住。
        它由 `--strict` 下的弱证据提示兜底（见
        `test_weak_overview_consistency_is_flagged_as_weak_integration`），
        出题侧的真手段是 SKILL.md 的多模块纪律。这里把「词面拦不住」这个事实固定下来，
        免得以后误以为 G16 已经覆盖了它。
        """
        case = G16_DISCARDED_NEW[0]
        verdict = MODULE.evaluate(case["提示词"], case["任务类型"])
        self.assertTrue(verdict["g16_ok"])
        strict = MODULE.check(
            [(case["子文件夹名称"], case["任务类型"], case["提示词"])], strict=True
        )
        self.assertFalse(strict["ok"])

    def test_bug_and_feature_are_enforced_again(self) -> None:
        """2026-10-01 撤回 2026-09-30 的放宽：Bug修复 / feature迭代 重新过 G16 困难下限。"""
        self.assertEqual(len(BUG_FEATURE_DISCARDS), 6)
        for case in BUG_FEATURE_DISCARDS:
            with self.subTest(case=case["子文件夹名称"]):
                verdict = MODULE.evaluate(case["提示词"], case["任务类型"])
                self.assertFalse(verdict["g16_ok"])
                self.assertTrue(verdict["g16_enforced"])
                self.assertEqual(verdict["difficulty_policy"], "hard-floor")
                self.assertTrue(verdict["reasons"])
        # Bugfix按判据仍然三项全备，功能优化与普通类型一样两项。
        self.assertEqual(MODULE.required_feature_count("Bugfix"), 3)
        self.assertEqual(MODULE.required_feature_count("功能优化"), 2)

    def test_g19_medium_floor_flags_iteration_and_repair(self) -> None:
        """2026-10-01 平台新规则 G19：迭代 / 修复题的中等三项要全备，缺项要被点名。"""
        self.assertEqual(len(G19_DISCARDED), 2)
        for case in G19_DISCARDED:
            with self.subTest(case=case["子文件夹名称"]):
                verdict = MODULE.evaluate(case["提示词"], case["任务类型"])
                self.assertTrue(verdict["g19_applies"])
                self.assertFalse(verdict["g19_ok"])
                self.assertEqual(verdict["g19_required_features"], 3)
                self.assertTrue(verdict["g19_missing_features"])
                self.assertIn("实现决策", verdict["g19_missing_features"])
                self.assertTrue(
                    any("G19" in item for item in verdict["hints"]),
                    "G19 缺项要进 hints 供人工复核",
                )
        # 非迭代 / 修复类型不受 G19 约束
        self.assertFalse(MODULE.evaluate("随便一段题面", "功能开发")["g19_applies"])

    def test_g19_and_single_module_are_blocking_under_strict(self) -> None:
        for case in G19_DISCARDED:
            with self.subTest(case=case["子文件夹名称"]):
                loose = MODULE.check([(case["子文件夹名称"], case["任务类型"], case["提示词"])])
                strict = MODULE.check(
                    [(case["子文件夹名称"], case["任务类型"], case["提示词"])], strict=True
                )
                self.assertFalse(loose["ok"])
                self.assertFalse(strict["ok"])
                self.assertIn("G19", loose["items"][0]["violation_kinds"])
                self.assertIn("strict", strict["items"][0]["violation_kinds"])

    def test_g19_hard_block_cannot_be_released_by_a_difficulty_note(self) -> None:
        case = G19_DISCARDED[0]
        items = [(case["子文件夹名称"], case["任务类型"], case["提示词"])]
        note = "难度复核: 调用与数据流理解 + 实现决策"
        result = MODULE.check(items, {case["子文件夹名称"]: note}, strict=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["justified"], [])
        self.assertIn("负向硬闸", result["items"][0]["justification_issue"])

    def test_platform_false_negatives_are_now_hard_blocked(self) -> None:
        """2026-10-07 平台真实回执中，当前正向词面闸漏掉的 4 条 G16 + 2 条 G19。"""
        items = [
            (str(case["平台记录id"]), case["任务类型"], case["提示词"])
            for case in PLATFORM_REJECTIONS
        ]
        result = MODULE.check(items)
        self.assertFalse(result["ok"])
        self.assertEqual(result["g16_violation_count"], 4)
        self.assertEqual(result["g19_hard_violation_count"], 2)
        self.assertEqual(result["platform_learned_violation_count"], 6)
        by_id = {row["label"]: row for row in result["items"]}
        for record_id in ("22772", "23064", "28797", "29446"):
            self.assertIn("G16", by_id[record_id]["violation_kinds"])
        for record_id in ("20806", "21834"):
            self.assertIn("G19", by_id[record_id]["violation_kinds"])

    def test_g18_discard_is_orthogonal_to_the_difficulty_gate(self) -> None:
        """G18（当日两侧满分占比超限）与题面难度不是一回事：难度闸不因它拦人。"""
        self.assertEqual(len(G18_DISCARDED), 1)
        case = G18_DISCARDED[0]
        verdict = MODULE.evaluate(case["提示词"], case["任务类型"])
        self.assertTrue(verdict["g16_ok"])
        notes = " ".join(MODULE.check([(case["子文件夹名称"], case["任务类型"], case["提示词"])])["notes"])
        self.assertIn("G18", notes)

    def test_single_module_prompt_gets_a_multi_module_hint(self) -> None:
        single = (
            "给检验单加一个导出按钮：把当前列表另存成文件，导出失败要说清原因。"
        )
        verdict = MODULE.evaluate(single, "依赖升级与迁移")
        self.assertFalse(verdict["multi_module"])
        self.assertTrue(any("功能模块" in item for item in verdict["hints"]))

    def test_weak_overview_consistency_is_flagged_as_weak_integration(self) -> None:
        """cc10019 #1 的形状：只有「列表与概览条数一致」撑着多模块整合，要给提示。"""
        case = G16_DISCARDED_NEW[0]
        verdict = MODULE.evaluate(case["提示词"], case["任务类型"])
        self.assertTrue(verdict["multi_module"])
        self.assertTrue(
            any("弱证据" in item for item in verdict["hints"]),
            f"平台判废的「概览一致」形状要给弱证据提示：{verdict['hints']}",
        )

    def test_blocked_passed_prompts_are_only_the_known_count_rule_over_blocks(self) -> None:
        blocked = {
            case["子文件夹名称"]
            for case in PASSED
            if not MODULE.evaluate(case["提示词"], case["任务类型"])["g16_ok"]
        }
        new_blocks = blocked - COUNT_RULE_OVER_BLOCKS_NOW
        self.assertFalse(
            new_blocks,
            f"硬证据层新拦住了平台判通过的题面：{sorted(new_blocks)}",
        )
        # 旧过拦被修掉时也要显式更新这份名单，避免悄悄漂移。
        self.assertEqual(blocked, COUNT_RULE_OVER_BLOCKS_NOW)
        self.assertTrue(COUNT_RULE_OVER_BLOCKS_NOW <= COUNT_RULE_OVER_BLOCKS)

    def test_discarded_ones_hit_the_named_medium_shapes(self) -> None:
        shapes = {
            name: MODULE.evaluate(case["提示词"], case["任务类型"])["medium_shape"]
            for name, case in BY_NAME.items()
        }
        # #2/#3 是「新增实体的常规登记 + 列表/校验/批量/状态保持」，#31 是「跨调用点收拢」
        self.assertTrue(shapes["100162-codegen-2"] and "新增实体" in shapes["100162-codegen-2"][0])
        self.assertTrue(shapes["100163-codegen-3"] and "新增实体" in shapes["100163-codegen-3"][0])
        self.assertTrue(
            shapes["1001631-refactor-31"] and "跨调用点" in shapes["1001631-refactor-31"][0]
        )

    def test_discarded_ones_have_no_hard_evidence(self) -> None:
        """15 条废弃的共同点：三项特征里一项硬证据都没有。"""
        for case in DISCARDED:
            with self.subTest(case=case["子文件夹名称"]):
                verdict = MODULE.evaluate(case["提示词"], case["任务类型"])
                self.assertEqual(
                    verdict["hard_features"],
                    [],
                    f"{case['项目']}#{case['编号']} 竟然读出了硬证据：{verdict['hard_features']}",
                )

    def test_justified_note_downgrades_violation_to_trace(self) -> None:
        case = DISCARDED[0]
        items = [(case["子文件夹名称"], case["任务类型"], case["提示词"])]

        blocked = MODULE.check(items)
        justified = MODULE.check(
            items, {case["子文件夹名称"]: "难度复核: 多模块整合 + 关键设计取舍"}
        )

        self.assertFalse(blocked["ok"])
        self.assertTrue(justified["ok"])
        self.assertEqual(len(justified["justified"]), 1)

    def test_bugfix_and_feature_face_the_same_floor_as_general_types(self) -> None:
        """2026-10-01 起 Bug修复 / feature迭代 与普通类型一样过 G16 困难下限。"""
        prompt = (
            "请修复库存看板上的条数与库存列表保持一致；"
            "历史记录仍按当时的判定口径保留；"
            "同一批数据重复送两遍只入一次。"
        )
        general = MODULE.evaluate(prompt, "功能开发")
        bugfix = MODULE.evaluate(prompt, "Bugfix")
        alias = MODULE.evaluate(prompt, "bugfix")
        feature = MODULE.evaluate(prompt, "功能优化")

        self.assertTrue(general["g16_ok"])
        self.assertEqual(general["required_features"], 2)
        # 功能优化两项即达标；Bugfix（含 bugfix 别名）要求三项全备，所以被拦。
        self.assertTrue(feature["g16_ok"])
        self.assertTrue(feature["g16_enforced"])
        self.assertEqual(feature["required_features"], 2)
        self.assertFalse(bugfix["g16_ok"])
        self.assertFalse(alias["g16_ok"])
        self.assertEqual(bugfix["required_features"], 3)
        self.assertEqual(alias["difficulty_policy"], "hard-floor")

    def test_two_weak_features_are_not_enough_anymore(self) -> None:
        """这是 cc10017 #3 / #14 的形状：特征数够两项，但一项硬证据都没有。"""
        prompt = (
            "请修复库存看板上的条数与库存列表保持一致；"
            "历史记录仍按当时的判定口径保留。"
        )
        verdict = MODULE.evaluate(prompt, "功能开发")

        self.assertEqual(verdict["strong_feature_count"], 2)
        self.assertEqual(verdict["hard_features"], [])
        self.assertFalse(verdict["g16_ok"])
        self.assertTrue(any("硬证据" in item for item in verdict["reasons"]))

    def test_bugfix_two_feature_justification_cannot_bypass_floor(self) -> None:
        """受闸类型（此处用 功能开发）的「难度复核」备注要写够项数才放行。"""
        prompt = (
            "请修复库存看板上的条数与库存列表保持一致；"
            "历史记录仍按当时的判定口径保留。"
        )
        items = [("codegen-1", "功能开发", prompt)]

        blocked = MODULE.check(items, {"codegen-1": "难度复核: 多模块整合"})
        justified = MODULE.check(
            items,
            {"codegen-1": "难度复核: 多模块整合 + 关键设计取舍"},
        )

        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["violation_count"], 1)
        self.assertTrue(justified["ok"])
        self.assertEqual(justified["justified"][0]["justification_feature_count"], 2)

    def test_bugfix_with_all_three_features_passes_without_note(self) -> None:
        prompt = (
            "请修复库存看板上的条数与库存列表保持一致；"
            "历史记录仍按当时的判定口径保留；"
            "状态只能按提交、审核、发布顺序流转，不允许跳级。"
        )
        result = MODULE.check([("refactor-2", "代码重构", prompt)])

        self.assertTrue(result["ok"])
        self.assertEqual(result["violation_count"], 0)
        self.assertEqual(result["items"][0]["hard_features"], ["状态机与单向推进"])
        # 同一段题面挂在Bugfix上时要求三项全备；这段题面正好够三项，所以照样过，
        # 但「受闸」这件事必须在输出里看得出来。
        bugfix = MODULE.check([("bug-2", "Bugfix", prompt)])
        self.assertTrue(bugfix["ok"])
        self.assertTrue(bugfix["items"][0]["g16_enforced"])
        self.assertEqual(bugfix["items"][0]["required_features"], 3)
        self.assertEqual(bugfix["items"][0]["violation_kinds"], [])
        # 同类题面只写到两项特征时会被拦下。
        short = MODULE.check([("bug-3", "Bugfix", "请修复库存看板上的条数与库存列表保持一致；"
                                                  "状态只能按提交、审核、发布顺序流转，不允许跳级。")])
        self.assertFalse(short["ok"])
        self.assertIn("G16", short["items"][0]["violation_kinds"])

    def test_tail_only_hard_evidence_is_reported(self) -> None:
        """主诉求是列表一致、末尾补一句「断线重试接着走」的题面要给出人工复核提示。"""
        prompt = (
            "给使用登记加检索定位：条件叠加取交集，运营概览的条数要与列表一致。"
            "刷新后保留条件，使用登记断线后重试接着走。"
        )
        verdict = MODULE.evaluate(prompt, "功能开发")

        self.assertTrue(verdict["hints"])
        self.assertTrue(verdict["g16_ok"])
        strict = MODULE.check([("tail-1", "功能开发", prompt)], strict=True)
        self.assertFalse(strict["ok"])

    def test_strict_mode_is_opt_in_only(self) -> None:
        prompt = "给台账加导出：导出失败要能重试。"
        loose = MODULE.check([("tail-2", "功能开发", prompt)])
        # 普通模式下这条仍按特征数拦下（导出失败重试只算弱证据），strict 只多一条提示口径
        self.assertFalse(loose["ok"])

    def test_strict_mode_flags_single_module_and_medium_floor(self) -> None:
        """`--strict` 把「单一功能模块」与「G19 疑似不达中等」一起当不合格。"""
        single_module = "给检验单加一个导出按钮：把当前列表另存成文件，导出失败要说清原因。"
        strict = MODULE.check([("single-1", "依赖升级与迁移", single_module)], strict=True)
        self.assertFalse(strict["ok"])
        self.assertIn("strict", strict["items"][0]["violation_kinds"])
        self.assertGreaterEqual(strict["strict_only_violation_count"], 0)
        self.assertEqual(strict["g16_exempt_count"], 0)
        self.assertEqual(strict["g16_enforced_count"], 1)

    def test_every_type_is_enforced(self) -> None:
        for task_type in ("功能开发", "功能优化", "Bugfix", "代码重构", "依赖升级与迁移"):
            with self.subTest(task_type=task_type):
                self.assertTrue(MODULE.g16_enforced(task_type))
                self.assertEqual(MODULE.difficulty_policy(task_type), "hard-floor")
        result = MODULE.check([("x", "功能优化", "给台账加个按钮")])
        self.assertEqual(result["g16_exempt_count"], 0)
        self.assertEqual(result["g16_enforced_count"], 1)


if __name__ == "__main__":
    unittest.main()
