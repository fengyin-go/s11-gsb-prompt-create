#!/usr/bin/env python3
"""One prompt, one repository, one main branch, origin + workspace."""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

import batch_prompt_workbook as workbook
import github_helpers as hub
from check_repo_theme import compute_capacity, derive_repo_modules, derivation_health
from repo_topic_policy import load_topic_map
from s11_policy import TYPE_WEIGHTS, validate_plan

SCRIPT_DIR = Path(__file__).resolve().parent
MANIFEST = "s11-task-repos.json"
REPO_SUFFIX_BY_SLUG = {
    "development": "development",
    "bugfix": "bugfix",
    "optimization": "optimization",
    "refactor": "refactor",
    "migration": "migration",
}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def git(repo, *args):
    result = hub.run_git(["-c", "core.hooksPath=/dev/null", *args], cwd=Path(repo))
    if result.returncode:
        raise ValueError(result.stderr.strip() or "git 调用失败")
    return result.stdout.strip()


def source_repo_name(remote_url):
    """Return the source repository basename from HTTPS, SSH, or SCP Git URLs."""
    normalized = hub.normalize_repo_url(remote_url).rstrip("/")
    name = normalized.rsplit("/", 1)[-1]
    if not name or name in {".", ".."}:
        raise ValueError(f"无法从源仓库远端解析仓库名: {remote_url}")
    return name


def task_repo_name(base_name, sequence, slug):
    """Name a task repository after its source, sequence, and S11 task type."""
    suffix = REPO_SUFFIX_BY_SLUG.get(str(slug))
    if suffix is None:
        raise ValueError(f"不支持的 S11 任务类型 slug: {slug}")
    if not 1 <= int(sequence) <= 99:
        raise ValueError("任务仓库序号必须在 01..99")
    return f"{base_name}-{int(sequence):02d}-{suffix}"


def verify_task(target, entry, *, remote=True, workspace_required=False):
    problems = []
    for legacy in ("originA", "originB", "workspaceA", "workspaceB"):
        if (target / legacy).exists():
            problems.append(f"发现旧双侧目录 {legacy}；本入口不自动删除或迁移已有产物")
    origin = target / "origin"
    if not (origin / ".git").is_dir():
        return problems + ["origin 缺少独立 .git 克隆"]
    if git(origin, "rev-parse", "--abbrev-ref", "HEAD") != "main":
        problems.append("origin 必须在 main")
    if git(origin, "rev-parse", "HEAD") != entry["initial_commit"]:
        problems.append("origin 不在冻结初始提交")
    if git(origin, "status", "--porcelain"):
        problems.append("origin 有未提交改动")
    if hub.normalize_repo_url(git(origin, "remote", "get-url", "origin")) != entry["repo_url"]:
        problems.append("origin 指向其他任务仓库")
    for key, value in (("remote", "origin"), ("merge", "refs/heads/main")):
        if git(origin, "config", "--get", f"branch.main.{key}") != value:
            problems.append("origin 未跟踪 origin/main")
    workspace_dir = target / "workspace"
    if not workspace_dir.is_dir():
        problems.append("缺少 workspace")
    elif workspace_required or any(workspace_dir.iterdir()):
        if not (workspace_dir / ".git").is_dir():
            problems.append("workspace 必须是独立 Git 克隆")
        else:
            if git(workspace_dir, "rev-parse", "--abbrev-ref", "HEAD") != "main":
                problems.append("workspace 必须在 main")
            if hub.normalize_repo_url(git(workspace_dir, "remote", "get-url", "origin")) != entry["repo_url"]:
                problems.append("workspace 指向其他任务仓库")
    if remote:
        heads = hub.remote_heads(entry["repo_url"])
        if set(heads) != {"main"}:
            problems.append(f"远端必须仅有 main，实际 {sorted(heads)}")
        # main advances after running; the immutable initial commit remains in origin/task.json.
        if not (target / "state.json").exists() and heads.get("main") != entry["initial_commit"]:
            problems.append("运行前远端 main 不在初始提交")
    return problems


def validate_preflight(path, source, commit):
    evidence = read_json(path)
    required_true = ("startup_ok", "cleaned_up", "baseline_push_ok", "dependency_ignored")
    if any(evidence.get(k) is not True for k in required_true):
        raise ValueError("启动、清理、基线推送或依赖忽略证据未通过")
    if evidence.get("startup_commit") != commit or evidence.get("baseline_commit") != commit:
        raise ValueError("预检证据不属于最终基准提交")
    if not evidence.get("startup_command") or not evidence.get("startup_evidence"):
        raise ValueError("预检缺少启动命令或就绪证据")
    for rel in evidence.get("dependency_paths", []):
        path = Path(rel)
        if path.is_absolute() or ".." in path.parts or ".git" in path.parts or ".env" in path.parts:
            raise ValueError(f"不安全的依赖路径: {rel}")
        if not (source / path).is_dir():
            raise ValueError(f"缺少依赖种子目录: {rel}")
        if hub.run_git(["check-ignore", "-q", str(path) + "/"], cwd=source).returncode:
            raise ValueError(f"依赖没有被 Git 忽略: {rel}")
        if git(source, "ls-files", "--", str(path)):
            raise ValueError(f"依赖已被 Git 跟踪: {rel}")
    return evidence


