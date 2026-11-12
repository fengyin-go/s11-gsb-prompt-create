from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "sync_platform_prompt_corpus.py"
SPEC = importlib.util.spec_from_file_location("sync_platform_prompt_corpus", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class SyncPlatformPromptCorpusTest(unittest.TestCase):
    def test_builds_compact_corpus_from_platform_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "index.json"
            path.write_text(json.dumps({
                "username": "gonghao",
                "fetched_at": 1791379997.0,
                "items": [{
                    "id": 7,
                    "repo_id": "owner/repo-7",
                    "question_type": "Bug修复",
                    "status": "DISCARDED",
                    "user_prompt": "题面正文",
                    "submitted_at": "2026-10-01T00:00:00+08:00",
                }],
            }, ensure_ascii=False), encoding="utf-8")

            items, account, fetched_at = MODULE.read_index(path)
            corpus = MODULE.build_corpus(items, account=account, fetched_at=fetched_at)

        self.assertEqual(corpus["account"], "gonghao")
        self.assertEqual(corpus["record_count"], 1)
        self.assertEqual(corpus["items"][0]["prompt"], "题面正文")
        self.assertEqual(corpus["items"][0]["task_type"], "Bug修复")
        self.assertEqual(len(corpus["items"][0]["prompt_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
