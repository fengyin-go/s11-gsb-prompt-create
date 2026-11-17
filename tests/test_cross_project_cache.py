from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


CACHE = load_module("cross_project_cache")
CHECKER = load_module("check_cross_project_prompts")


class CrossProjectCacheTest(unittest.TestCase):
    def test_pair_results_hit_after_cache_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            corpus = root / "peer.xlsx"
            corpus.write_bytes(b"peer corpus v1")
            digest = CACHE.corpus_fingerprint([corpus])
            path = root / "cache.json"
            candidates = [{
                "label": "candidate",
                "task_type": "功能开发",
                "prompt": "采购单审核列表增加供应商与入库仓库汇总。",
            }]
            peers = [{
                "label": "peer",
                "task_type": "功能开发",
                "prompt": "采购单列表展示供应商编号和审核状态。",
            }]

            first_cache = CACHE.CrossProjectCache(path, corpus_digest=digest)
            first = CHECKER.check(candidates, peers, cache=first_cache)
            first_cache.save()
            second_cache = CACHE.CrossProjectCache(path, corpus_digest=digest)
            second = CHECKER.check(candidates, peers, cache=second_cache)

            self.assertGreater(first_cache.stats["misses"], 0)
            self.assertEqual(second_cache.stats["misses"], 0)
            self.assertGreater(second_cache.stats["hits"], 0)
            self.assertEqual(first["violation_count"], second["violation_count"])
            self.assertEqual(
                first["raw_a5_max_common_fragment_length"],
                second["raw_a5_max_common_fragment_length"],
            )

    def test_corpus_hash_change_invalidates_all_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            corpus = root / "peer.xlsx"
            corpus.write_bytes(b"v1")
            path = root / "cache.json"
            old_digest = CACHE.corpus_fingerprint([corpus])
            cache = CACHE.CrossProjectCache(path, corpus_digest=old_digest)
            cache.put(
                "local-compare",
                {"task_type": "功能开发", "prompt": "A"},
                {"task_type": "功能开发", "prompt": "B"},
                {"max_text_overlap": 0.2},
                {"ok": True},
            )
            cache.save()

            corpus.write_bytes(b"v2")
            new_digest = CACHE.corpus_fingerprint([corpus])
            checker_digest = CACHE.corpus_fingerprint(
                [corpus],
                extra={"implementation_sha256": {"checker.py": "v2"}},
            )
            reloaded = CACHE.CrossProjectCache(path, corpus_digest=new_digest)

            self.assertNotEqual(old_digest, new_digest)
            self.assertNotEqual(new_digest, checker_digest)
            self.assertTrue(reloaded.stats["invalidated"])
            self.assertEqual(reloaded.entries, {})

    def test_threshold_change_is_a_cache_miss(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = CACHE.CrossProjectCache(
                Path(temp) / "cache.json",
                corpus_digest="corpus",
            )
            candidate = {"task_type": "功能开发", "prompt": "候选"}
            peer = {"task_type": "功能开发", "prompt": "历史"}
            cache.put(
                "local-compare",
                candidate,
                peer,
                {"max_text_overlap": 0.2},
                {"ok": True},
            )

            hit = cache.get(
                "local-compare",
                candidate,
                peer,
                {"max_text_overlap": 0.2},
            )
            miss = cache.get(
                "local-compare",
                candidate,
                peer,
                {"max_text_overlap": 0.1},
            )

            self.assertEqual(hit, {"ok": True})
            self.assertIsNone(miss)

    def test_corrupt_cache_falls_back_to_miss(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cache.json"
            path.write_text("{not-json", encoding="utf-8")
            cache = CACHE.CrossProjectCache(path, corpus_digest="corpus")

            self.assertTrue(cache.stats["invalidated"])
            self.assertTrue(cache.stats["load_error"])
            self.assertIsNone(
                cache.get(
                    "local-compare",
                    {"task_type": "功能开发", "prompt": "A"},
                    {"task_type": "功能开发", "prompt": "B"},
                    {},
                )
            )

    def test_platform_pair_reuses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cache.json"
            candidates = [{
                "label": "candidate",
                "task_type": "功能开发",
                "prompt": "采购单审核列表增加供应商汇总。",
            }]
            peers = [{
                "label": "peer",
                "task_type": "功能开发",
                "prompt": "采购单列表展示供应商编号。",
            }]
            first_cache = CACHE.CrossProjectCache(path, corpus_digest="platform")
            first = CHECKER.check_platform_corpus(candidates, peers, cache=first_cache)
            first_cache.save()
            second_cache = CACHE.CrossProjectCache(path, corpus_digest="platform")
            second = CHECKER.check_platform_corpus(candidates, peers, cache=second_cache)

            self.assertGreater(first_cache.stats["misses"], 0)
            self.assertEqual(second_cache.stats["misses"], 0)
            self.assertEqual(first["violation_count"], second["violation_count"])


if __name__ == "__main__":
    unittest.main()
