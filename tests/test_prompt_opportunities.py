"""Regression tests for the external prompt-opportunity map contract."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_prompt_opportunities as opportunities
import check_repo_theme
import check_prompt_naturalness as naturalness
import batch_prompt_workbook as workbook
import prompt_cross_project_registry as cross_registry
import prepare_handoff


class OpportunityMapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="s11-opportunities-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "src").mkdir()
        (self.repo / "src/orders.py").write_text(
            "def build_orders(rows):\n    订单数据集 = [dict(row) for row in rows]\n    return 订单数据集\n",
            encoding="utf-8",
        )
        (self.repo / "src/export.py").write_text(
            "def export_orders(订单数据集):\n    return str(订单数据集)\n",
            encoding="utf-8",
        )
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git(
            "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-q", "-m", "source",
        )
        self.base = self.git("rev-parse", "HEAD")
        self.modes = [
            mode for mode in check_repo_theme.DEMAND_MODE_LEXICON
            if mode != "代码理解"
        ]
        self.map_path = self.repo / "repo-topic-map.json"
        plans = []
        for index in range(36):
            mode = self.modes[index // 3]
            plans.append({
                "sequence": index + 1,
                "slug": "development",
                "task_type": "功能开发",
                "topic": f"topic-{index % 18}",
                "family": f"新增能力-{index}",
                "angle": mode,
                "domain_fit": {"prompt_main_request": self.plans_prompt(mode)},
                "difficulty_design": {"version": 3},
            })
        self.topic_map = {
            "version": 1,
            "profile": "gsb-semantic-v2",
            "difficulty_contract_version": 3,
            "type_policy": "capacity-first",
            "domain": "数据工程",
            "subdirection": "数据采集、爬虫与RPA",
            "base_commit": self.base,
            "topics": [],
            "task_plan": plans,
        }
        self.map_path.write_text(
            json.dumps(self.topic_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.git("add", "-A")
        self.git(
            "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-q", "-m", "map",
        )
        self.head = self.git("rev-parse", "HEAD")
        self.opportunity_path = self.root / "prompt-opportunities/repo.json"
        self.payload = self.make_payload()
        self.write_payload()

    def plans_prompt(self, mode: str) -> str:
        return f"当前缺少{mode}能力"

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def brief(self, index: int, *, mode: str, status: str) -> dict:
        item = {
            "opportunity_id": f"O-{index}",
            "status": status,
            "task_type": "功能开发",
            "family": f"新增能力-{index}",
            "demand_mode": mode,
            "story_summary": "订单数据进入解析链路后需要形成可核对结果。",
            "current_behavior": "当前只保留临时订单字段。",
            "desired_change": "生成订单数据集并供下游导出。",
            "data_chain": {
                "input": "订单原始记录",
                "operation": "解析订单记录",
                "output": "订单数据集",
                "observable": "导出文件保留订单编号",
            },
            "modules": {
                "primary": "订单解析",
                "secondary": "分析导出中心",
                "artifact": "订单数据集",
                "consumer_behavior": "导出中心读取同一订单数据集",
            },
            "constraints": ["字段必须完整", "重复导入不能重复生成"],
            "design_tradeoff": {
                "axis": "按批次重算还是按记录增量更新",
                "options": ["按批次重算", "按记录增量更新"],
                "invariant": "下游只能看到一份完整订单数据集",
            },
            "source_anchors": [
                {
                    "path": "src/orders.py",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": "def build_orders(rows):",
                },
                {
                    "path": "src/export.py",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": "def export_orders(订单数据集):",
                },
            ],
            "natural_prompt_angle": "从订单结果无法被下游核对切入。",
            "avoid_patterns": ["不要写成普通列表页"],
            "acceptance_signals": ["订单编号可追溯"],
        }
        if status == "selected":
            item["sequence"] = index + 1
            item["topic_id"] = f"topic-{index % 18}"
        else:
            item["reserve_for_mode"] = mode
        return item

    def make_payload(self) -> dict:
        selected = [
            self.brief(index, mode=self.modes[index // 3], status="selected")
            for index in range(36)
        ]
        reserve = [
            self.brief(100 + index, mode=mode, status="reserve")
            for index, mode in enumerate(self.modes)
        ]
        return {
            "schema_version": 1,
            "source_project": "repo",
            "source_commit": self.head,
            "source_base_commit": self.base,
            "repo_topic_map_sha256": hashlib.sha256(self.map_path.read_bytes()).hexdigest(),
            "target_count": 36,
            "selected_count": 36,
            "reserve_count": 12,
            "selection_policy": "12-modes-x3-plus-one-reserve-per-mode",
            "mode_counts": {mode: 3 for mode in self.modes},
            "reserve_mode_counts": {mode: 1 for mode in self.modes},
            "opportunities": selected + reserve,
        }

    def write_capacity_payload(self, count: int) -> None:
        plans = []
        mode_sequence = []
        for index in range(count):
            mode = self.modes[index % len(self.modes)]
            mode_sequence.append(mode)
            plans.append({
                "sequence": index + 1,
                "slug": "development",
                "task_type": "功能开发",
                "topic": f"topic-{index % 18}",
                "family": f"新增能力-{index}",
                "angle": mode,
                "domain_fit": {"prompt_main_request": self.plans_prompt(mode)},
                "difficulty_design": {"version": 3},
            })
        self.topic_map["task_plan"] = plans
        self.map_path.write_text(
            json.dumps(self.topic_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.git("add", "-A")
        self.git(
            "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-q", "-m", f"map-{count}",
        )
        self.head = self.git("rev-parse", "HEAD")
        selected = [
            self.brief(index, mode=mode, status="selected")
            for index, mode in enumerate(mode_sequence)
        ]
        used_modes = list(dict.fromkeys(mode_sequence))
        reserve = [
            self.brief(100 + index, mode=mode, status="reserve")
            for index, mode in enumerate(used_modes)
        ]
        mode_counts = {
            mode: mode_sequence.count(mode)
            for mode in used_modes
        }
        self.payload = {
            "schema_version": 1,
            "source_project": "repo",
            "source_commit": self.head,
            "source_base_commit": self.base,
            "repo_topic_map_sha256": hashlib.sha256(self.map_path.read_bytes()).hexdigest(),
            "target_count": count,
            "selected_count": count,
            "reserve_count": len(reserve),
            "selection_policy": opportunities.CAPACITY_SELECTION_POLICY,
            "mode_counts": mode_counts,
            "reserve_mode_counts": {mode: 1 for mode in used_modes},
            "opportunities": selected + reserve,
        }
        self.write_payload()

    def write_payload(self):
        self.opportunity_path.parent.mkdir(parents=True, exist_ok=True)
        self.opportunity_path.write_text(
            json.dumps(self.payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_valid_external_map(self):
        result = opportunities.check(self.repo, self.map_path, self.opportunity_path)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["selected_count"], 36)
        self.assertEqual(result["reserve_count"], 12)

    def test_capacity_payload_uses_actual_topic_plan_count(self):
        self.write_capacity_payload(8)
        result = opportunities.check(self.repo, self.map_path, self.opportunity_path)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["target_count"], 8)
        self.assertEqual(result["selected_count"], 8)
        self.assertEqual(result["reserve_count"], 8)
        self.assertEqual(
            result["selection_policy"],
            opportunities.CAPACITY_SELECTION_POLICY,
        )

    def test_capacity_payload_must_match_topic_plan_count(self):
        self.write_capacity_payload(8)
        self.payload["target_count"] = 9
        self.write_payload()
        result = opportunities.check(self.repo, self.map_path, self.opportunity_path)
        self.assertFalse(result["ok"])
        self.assertTrue(any("task_plan 数量 8" in error for error in result["errors"]))

    def test_map_inside_repo_is_rejected(self):
        inside = self.repo / "prompt-opportunities.json"
        inside.write_text(self.opportunity_path.read_text(encoding="utf-8"), encoding="utf-8")
        result = opportunities.check(self.repo, self.map_path, inside)
        self.assertFalse(result["ok"])
        self.assertTrue(any("仓库内部" in error for error in result["errors"]))

    def test_final_prompt_fields_are_rejected(self):
        self.payload["opportunities"][0]["final_prompt"] = "最终题面"
        self.write_payload()
        result = opportunities.check(self.repo, self.map_path, self.opportunity_path)
        self.assertFalse(result["ok"])
        self.assertTrue(any("最终题面字段" in error for error in result["errors"]))

    def test_missing_mode_seat_is_rejected(self):
        self.payload["opportunities"] = [
            item for item in self.payload["opportunities"]
            if item.get("sequence") != 1
        ]
        self.write_payload()
        result = opportunities.check(self.repo, self.map_path, self.opportunity_path)
        self.assertFalse(result["ok"])
        self.assertTrue(any("必须 36 条" in error or "必须 3 条" in error
                            for error in result["errors"]))

    def test_tampered_mode_summary_is_rejected(self):
        first_mode = self.modes[0]
        self.payload["mode_counts"][first_mode] = 2
        self.write_payload()
        result = opportunities.check(self.repo, self.map_path, self.opportunity_path)
        self.assertFalse(result["ok"])
        self.assertTrue(any("mode_counts" in error for error in result["errors"]))

    def test_duplicate_opportunity_id_is_rejected(self):
        self.payload["opportunities"][1]["opportunity_id"] = \
            self.payload["opportunities"][0]["opportunity_id"]
        self.write_payload()
        result = opportunities.check(self.repo, self.map_path, self.opportunity_path)
        self.assertFalse(result["ok"])
        self.assertTrue(any("opportunity_id 重复" in error for error in result["errors"]))

    def test_result_exposes_hashes_for_handoff_freeze(self):
        result = opportunities.check(self.repo, self.map_path, self.opportunity_path)
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            result["opportunity_sha256"],
            hashlib.sha256(self.opportunity_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            result["repo_topic_map_sha256"],
            hashlib.sha256(self.map_path.read_bytes()).hexdigest(),
        )

    def test_handoff_revalidates_workbook_and_external_map(self):
        batch = self.root / "batch"
        batch.mkdir()
        workbook = batch / "tasks.xlsx"
        workbook.write_bytes(b"initial workbook")
        checked = opportunities.check(self.repo, self.map_path, self.opportunity_path)
        manifest = {
            "workbook": str(workbook),
            "workbook_sha256": hashlib.sha256(workbook.read_bytes()).hexdigest(),
            "repo": str(self.repo),
            "prompt_opportunities": {
                "ok": True,
                "opportunity_map": str(self.opportunity_path),
                "sha256": checked["opportunity_sha256"],
                "source_commit": checked["source_commit"],
                "repo_topic_map_sha256": checked["repo_topic_map_sha256"],
            },
        }
        (batch / "prompt-generation-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        binding = prepare_handoff.validate_prompt_opportunity(
            batch, self.repo, self.map_path
        )
        self.assertEqual(binding["sha256"], checked["opportunity_sha256"])

        workbook.write_bytes(b"changed workbook")
        with self.assertRaisesRegex(ValueError, "tasks.xlsx"):
            prepare_handoff.validate_prompt_opportunity(batch, self.repo, self.map_path)

    def test_update_many_writes_36_rows_and_freezes_opportunity_binding(self):
        batch = self.root / "batch"
        batch.mkdir()
        rows = []
        candidates = []
        business_objects = (
            "订单 发票 运单 客户 商品 库存 退款 结算 优惠券 合同 回款 物流轨迹 "
            "售后单 供应商 采购单 入库单 出库单 盘点单 质检单 价目表 渠道账单 "
            "对账批次 收款单 付款单 预算单 报销单 考勤单 排班表 工时单 设备台账 "
            "维修单 能耗记录 标签批次 指标快照 数据版本 审计记录"
        ).split()
        for index, business_object in enumerate(business_objects):
            folder = f"repo-{index + 1:02d}-development"
            prompt = (
                f"{business_object}处理结果仍有临时字段，"
                f"业务人员无法按{business_object}编号核对。"
                f"请补齐{business_object}解析、批次落库和导出消费，保存来源摘要与处理时间。"
                f"{business_object}编号和来源摘要必须来自同一输入批次，"
                "重复导入只保留一份可消费结果。"
                f"页面可以查看{business_object}处理进度并下载明细。"
            )
            rows.append({
                "子文件夹名称": folder,
                "任务类型": "功能开发",
                "编号": str(index + 1),
                "提示词": "",
                "提示词类型": "主提示词",
                "状态": "待生成",
                "备注": "",
                "更新时间": "",
            })
            candidates.append({"folder": folder, "prompt": prompt})
        natural = naturalness.check([
            {
                "子文件夹名称": item["folder"],
                "任务类型": "功能开发",
                "提示词": item["prompt"],
            }
            for item in candidates
        ])
        self.assertTrue(natural["ok"], natural)
        workbook.write_workbook(batch / "tasks.xlsx", rows)
        config_path = batch / "gsb-cross-project.json"
        config = {"registry": str(batch / "registry.json"), "lock_timeout_seconds": 1}
        with patch.object(workbook, "load_cross_project_config",
                          return_value=(config_path, config)), \
             patch.object(workbook, "run_write_gates",
                          return_value={"ok": True, "gate_version": "fixture"}), \
             patch.object(workbook, "run_cross_project_batch_gate",
                          return_value={"ok": True}), \
             patch("repo_topic_policy.load_topic_map", return_value=self.topic_map), \
             patch("check_prompt_g16.check", return_value={"ok": True}), \
             patch.object(cross_registry, "registry_lock",
                          return_value=nullcontext()), \
             patch.object(cross_registry, "load_registry", return_value={}), \
             patch.object(cross_registry, "upsert_entry"), \
             patch.object(cross_registry, "save_registry"):
            result = workbook.update_many(
                batch,
                candidates,
                repo=self.repo,
                topic_map=self.map_path,
                opportunity_map=self.opportunity_path,
                require_topic_map=True,
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(workbook.read_workbook(batch / "tasks.xlsx")), 36)
        manifest = json.loads(
            (batch / "prompt-generation-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["prompt_opportunities"]["sha256"],
            hashlib.sha256(self.opportunity_path.read_bytes()).hexdigest(),
        )
        binding = prepare_handoff.validate_prompt_opportunity(
            batch, self.repo, self.map_path
        )
        self.assertEqual(binding["selected_count"], 36)
        self.assertEqual(binding["reserve_count"], 12)


if __name__ == "__main__":
    unittest.main()
