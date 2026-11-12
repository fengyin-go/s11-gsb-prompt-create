from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SCRIPT_DIR = SKILL_DIR / "scripts"
for path in (SCRIPT_DIR,):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from solo_paths import solo_create_scripts  # noqa: E402

SOLO_SCRIPTS = solo_create_scripts()
if str(SOLO_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SOLO_SCRIPTS))

import batch_prompt_workbook as workbook_lib  # noqa: E402
import prompt_cross_project_registry as registry_lib  # noqa: E402


CHECKER = SCRIPT_DIR / "check_cross_project_prompts.py"
REGISTER = SCRIPT_DIR / "register_cross_project_batches.py"

# 单测固定关闭真实账号的平台语料，避免测试依赖运行机上的个人数据。
os.environ["GSB_PLATFORM_PROMPT_CORPUS"] = "0"


def record(folder: str, prompt: str, *, task_type: str = "功能开发") -> dict[str, str]:
    return {
        "子文件夹名称": folder,
        "任务类型": task_type,
        "编号": "1",
        "提示词": prompt,
        "提示词类型": "主提示词",
        "状态": "已生成",
        "备注": "",
        "更新时间": "",
    }


def write_prompt_workbook(path: Path, prompt: str, *, folder: str = "peer") -> None:
    workbook_lib.write_workbook(path, [record(folder, prompt)])


def write_registry(registry: Path, entries: list[tuple[Path, str, int]]) -> None:
    payload = registry_lib.empty_registry()
    for workbook, batch_id, prompt_count in entries:
        registry_lib.upsert_entry(
            payload,
            workbook,
            batch_id=batch_id,
            batch_dir=workbook.parent,
            status="finalized" if prompt_count else "empty",
            prompt_count=prompt_count,
        )
    registry_lib.save_registry(registry, payload)