def create_batch(parent, source, topic_path, *, preflight=None, owner=None, dry_run=False,
                 verify=False, start_index=1, visibility="public"):
    source, parent = Path(source).resolve(), Path(parent).resolve()
    commit = git(source, "rev-parse", "HEAD")
    if git(source, "status", "--porcelain"):
        raise ValueError("原始仓库有未提交改动；请先使用基准提交的干净克隆")
    if not 1 <= start_index <= 999999:
        raise ValueError("start-index 必须在 1..999999")
    remote = hub.source_remote_url(source)
    owner = owner or "fengyin-go"
    if owner != "fengyin-go":
        raise ValueError("数据仓库只允许创建在 fengyin-go 名下")
    base_repo_name = source_repo_name(remote)
    topic_map = load_topic_map(repo=source, parent=parent, explicit=topic_path,
                              required=True, expected_base_commit=commit)
    if topic_map.get("base_commit") != commit:
        raise ValueError("topic map 必须明确绑定最终基准提交")
    counts = validate_plan(topic_map)
    capacity = compute_capacity(derive_repo_modules(source), topic_map=topic_map, invented_auto=True)
    health = derivation_health(source)
    capacity["derivation_health"] = health
    if health.get("warning"):
        raise ValueError(f"容量扫描需修正: {health['warning']}")
    if len(topic_map["task_plan"]) > min(capacity["capacity"], int(topic_map.get("max_capacity") or 50)):
        raise ValueError("task_plan 超过真实仓库容量")
    evidence = None
    if not verify:
        if not preflight:
            raise ValueError("需要 --preflight 真实启动证据，不接受跳过启动预检")
        evidence = validate_preflight(preflight, source, commit)
        heads = hub.remote_heads(remote)
        if commit not in heads.values():
            raise ValueError("最终基准提交没有出现在原始仓库远端分支")
    old = read_json(parent / MANIFEST) if (parent / MANIFEST).is_file() else {"tasks": []}
    known = {item["folder"]: item for item in old["tasks"]}
    # A batch started before directory naming changed must retain every frozen ID.
    legacy = bool(known) and all(re.fullmatch(r"s11-\d{6}", name) for name in known)
    def folder_for(item):
        return (f"s11-{start_index + item['sequence'] - 1:06d}" if legacy else
                task_repo_name(base_repo_name, item["sequence"], item["slug"]))
    planned_names = {folder_for(item) for item in topic_map["task_plan"]}
    if set(known) - planned_names:
        raise ValueError("新计划遗漏既有任务或 start-index 已变；保留原任务编号和计划后再追加")
    journal_path=parent/'creation-journal.json'
    journal=read_json(journal_path) if journal_path.is_file() else {'source_repo':remote,'base_commit':commit,'tasks':{}}
    if journal.get('source_repo')!=remote or journal.get('base_commit')!=commit:raise ValueError('建仓恢复日志不属于当前基线')
    tasks, failed, created, skipped = [], [], [], []
    for item in topic_map["task_plan"]:
        number = start_index + item["sequence"] - 1
        if number > 999999:
            raise ValueError("任务编号超过六位上限")
        folder = folder_for(item)
        target = parent / folder
        for other in parent.parent.glob(f"*/{folder}"):
            if other.resolve() != target.resolve() and ((other / "task.json").exists() or (other / "task-seed.json").exists()):
                raise ValueError(f"任务编号 {folder} 已被其他批次使用: {other}；请使用独立的批次 root")
        entry = {"folder": folder, "task_id": folder, "number": number, "task_type": item["task_type"],
                 "initial_commit": commit, "branch": "main", "topic": item["topic"],
                 "family": item["family"], "angle": item["angle"], "sequence": item["sequence"]}
        if legacy:
            entry.pop("number")
        previous = known.get(folder)
        if previous:
            for key in entry:
                if previous.get(key) != entry[key]:
                    raise ValueError(f"已有任务 {folder} 的 {key} 发生变化；保留旧计划后只向末尾追加")
        if verify:
            if previous is None:
                failed.append({"folder": folder, "reason": "清单中没有该任务"})
                continue
            issues = verify_task(target, previous)
            (failed if issues else skipped).append({**previous, "problems": issues})
            tasks.append(previous)
            continue
        # A recorded repository is the only repository we may reuse. Same branch names alone do not prove ownership.
        if previous:
            entry.update({k: previous[k] for k in ("repo", "repo_url")})
            issues = verify_task(target, entry)
            if issues:
                raise ValueError(f"{folder}: {'；'.join(issues)}")
            skipped.append(entry)
        else:
            name = task_repo_name(base_repo_name, item["sequence"], item["slug"])
            progress=journal['tasks'].get(folder,{})
            exists=hub.repo_exists(owner,name)
            if exists and progress.get('stage') not in ('created','pushed','cloned','verified'):
                raise ValueError(f"候选仓库名已被占用且未登记在本批清单中，停止避免认领: {owner}/{name}")
            if progress and progress.get('repo') != f'{owner}/{name}':raise ValueError('建仓日志的仓库归属不一致')
            entry.update(repo=f"{owner}/{name}", repo_url=f"https://github.com/{owner}/{name}")
            if target.exists() and not progress:
                raise ValueError(f"目标 {target} 已存在但未登记，停止避免覆盖")
            if not dry_run:
                def checkpoint(stage):
                    journal['tasks'][folder]={'repo':entry['repo'],'initial_commit':commit,'stage':stage}
                    save_json(journal_path,journal)
                if not exists:
                    checkpoint('creating')
                    hub.create_repo(owner,name,visibility)
                    checkpoint('created')
                hub.push_commit_to_branch(entry['repo_url'],commit,'main',source)
                checkpoint('pushed')
                target.mkdir(parents=True,exist_ok=True)
                if not (target/'origin').exists():
                    hub.clone_branch(entry['repo_url'],'main',target/'origin',full_clone=False,
                                     git_user_name=None,git_user_email=None,local_source=source)
                checkpoint('cloned')
                (target/'workspace').mkdir(exist_ok=True)
                for rel in evidence.get("dependency_paths", []):
                    # macOS CoW clones; rsync preserves links and metadata elsewhere.
                    src, dst = source / rel, target / "origin" / rel
                    if dst.exists():continue
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    command = ["cp", "-Rc", str(src), str(dst)] if sys.platform == "darwin" else ["cp", "-a", str(src), str(dst)]
                    result = hub.run_cmd(command)
                    if result.returncode:
                        raise ValueError(result.stderr)
                issues = verify_task(target, entry)
                if issues:
                    raise ValueError(f"{folder}: {'；'.join(issues)}")
                checkpoint('verified')
                save_json(target / "task-seed.json", entry)
                # Persist each completed task so a failed later task is safely resumable.
                known[folder] = entry
                save_json(parent / MANIFEST, {"schema_version": 1, "source_repo": remote,
                          "base_commit": commit, "branch": "main", "tasks": list(known.values())})
            created.append(entry)
        tasks.append(entry)
    if not dry_run and not verify:
        rows = workbook.read_workbook(parent / "tasks.xlsx")
        by_name = {row["子文件夹名称"]: row for row in rows}
        if set(by_name) - {t["folder"] for t in tasks}:
            raise ValueError("工作簿含计划外任务，停止避免丢行")
        for task in tasks:
            if task["folder"] not in by_name:
                row = workbook.blank_record({"folder": task["folder"], "task_type": task["task_type"],
                                             "number": str(task.get("number", int(task["task_id"][4:]) if legacy else 0))})
                rows.append(row)
            elif by_name[task["folder"]]["任务类型"] != task["task_type"]:
                raise ValueError("已有工作簿任务类型与计划不一致")
        workbook.write_workbook(parent / "tasks.xlsx", rows)
        config = parent / "gsb-cross-project.json"
        if not config.exists():
            registry = Path(os.environ.get("GSB_CROSS_PROJECT_REGISTRY") or
                            SCRIPT_DIR.parent.parent / "gsb-prompt-create/data/cross-project-registry.json")
            save_json(config, {"registry": str(registry), "checker": str(SCRIPT_DIR / "check_cross_project_prompts.py"),
                      "require_registry": True, "require_coverage": True, "min_peer_batches": 1,
                      "bootstrap_no_history": False, "root_fallback": False})
    return {"ok": not failed, "dry_run": dry_run, "planned_count": len(tasks), "created_count": len(created),
            "skipped_count": len(skipped), "failed_count": len(failed), "failed": failed,
            "type_counts": counts, "type_weights": TYPE_WEIGHTS, "capacity": capacity,
            "structure": "origin + workspace", "branches": ["main"], "tasks": tasks,
            "workbook": str(parent / "tasks.xlsx"), "preflight": evidence,
            "next_step": "复制依赖后做无安装启动 smoke；生成提示词；prepare_handoff.py 交接"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", required=True, help="<root>/batches/<batch>")
    parser.add_argument("--source", required=True)
    parser.add_argument("--topic-map", required=True)
    parser.add_argument("--preflight")
    parser.add_argument("--owner")
    parser.add_argument("--visibility", choices=["public", "private", "internal"], default="public")
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        result = create_batch(args.parent, args.source, args.topic_map, preflight=args.preflight,
                              owner=args.owner, dry_run=args.dry_run, verify=args.verify,
                              start_index=args.start_index, visibility=args.visibility)
    except (ValueError, OSError, RuntimeError) as exc:
        result = {"ok": False, "blocking_reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
