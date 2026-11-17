from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import batch_platform_audit


class BatchPlatformAuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="platform-audit-")
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name)

    def write_receipt(self, task_id: str, status: str, checks: list[dict]) -> None:
        review = self.batch / task_id / "review"
        review.mkdir(parents=True)
        (review / "upload.json").write_text(
            json.dumps({
                "task_id": task_id,
                "submission_id": 100,
                "remote_status": status,
                "qc_checks": checks,
            }, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_failure_owners_are_separated(self):
        self.write_receipt("repo-01-development", "DISCARDED", [{
            "check_key": "trace_complexity_floor",
            "check_name": "P5",
            "passed": False,
            "summary": "H3 不成立",
        }])
        self.write_receipt("repo-02-bugfix", "PENDING_FIX", [
            {
                "check_key": "trace_tool_call_floor",
                "check_name": "P6",
                "passed": False,
                "summary": "42 < 50",
            },
            {
                "check_key": "trace_memory",
                "check_name": "memory",
                "passed": False,
                "summary": "读取会话轨迹",
            },
        ])
        self.write_receipt("repo-03-optimization", "DISCARDED", [{
            "check_key": "dedup",
            "check_name": "查重",
            "passed": False,
            "summary": "A-5 公共长片段",
        }])
        self.write_receipt("repo-04-development", "PENDING_FIX", [{
            "check_key": "local_rules",
            "check_name": "本地规则",
            "passed": False,
            "summary": "Feedback 格式",
        }])

        result = batch_platform_audit.audit(self.batch)

        self.assertEqual(result["status_counts"]["DISCARDED"], 2)
        self.assertEqual(result["status_counts"]["PENDING_FIX"], 2)
        self.assertEqual(result["category_counts"]["prompt_difficulty"], 1)
        self.assertEqual(result["category_counts"]["prompt_workload"], 1)
        self.assertEqual(result["category_counts"]["runtime_memory"], 1)
        self.assertEqual(result["category_counts"]["prompt_dedup"], 1)
        self.assertEqual(result["category_counts"]["feedback_or_delivery"], 1)
        by_id = {row["task_id"]: row for row in result["tasks"]}
        self.assertEqual(
            by_id["repo-02-bugfix"]["owners"],
            ["s11-feedback-run", "s11-gsb-prompt-create"],
        )


if __name__ == "__main__":
    unittest.main()
