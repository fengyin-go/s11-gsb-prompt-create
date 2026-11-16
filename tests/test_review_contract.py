from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import review_contract
from prompt_history_lib import _LEGACY_CONTEXT_BOILERPLATE


HERE = Path(__file__).resolve().parent
BATCH_REJECTIONS = json.loads(
    (HERE / "fixtures" / "platform-batch-rejections-20261115.json").read_text(
        encoding="utf-8"
    )
)


class ReviewContractTest(unittest.TestCase):
    def setUp(self):
        self.prompt = (
            "请读取订单记录并生成已发布订单数据集。"
            "写入失败时保留上一份完整数据集，重试只补缺失记录。"
            "导出中心必须读取同一份已发布版本。"
        )
        self.requirement = {
            "id": "R1",
            "text": "写入失败保留上一份完整数据集",
            "check": "制造写入失败后读取版本",
        }
        self.contract = {
            "version": 1,
            "business_checks": [
                {
                    "requirement_id": "R1",
                    "behavior": "写入失败后旧版本仍可读取，重试只补缺失记录",
                    "evidence_kind": "probe",
                    "success_signal": "旧版本内容不变且恢复后记录完整",
                }
            ],
            "test_behaviors": [
                {
                    "requirement_ids": ["R1"],
                    "selector": "rollback_and_resume",
                    "behavior": "验证失败回退和从缺失记录继续写入",
                }
            ],
            "key_business_constraint": "写入失败时保留上一份完整数据集",
            "environment_exclusions": sorted(review_contract.REQUIRED_ENVIRONMENT_EXCLUSIONS),
            "model_process_focus": [
                "模型是否先构造失败写入，再验证旧版本没有被覆盖"
            ],
            "style_focus": "用完整主谓句写清失败、修正和用户可检查的数据结果",
            "language_terms": [{"source": "OCC", "public": "提交时带上当前版本"}],
        }
        self.record = {
            "子文件夹名称": "orders-01-development",
            "提示词": self.prompt,
            "requirements": [self.requirement],
            "review_contract": self.contract,
        }

    def problems(self, record=None, metadata=None):
        record = record or self.record
        metadata = metadata or {
            "tasks": {
                record["子文件夹名称"]: {
                    **record,
                }
            }
        }
        return review_contract.errors([record], metadata)

    def test_valid_review_contract_passes(self):
        self.assertEqual(self.problems(), [])

    def test_batch_rejection_fixture_covers_all_observed_classes(self):
        cases = BATCH_REJECTIONS["cases"]
        self.assertEqual(len(cases), 21)
        counts = {}
        for case in cases:
            counts[case["category"]] = counts.get(case["category"], 0) + 1
        self.assertEqual(counts["difficulty_discarded"], 13)
        self.assertEqual(counts["feedback_wrong_attribution"], 2)
        self.assertEqual(counts["feedback_telegraph"], 2)
        self.assertEqual(counts["feedback_generic_test"], 2)
        self.assertEqual(counts["trace_memory"], 2)

    def test_every_requirement_needs_a_business_check(self):
        record = copy.deepcopy(self.record)
        record["requirements"].append(
            {"id": "R2", "text": "导出中心读取同一版本", "check": "核对导出"}
        )
        metadata = {"tasks": {record["子文件夹名称"]: record}}
        errors = self.problems(record, metadata)
        self.assertTrue(any("未覆盖需求：R2" in error for error in errors))

    def test_key_constraint_must_appear_verbatim(self):
        record = copy.deepcopy(self.record)
        record["review_contract"]["key_business_constraint"] = "写入失败后系统自动忽略所有内容"
        metadata = {"tasks": {record["子文件夹名称"]: record}}
        self.assertTrue(any("不在最终题面中" in error for error in self.problems(record, metadata)))

    def test_environment_exclusions_are_required(self):
        record = copy.deepcopy(self.record)
        record["review_contract"]["environment_exclusions"] = ["dependency_install"]
        metadata = {"tasks": {record["子文件夹名称"]: record}}
        errors = self.problems(record, metadata)
        self.assertTrue(any("environment_exclusions 缺少" in error for error in errors))

    def test_environment_noise_cannot_be_model_process_focus(self):
        record = copy.deepcopy(self.record)
        record["review_contract"]["model_process_focus"] = [
            "gateway 超时导致最终失败"
        ]
        metadata = {"tasks": {record["子文件夹名称"]: record}}
        self.assertTrue(any("不能放环境或工具噪声" in error for error in self.problems(record, metadata)))

    def test_context_source_restrictions_are_rejected(self):
        record = copy.deepcopy(self.record)
        record["提示词"] += _LEGACY_CONTEXT_BOILERPLATE
        metadata = {"tasks": {record["子文件夹名称"]: record}}
        self.assertTrue(any("上下文来源限制" in error for error in self.problems(record, metadata)))

    def test_handoff_rejects_paraphrased_context_restrictions_and_vague_scaffolds(self):
        for tail in ("仅限使用本项目代码完成开发。", "不要参考之前的对话。", "结果要关联另一个入口。"):
            with self.subTest(tail=tail):
                record = copy.deepcopy(self.record)
                record["提示词"] += tail
                self.assertTrue(self.problems(record))

    def test_legacy_metadata_does_not_require_a_prompt_statement(self):
        record = copy.deepcopy(self.record)
        record["review_contract"]["context_isolation"] = {
            "workspace_only": True,
            "cross_session_memory": False,
            "answer_notes": False,
            "prompt_statement": _LEGACY_CONTEXT_BOILERPLATE,
        }
        metadata = {"tasks": {record["子文件夹名称"]: record}}
        self.assertEqual(self.problems(record, metadata), [])

    def test_test_behavior_must_map_to_real_requirement(self):
        record = copy.deepcopy(self.record)
        record["review_contract"]["test_behaviors"][0]["requirement_ids"] = ["R9"]
        metadata = {"tasks": {record["子文件夹名称"]: record}}
        self.assertTrue(any("requirement_ids 必须引用真实需求" in error for error in self.problems(record, metadata)))


if __name__ == "__main__":
    unittest.main()
