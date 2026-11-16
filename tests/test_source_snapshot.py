"""Tests for freezing plain local directories into stable Git snapshots."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import create_source_snapshot as source_snapshot


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=path,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class SourceSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s11-source-snapshot-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "local app"
        (self.source / "src").mkdir(parents=True)
        (self.source / "src/app.py").write_text(
            "def run(records):\n    return records\n", encoding="utf-8"
        )
        (self.source / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        (self.source / ".env.example").write_text("TOKEN=example\n", encoding="utf-8")
        (self.source / "node_modules").mkdir()
        (self.source / "node_modules/cache.txt").write_text("dependency", encoding="utf-8")
        (self.source / "runtime.sqlite").write_text("runtime data", encoding="utf-8")
        self.batch = self.base / "batch"

    def test_plain_directory_becomes_clean_deterministic_snapshot(self):
        first = source_snapshot.prepare_source_snapshot(
            self.source, self.batch, source_name="local-app", excludes=["*.sqlite"]
        )
        self.assertEqual(
            source_snapshot.validate_snapshot(first["snapshot_dir"], first["snapshot_manifest"])[
                "snapshot_commit"
            ],
            first["snapshot_commit"],
        )
        self.assertEqual(git(Path(first["snapshot_dir"]), "symbolic-ref", "--short", "HEAD"), "main")
        self.assertEqual(git(Path(first["snapshot_dir"]), "status", "--porcelain"), "")
        tracked = set(git(Path(first["snapshot_dir"]), "ls-files").splitlines())
        self.assertIn("src/app.py", tracked)
        self.assertIn(".env.example", tracked)
        self.assertNotIn(".env", tracked)
        self.assertNotIn("node_modules/cache.txt", tracked)
        self.assertNotIn("runtime.sqlite", tracked)
        self.assertEqual(first["file_count"], len(first["files"]))

        second_batch = self.base / "batch-2"
        second = source_snapshot.prepare_source_snapshot(
            self.source,
            second_batch,
            source_name="local-app",
            excludes=["*.sqlite"],
        )
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(first["snapshot_tree"], second["snapshot_tree"])

    def test_existing_snapshot_is_never_overwritten(self):
        source_snapshot.prepare_source_snapshot(self.source, self.batch)
        with self.assertRaisesRegex(ValueError, "不覆盖"):
            source_snapshot.prepare_source_snapshot(self.source, self.batch)


if __name__ == "__main__":
    unittest.main()
