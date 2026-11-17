from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import platform_contract
RUNNER = Path(__file__).resolve().parents[2] / "s11-feedback-run/scripts"
sys.path.insert(0, str(RUNNER))
import fbr_sheet


TOPIC = {"domain": "数据工程", "subdirection": "其他"}
CFG = {"harness": "Claude Code", "harness_version": "2.1.319"}


class PlatformContract(unittest.TestCase):
    def metadata(self, **common):
        return {
            "common": {
                "repo_id": "repo",
                "domain": "数据工程",
                "subdirection": "其他",
                "language": "Python",
                "frameworks": "FastAPI",
                "repro_level": "已容器化，可一键起环境",
                **common,
            },
            "tasks": {
                "repo-01-development": {
                    "difficulty": "困难",
                    "recording_plan": {"demo": [{"cmd": "make test", "show": "ok"}]},
                },
            },
        }

    def rows(self):
        return {"repo-01-development": {"任务类型": "功能开发", "任务难度": "困难"}}

    def test_descriptive_repro_level_is_split_without_guessing(self):
        errors, meta = platform_contract.validate(
            self.metadata(repro_level="已容器化，真实镜像首次安装和无安装重启均通过"),
            self.rows(),
            TOPIC,
            CFG,
        )
        self.assertEqual(errors, [])
        self.assertEqual(meta["common"]["repro_level"], "已容器化，可一键起环境")
        self.assertIn("真实镜像首次安装", meta["common"]["repro_evidence"])

    def test_invalid_domain_and_repro_level_are_reported_before_handoff(self):
        errors, _ = platform_contract.validate(
            self.metadata(domain="测试领域", subdirection="测试方向", repro_level="环境可用"),
            self.rows(),
            TOPIC,
            CFG,
        )
        text = "；".join(errors)
        self.assertIn("domain 不是平台枚举", text)
        self.assertIn("repro_level 不是平台枚举", text)
        self.assertIn("必须与 repo-topic-map.json 一致", text)

    def test_web_plan_requires_page_coverage_before_handoff(self):
        meta = self.metadata()
        meta["tasks"]["repo-01-development"]["recording_plan"] = {
            "mode": "web",
            "serve_cmd": "npm start",
            "steps": [{"action": "click", "selector": "button"}],
        }
        errors, _ = platform_contract.validate(meta, self.rows(), TOPIC, CFG)
        self.assertTrue(any("必须声明 pages" in item for item in errors))

    def test_contract_matches_runner_platform_options(self):
        self.assertEqual(platform_contract.DOMAIN_SUBDIRECTIONS["安全"],
                         fbr_sheet.OPTIONS["子方向"][:5])
        self.assertEqual(platform_contract.TASK_TYPES,
                         tuple(fbr_sheet.OPTIONS["任务类型"][:5]))
        self.assertEqual(platform_contract.DIFFICULTIES, tuple(fbr_sheet.OPTIONS["任务难度"]))
        self.assertEqual(platform_contract.OS_PLATFORM, fbr_sheet.OPTIONS["操作系统"][0])
        self.assertEqual(platform_contract.REPRO_LEVELS,
                         tuple(fbr_sheet.OPTIONS["环境可复现等级"]))


if __name__ == "__main__":
    unittest.main()