class CrossProjectRegistryTest(unittest.TestCase):
    def test_checker_reads_registered_peer_from_another_parent(self) -> None:
        peer_prompt = (
            "请为检测样品接收（检测样品、样品编号）增加一个可独立进入的概览视图，"
            "集中呈现样品编号、样品名称、委托单位、样品类型以及今日接收、待接收样品。"
        )
        candidate = (
            "在光伏电站档案列表旁增加按所属区域和运维负责人汇总的负荷面板。"
            "面板给出每位负责人管理的电站数、并网运行数、停运维护数、总装机容量，"
            "点某位负责人后列表只保留其名下电站。"
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            peer_batch = root / "root-a" / "batch-peer"
            current_batch = root / "root-b" / "batch-current"
            peer_batch.mkdir(parents=True)
            current_batch.mkdir(parents=True)
            peer_workbook = peer_batch / workbook_lib.DEFAULT_WORKBOOK
            write_prompt_workbook(peer_workbook, peer_prompt)
            registry = root / "registry.json"
            write_registry(registry, [(peer_workbook, "batch-peer", 1)])

            completed = subprocess.run(
                [
                    sys.executable,
                    str(CHECKER),
                    "--batch-dir",
                    str(current_batch),
                    "--registry",
                    str(registry),
                    "--require-registry",
                    "--require-coverage",
                    "--min-peer-batches",
                    "1",
                    "--prompt",
                    candidate,
                    "--task-type",
                    "功能开发",
                    "--source-folder",
                    "100021-codegen-1",
                ],
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, payload)
        self.assertTrue(payload["registry_used"])
        self.assertTrue(payload["coverage_ok"])
        self.assertEqual(payload["peer_prompt_count"], 1)
        self.assertEqual(payload["cross_project_peer_batches"], ["batch-peer"])

    def test_missing_registry_fails_when_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            current_batch = Path(temp) / "batch-current"
            current_batch.mkdir()
            completed = subprocess.run(
                [
                    sys.executable,
                    str(CHECKER),
                    "--batch-dir",
                    str(current_batch),
                    "--registry",
                    str(Path(temp) / "missing.json"),
                    "--require-registry",
                    "--require-coverage",
                    "--prompt",
                    "实现一个新的设备档案查看页面。",
                    "--task-type",
                    "功能开发",
                ],
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 1)
        self.assertFalse(payload["coverage_ok"])
        self.assertEqual(payload["coverage_issues"][0]["kind"], "missing_registry")

    def test_hash_mismatch_fails_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            peer_batch = root / "batch-peer"
            current_batch = root / "batch-current"
            peer_batch.mkdir()
            current_batch.mkdir()
            peer_workbook = peer_batch / workbook_lib.DEFAULT_WORKBOOK
            write_prompt_workbook(peer_workbook, "请实现检测样品的接收记录页面。")
            registry = root / "registry.json"
            write_registry(registry, [(peer_workbook, "batch-peer", 1)])
            write_prompt_workbook(
                peer_workbook,
                "请实现检测样品的接收记录页面，并增加新的审核状态。",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(CHECKER),
                    "--batch-dir",
                    str(current_batch),
                    "--registry",
                    str(registry),
                    "--require-registry",
                    "--require-coverage",
                    "--prompt",
                    "实现光伏电站档案的总览页面。",
                    "--task-type",
                    "功能开发",
                ],
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 1)
        kinds = {item["kind"] for item in payload["coverage_issues"]}
        self.assertIn("workbook_hash_mismatch", kinds)

    def test_empty_registry_requires_explicit_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            current_batch = Path(temp) / "batch-current"
            current_batch.mkdir()
            registry = Path(temp) / "registry.json"
            write_registry(registry, [])
            base_command = [
                sys.executable,
                str(CHECKER),
                "--batch-dir",
                str(current_batch),
                "--registry",
                str(registry),
                "--require-registry",
                "--require-coverage",
                "--prompt",
                "实现一个新的设备档案查看页面。",
                "--task-type",
                "功能开发",
            ]
            blocked = subprocess.run(base_command, capture_output=True, text=True)
            bootstrapped = subprocess.run(
                [*base_command, "--bootstrap-no-history"],
                capture_output=True,
                text=True,
            )

        self.assertEqual(blocked.returncode, 1)
        self.assertIn(
            "insufficient_peer_batches",
            {item["kind"] for item in json.loads(blocked.stdout)["coverage_issues"]},
        )
        self.assertEqual(bootstrapped.returncode, 0, bootstrapped.stdout)

    def test_register_script_discovers_multiple_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root_a = root / "root-a"
            root_b = root / "root-b"
            first = root_a / "batch-a" / workbook_lib.DEFAULT_WORKBOOK
            second = root_b / "nested" / "batch-b" / workbook_lib.DEFAULT_WORKBOOK
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            write_prompt_workbook(first, "实现检测样品接收页面。")
            write_prompt_workbook(second, "实现光伏电站档案页面。")
            registry = root / "registry.json"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(REGISTER),
                    "--scan-root",
                    str(root_a),
                    "--scan-root",
                    str(root_b),
                    "--max-depth",
                    "4",
                    "--registry",
                    str(registry),
                    "--register",
                    "--write-batch-config",
                ],
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            saved = registry_lib.load_registry(registry)
            first_config_exists = (first.parent / workbook_lib.CROSS_PROJECT_CONFIG).is_file()
            second_config_exists = (second.parent / workbook_lib.CROSS_PROJECT_CONFIG).is_file()

        self.assertEqual(completed.returncode, 0, payload)
        self.assertEqual(payload["discovered_count"], 2)
        self.assertEqual(payload["registered_count"], 2)
        self.assertEqual(payload["batch_configs_written"], 2)
        self.assertEqual(len(saved["entries"]), 2)
        self.assertTrue(first_config_exists)
        self.assertTrue(second_config_exists)

    def test_update_runs_cross_project_gate_and_registers_current_batch(self) -> None:
        peer_prompt = (
            "请为检测样品接收（检测样品、样品编号）增加一个可独立进入的概览视图，"
            "集中呈现样品编号、样品名称、委托单位、样品类型以及今日接收、待接收样品。"
        )
        candidate = (
            "在光伏电站档案列表旁增加按所属区域和运维负责人汇总的负荷面板。"
            "面板给出每位负责人管理的电站数、并网运行数、停运维护数、总装机容量，"
            "点某位负责人后列表只保留其名下电站。"
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            peer_batch = root / "batch-peer"
            current_batch = root / "batch-current"
            peer_batch.mkdir()
            current_batch.mkdir()
            peer_workbook = peer_batch / workbook_lib.DEFAULT_WORKBOOK
            write_prompt_workbook(peer_workbook, peer_prompt)
            registry = root / "registry.json"
            write_registry(registry, [(peer_workbook, "batch-peer", 1)])
            config = {
                "registry": str(registry),
                "checker": str(CHECKER),
                "require_registry": True,
                "require_coverage": True,
                "min_peer_batches": 1,
                "root_fallback": False,
            }
            (current_batch / workbook_lib.CROSS_PROJECT_CONFIG).write_text(
                json.dumps(config, ensure_ascii=False),
                encoding="utf-8",
            )

            task = current_batch / "s11-000001"
            task.mkdir()
            (task / "task-seed.json").write_text(json.dumps({"task_id": task.name, "task_type": "功能开发"}), encoding="utf-8")
            # This case isolates registry registration; G16 itself has separate real-corpus tests.
            from unittest.mock import patch
            g16_patch = patch("check_prompt_g16.check", return_value={"ok": True})
            g16_patch.start()
            self.addCleanup(g16_patch.stop)
            with mock.patch.object(
                workbook_lib,
                "run_write_gates",
                return_value={
                    "ok": True,
                    "gate_version": "test",
                    "problems": [],
                    "theme": {},
                    "difficulty": {},
                    "dedup": {},
                },
            ):
                result = workbook_lib.update(
                    current_batch,
                    None,
                    "s11-000001",
                    candidate,
                    "",
                    None,
                    None,
                    repo=None,
                    skip_version_check=True,
                )
            saved = registry_lib.load_registry(registry)
            manifest = json.loads(
                (current_batch / workbook_lib.MANIFEST_FILENAME).read_text(encoding="utf-8")
            )

        self.assertEqual(result["gate"]["cross_project"]["peer_prompt_count"], 1)
        current_workbook = current_batch / workbook_lib.DEFAULT_WORKBOOK
        self.assertIn(registry_lib.canonical_path(current_workbook), saved["entries"])
        self.assertEqual(
            saved["entries"][registry_lib.canonical_path(current_workbook)]["prompt_count"],
            1,
        )
        self.assertEqual(manifest["cross_project"]["cross_project_peer_batches"], ["batch-peer"])

    def test_update_blocks_similar_prompt_before_write(self) -> None:
        prompt = (
            "请为检测样品接收（检测样品、样品编号）增加一个可独立进入的概览视图，"
            "集中呈现样品编号、样品名称、委托单位、样品类型以及今日接收、待接收样品。"
            "页面需要同时服务列表和详情，空数据或读取失败时保留当前条件并说明原因；"
            "多次打开时，显示数量与当前记录不能冲突，也不能影响原有操作。"
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            peer_batch = root / "batch-peer"
            current_batch = root / "batch-current"
            peer_batch.mkdir()
            current_batch.mkdir()
            peer_workbook = peer_batch / workbook_lib.DEFAULT_WORKBOOK
            write_prompt_workbook(peer_workbook, prompt)
            registry = root / "registry.json"
            write_registry(registry, [(peer_workbook, "batch-peer", 1)])
            (current_batch / workbook_lib.CROSS_PROJECT_CONFIG).write_text(
                json.dumps({
                    "registry": str(registry),
                    "checker": str(CHECKER),
                    "require_registry": True,
                    "require_coverage": True,
                    "min_peer_batches": 1,
                    "root_fallback": False,
                }, ensure_ascii=False),
                encoding="utf-8",
            )

            with mock.patch.object(
                workbook_lib,
                "run_write_gates",
                return_value={
                    "ok": True,
                    "gate_version": "test",
                    "problems": [],
                    "theme": {},
                    "difficulty": {},
                    "dedup": {},
                },
            ):
                with self.assertRaises(SystemExit):
                    workbook_lib.update(
                        current_batch,
                        None,
                        "100021-codegen-1",
                        prompt,
                        "",
                        None,
                        None,
                        repo=None,
                        skip_version_check=True,
                    )
            current_workbook = current_batch / workbook_lib.DEFAULT_WORKBOOK
            saved = registry_lib.load_registry(registry)

        self.assertFalse(current_workbook.exists())
        self.assertNotIn(registry_lib.canonical_path(current_workbook), saved["entries"])


    def test_update_blocks_platform_forbidden_topic_before_write(self) -> None:
        prompt = "把处理完的记录放回待办清单，运营概览也要跟着更新。"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current_batch = root / "batch-current"
            current_batch.mkdir()
            corpus = root / "platform-corpus.json"
            corpus.write_text(json.dumps({
                "profile": "gsb-platform-prompt-corpus-v1",
                "account": "tester",
                "fetched_at": "2026-10-08T00:00:00+08:00",
                "items": [],
            }, ensure_ascii=False), encoding="utf-8")
            registry = root / "registry.json"
            write_registry(registry, [])
            (current_batch / workbook_lib.CROSS_PROJECT_CONFIG).write_text(
                json.dumps({
                    "registry": str(registry),
                    "checker": str(CHECKER),
                    "require_registry": True,
                    "require_coverage": True,
                    "min_peer_batches": 0,
                    "bootstrap_no_history": True,
                    "root_fallback": False,
                }, ensure_ascii=False),
                encoding="utf-8",
            )

            with mock.patch.dict(
                os.environ,
                {"GSB_PLATFORM_PROMPT_CORPUS": str(corpus)},
            ), mock.patch.object(
                workbook_lib,
                "run_write_gates",
                return_value={
                    "ok": True,
                    "gate_version": "test",
                    "problems": [],
                    "theme": {},
                    "difficulty": {},
                    "dedup": {},
                },
            ):
                with self.assertRaises(SystemExit):
                    workbook_lib.update(
                        current_batch,
                        None,
                        "100021-codegen-1",
                        prompt,
                        "",
                        None,
                        None,
                        repo=None,
                        skip_version_check=True,
                    )

        self.assertFalse((current_batch / workbook_lib.DEFAULT_WORKBOOK).exists())


if __name__ == "__main__":
    unittest.main()
