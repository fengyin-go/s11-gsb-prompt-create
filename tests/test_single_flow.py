"""Local Git integration: plan -> single-main clones -> real runner V2 reader."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import batch_prompt_workbook as workbook
import create_batch_tasks as create
import prepare_handoff as handoff
from s11_policy import allocate_counts, validate_plan


def sh(path, *args):
    p = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=path,
                       capture_output=True, text=True)
    if p.returncode:
        raise AssertionError(p.stderr)
    return p.stdout.strip()


class SingleFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="s11-skill-test-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "root"
        self.parent = self.root / "batches/b1111-01"
        self.parent.mkdir(parents=True)
        self.source = self.base / "source"
        self.source.mkdir()
        sh(self.source, "init", "-q", "-b", "main")
        sh(self.source, "config", "user.name", "Selftest")
        sh(self.source, "config", "user.email", "selftest@example.com")
        (self.source / "src").mkdir()
        for name in ("orders", "accounts", "search", "exports", "invoices"):
            (self.source / "src" / f"{name}.py").write_text(f"def {name}():\n    return []\n")
        (self.source / ".gitignore").write_text("node_modules/\n")
        (self.source / "Makefile").write_text("setup:\n\t@true\ntest:\n\t@true\n")
        sh(self.source, "add", "-A")
        sh(self.source, "commit", "-qm", "initial")
        self.commit = sh(self.source, "rev-parse", "HEAD")
        self.source_bare = self.base / "cc10003.git"
        sh(self.base, "clone", "--bare", str(self.source), str(self.source_bare))
        sh(self.source, "remote", "add", "origin", str(self.source_bare))
        self.remote_repos = {}
        self.topic = {"version": 1, "profile": "gsb-semantic-v2", "base_commit": self.commit,
                      "max_capacity": 1, "topics": [{"id": "orders", "name": "订单", "module": "orders",
                        "keywords": ["订单", "订单项"], "evidence": ["src/orders.py"], "seat_capacity": 1}],
                      "task_plan": [{"sequence": 1, "slug": "development", "task_type": "功能开发",
                                     "topic": "orders", "family": "新增能力", "angle": "订单导出"}]}
        create.save_json(self.parent / "repo-topic-map.json", self.topic)
        create.save_json(self.parent / "startup-preflight.json", {"startup_ok": True, "cleaned_up": True,
            "baseline_push_ok": True, "dependency_ignored": True, "dependency_paths": [],
            "startup_commit": self.commit, "baseline_commit": self.commit,
            "startup_command": "python3 -c 'from src.orders import orders; orders()'",
            "startup_evidence": "local fixture entry succeeds"})
        create.save_json(self.root / "_config/settings.json", {"image_id": "sha256:" + "a" * 64, "github_owner": "fengyin-go",
            "git_user_name": "Selftest", "git_user_email": "selftest@example.com"})
        self.original_clone = create.hub.clone_branch

    def fake_repo_create(self, owner, name, visibility):
        bare = self.base / f"{name}.git"
        sh(self.base, "init", "--bare", "-q", "-b", "main", str(bare))
        self.remote_repos[f"https://github.com/{owner}/{name}"] = bare

    def fake_heads(self, url, **kwargs):
        local = self.remote_repos.get(url, url)
        lines = sh(self.base, "ls-remote", "--heads", str(local)).splitlines()
        return {line.split()[1].removeprefix("refs/heads/"): line.split()[0] for line in lines}

    def fake_push(self, url, commit, branch, source):
        sh(source, "push", str(self.remote_repos[url]), f"{commit}:refs/heads/{branch}")
        return "created"

    def fake_clone(self, url, branch, dest, **kwargs):
        self.original_clone(str(self.remote_repos[url]), branch, dest, **kwargs)
        sh(dest, "remote", "set-url", "origin", url)

    def wire_git(self):
        patches = [patch.object(create.hub, "repo_exists", return_value=False),
                   patch.object(create.hub, "create_repo", side_effect=self.fake_repo_create),
                   patch.object(create.hub, "push_commit_to_branch", side_effect=self.fake_push),
                   patch.object(create.hub, "remote_heads", side_effect=self.fake_heads),
                   patch.object(create.hub, "clone_branch", side_effect=self.fake_clone)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def build(self, dry=False):
        return create.create_batch(self.parent, self.source, self.parent / "repo-topic-map.json",
                      preflight=self.parent / "startup-preflight.json", owner="fengyin-go", dry_run=dry)

    def test_dry_run_no_files_or_remote_mutations(self):
        self.wire_git()
        before = set(self.parent.rglob("*"))
        result = self.build(dry=True)
        self.assertTrue(result["ok"])
        self.assertEqual(set(self.parent.rglob("*")), before)
        self.assertFalse(self.remote_repos)
        self.assertEqual(result["branches"], ["main"])
        self.assertEqual(result["tasks"][0]["repo"], "fengyin-go/cc10003-01-development")
        self.assertEqual(result["tasks"][0]["folder"], "cc10003-01-development")
        self.assertEqual(result["tasks"][0]["task_id"], "cc10003-01-development")

    def test_bugfix_directory_matches_repository_and_start_index_only_affects_workbook(self):
        self.topic["task_plan"][0].update(task_type="Bugfix", slug="bugfix")
        create.save_json(self.parent / "repo-topic-map.json", self.topic)
        self.wire_git()
        result = create.create_batch(self.parent, self.source, self.parent / "repo-topic-map.json",
            preflight=self.parent / "startup-preflight.json", owner="fengyin-go", start_index=123)
        entry = result["tasks"][0]
        self.assertEqual(entry["folder"], "cc10003-01-bugfix")
        self.assertEqual(entry["task_id"], entry["folder"])
        self.assertEqual(entry["repo"], "fengyin-go/" + entry["folder"])
        task = self.parent / entry["folder"]
        self.assertTrue((task / "origin/.git").is_dir())
        self.assertEqual(workbook.parse_folder_name(task.name, self.parent)["number"], "123")
        row = workbook.read_workbook(self.parent / "tasks.xlsx")[0]
        self.assertEqual(row["子文件夹名称"], task.name)
        self.assertEqual(row["编号"], "123")
        self.assertTrue(create.create_batch(self.parent, self.source, self.parent / "repo-topic-map.json",
                owner="fengyin-go", start_index=123, verify=True)["ok"])

    def test_resume_legacy_batch_retains_frozen_ids(self):
        self.wire_git()
        result = self.build()
        entry = result["tasks"][0]
        old_folder = entry["folder"]
        entry.update(folder="s11-000001", task_id="s11-000001")
        entry.pop("number")
        (self.parent / old_folder).rename(self.parent / entry["folder"])
        create.save_json(self.parent / entry["folder"] / "task-seed.json", entry)
        create.save_json(self.parent / create.MANIFEST, {"tasks": [entry]})
        (self.parent / "tasks.xlsx").unlink()
        resumed = self.build()
        self.assertTrue(resumed["ok"])
        self.assertEqual(resumed["tasks"][0], entry)
        self.assertEqual(resumed["created_count"], 0)
        row = workbook.read_workbook(self.parent / "tasks.xlsx")[0]
        self.assertEqual(row["子文件夹名称"], "s11-000001")
        self.assertEqual(row["编号"], "1")
        with self.assertRaisesRegex(ValueError, "start-index"):
            create.create_batch(self.parent, self.source, self.parent / "repo-topic-map.json",
                preflight=self.parent / "startup-preflight.json", owner="fengyin-go", start_index=2)

    def test_repository_name_includes_source_sequence_and_type_slug(self):
        self.assertEqual(create.source_repo_name("https://github.com/acme/cc10001.git"), "cc10001")
        self.assertEqual(create.source_repo_name("git@github.com:acme/cc10001.git"), "cc10001")
        self.assertEqual(create.task_repo_name("cc10001", 1, "bugfix"), "cc10001-01-bugfix")
        self.assertEqual(create.task_repo_name("cc10001", 2, "bugfix"), "cc10001-02-bugfix")
        self.assertEqual(create.task_repo_name("cc10001", 3, "development"), "cc10001-03-development")
        with self.assertRaisesRegex(ValueError, "slug"):
            create.task_repo_name("cc10001", 1, "unknown")
        with self.assertRaisesRegex(ValueError, "01..99"):
            create.task_repo_name("cc10001", 100, "bugfix")

    def test_real_clones_handoff_and_main_advance(self):
        self.topic["task_plan"][0].update(task_type="功能优化", slug="optimization")
        create.save_json(self.parent / "repo-topic-map.json", self.topic)
        self.wire_git()
        result = self.build()
        task = self.parent / "cc10003-01-optimization"
        self.assertEqual(set(p.name for p in task.iterdir()), {"origin", "workspace", "task-seed.json"})
        self.assertFalse(any((task / "workspace").iterdir()))
        self.assertEqual(set(self.fake_heads(result["tasks"][0]["repo_url"])), {"main"})
        rows = workbook.read_workbook(self.parent / "tasks.xlsx")
        rows[0]["提示词"] = "真实测试夹具：新增订单导出接口，保留已有订单权限。"
        workbook.write_workbook(self.parent / "tasks.xlsx", rows)
        # Generation quality is covered by corpus/registry suites; isolate the file/reader integration here.
        metadata = {"common": {"repo_id": "orders-test", "domain": "测试领域", "subdirection": "测试方向",
                              "language": "Python", "frameworks": "stdlib", "repro_level": "已容器化，可一键起环境"},
                    "tasks": {task.name: {"requirements": [{"id": "R1", "text": "订单导出", "check": "make test"}],
                       "recording_plan": {"setup_cmd": "make setup", "demo": [{"cmd": "make test", "show": "ok"}]},
                       "dependency_smoke": {"ok": True, "commit": self.commit, "command": "make test",
                                            "evidence": "fixture log", "cleaned_up": True}}}}
        create.save_json(self.parent / "handoff-metadata.json", metadata)
        with patch.object(handoff, "validate_generation"):
            out = handoff.prepare(self.root, "b1111-01", self.parent / "handoff-metadata.json")
        self.assertTrue(out["ok"])
        lib = handoff.runner_lib()
        record = lib.load_task(task)
        self.assertEqual(lib.task_dirs(self.root, "b1111-01"), [task])
        self.assertEqual(record["execution_mode"], "single")
        self.assertEqual(record["branch"], "main")
        self.assertEqual(sh(task / "workspace", "symbolic-ref", "--short", "HEAD"), "main")
        self.assertFalse(sh(task / "workspace", "status", "--porcelain", "--ignored"))
        self.assertTrue(record["publish"]["initial_snapshot"].endswith("/commit/" + self.commit))
        (task / "workspace" / "answer.txt").write_text("artifact")
        sh(task / "workspace", "add", "answer.txt")
        sh(task / "workspace", "commit", "-qm", "artifact")
        artifact = sh(task / "workspace", "rev-parse", "HEAD")
        self.fake_push(record["publish"]["repo_url"], artifact, "main", task / "workspace")
        create.save_json(task / "state.json", {"stages": {"run": {"state": "ok"}}})
        self.assertEqual(create.verify_task(task, result["tasks"][0]), [])
        self.assertEqual(sh(task / "origin", "rev-parse", "HEAD"), self.commit)
        self.assertEqual(set(self.fake_heads(record["publish"]["repo_url"])), {"main"})
        self.assertEqual(lib.load_task(task)["initial_commit"], self.commit)
        (task / "originB").mkdir()
        with self.assertRaisesRegex(lib.FbrError, "A/B"):
            lib.load_task(task)

    def test_capacity_first_accepts_real_types_and_strict_ratio_is_opt_in(self):
        self.topic["task_plan"][0].update(task_type="Bugfix", slug="bugfix")
        self.assertEqual(validate_plan(self.topic)["Bugfix"], 1)
        self.topic["type_policy"] = "strict-ratio"
        from repo_topic_policy import normalize_topic_map
        self.topic = normalize_topic_map(self.topic)
        self.assertEqual(self.topic["type_policy"], "strict-ratio")
        with self.assertRaisesRegex(ValueError, "配比"):
            validate_plan(self.topic)

    def test_capacity_first_does_not_reduce_new_seats_to_suggested_ratio(self):
        from check_repo_theme import compute_capacity
        topic = {"max_capacity": 50, "topics": [{"id": "orders", "seat_capacity": 3}]}
        derived = {str(i): set() for i in range(10)}
        default = compute_capacity(derived, topic_map=topic, invented_auto=True)
        self.assertEqual(default["invented_slots"], 5)
        self.assertEqual(default["capacity"], 8)
        topic["type_policy"] = "strict-ratio"
        strict = compute_capacity(derived, topic_map=topic, invented_auto=True)
        self.assertEqual(strict["invented_slots"], 1)
        self.assertEqual(strict["capacity"], 4)

    def test_ratio_and_small_batch_rounding(self):
        self.assertEqual(list(allocate_counts(20).values()), [6, 6, 6, 1, 1])
        self.assertEqual(list(allocate_counts(30).values()), [9, 9, 9, 2, 1])
        for number in range(1, 101):
            actual = allocate_counts(number)
            self.assertEqual(sum(actual.values()), number)
            self.assertEqual(len(actual), 5)
        with self.assertRaises(ValueError):
            allocate_counts(-1)

    def test_scan_reads_seed_types_and_duplicate_batch_id_blocks(self):
        self.wire_git()
        self.build()
        found = workbook.scan(self.parent, None)
        self.assertEqual(found["pending_count"], 1)
        self.assertEqual(found["pending"][0]["task_type"], "功能开发")
        sibling = self.parent.parent / "b1111-02" / "cc10003-01-development"
        sibling.mkdir(parents=True)
        create.save_json(sibling / "task-seed.json", {"task_id": sibling.name})
        with self.assertRaisesRegex(ValueError, "其他批次"):
            self.build()

    def test_bugfix_is_a_first_prompt_not_a_repair_round_exemption(self):
        self.wire_git()
        self.build()
        rows = workbook.read_workbook(self.parent / "tasks.xlsx")
        rows[0].update({"任务类型": "Bugfix", "提示词": "修复一处未识别对象的错误", "提示词类型": "首轮"})
        workbook.write_workbook(self.parent / "tasks.xlsx", rows)
        from check_repo_theme import load_workbook_prompts
        _, repairs, exemptions = load_workbook_prompts(self.parent, "tasks.xlsx")
        self.assertFalse(repairs)
        self.assertFalse(exemptions)

    def test_invalid_startup_sha_and_unignored_dependency_block(self):
        self.wire_git()
        path = self.parent / "startup-preflight.json"
        evidence = create.read_json(path)
        evidence["startup_commit"] = "0" * 40
        create.save_json(path, evidence)
        with self.assertRaisesRegex(ValueError, "基准提交"):
            self.build()
        evidence["startup_commit"] = self.commit
        evidence["dependency_paths"] = ["src"]
        create.save_json(path, evidence)
        with self.assertRaisesRegex(ValueError, "忽略"):
            self.build()
        self.assertFalse(self.remote_repos)


if __name__ == "__main__":
    unittest.main()
