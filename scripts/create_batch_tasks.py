#!/usr/bin/env python3
"""One prompt, one repository, one main branch, origin + workspace."""
from __future__ import annotations
import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import batch_prompt_workbook as workbook
import domain_fit
import difficulty_contract
import semantic_dedup
import check_prompt_type_intent
import github_helpers as hub
import create_source_snapshot as source_snapshot
from check_repo_theme import compute_capacity, derive_repo_modules, derivation_health
from repo_topic_policy import load_topic_map
from s11_policy import TYPE_WEIGHTS, validate_plan

SCRIPT_DIR = Path(__file__).resolve().parent
MANIFEST = "s11-task-repos.json"
QUEUE_FILE = "creation-queue.json"
QUEUE_SCHEMA_VERSION = 1
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


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@contextlib.contextmanager
def exclusive_file_lock(path, *, wait_seconds=0.0) -> Iterator[Path]:
    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - macOS/Linux workspace.
        raise RuntimeError("当前平台没有 fcntl，无法保证建仓队列单 worker") from exc

    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError(f"建仓队列已有 worker 在运行：{lock_path}")
                time.sleep(0.1)
        yield lock_path
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def queue_path(parent):
    return Path(parent).resolve() / QUEUE_FILE


def load_queue(parent):
    path = queue_path(parent)
    if not path.is_file():
        raise ValueError(f"建仓队列不存在：{path}")
    payload = read_json(path)
    if (
        not isinstance(payload, dict)
        or int(payload.get("schema_version") or 0) != QUEUE_SCHEMA_VERSION
        or not isinstance(payload.get("tasks"), list)
    ):
        raise ValueError(f"建仓队列格式不受支持：{path}")
    return payload


def save_queue(parent, payload):
    payload["updated_at"] = now_iso()
    save_json(queue_path(parent), payload)


def _queue_summary(parent, queue):
    counts = {}
    for item in queue.get("tasks", []):
        status = str(item.get("status") or "pending")
        counts[status] = counts.get(status, 0) + 1
    return {
        "ok": queue.get("status") not in {"needs_attention", "failed"},
        "queue": str(queue_path(parent)),
        "status": queue.get("status"),
        "source_repo": queue.get("source_repo"),
        "base_commit": queue.get("base_commit"),
        "task_count": len(queue.get("tasks", [])),
        "status_counts": counts,
        "updated_at": queue.get("updated_at"),
        "worker": queue.get("worker") or {},
        "tasks": queue.get("tasks", []),
    }


def _retryable_queue_error(message):
    lowered = str(message or "").lower()
    return any(marker in lowered for marker in (
        "rate limit",
        "secondary rate",
        "http 429",
        "too many requests",
        "too many repositories",
        "too quickly",
        "限流",
        "等待预算",
        "timeout",
        "timed out",
        "connection reset",
        "tls handshake",
        "network is unreachable",
        "temporary failure",
    ))


def _fatal_queue_error(message):
    lowered = str(message or "").lower()
    return any(marker in lowered for marker in (
        "fengyin-solo",
        "禁止开始",
        "立即停止",
        "当前活跃 github 账号",
        "无法确认本机当前活跃 github 账号",
        "active github account",
        "bad credentials",
        "authentication failed",
        "requires authentication",
        "http 401",
        "http 403",
        "forbidden",
        "未授权",
        "认证失败",
    ))


def _retry_delay_seconds(attempts):
    """Queue-level backoff after a retryable failure escapes the GitHub helper."""
    return min(60 * 2 ** min(max(int(attempts) - 1, 0), 4), 900)


def _wait_for_retry(item):
    raw = str(item.get("next_retry_at") or "").strip()
    if not raw:
        return
    try:
        retry_at = datetime.fromisoformat(raw)
    except ValueError:
        return
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc).astimezone()
    remaining = (retry_at - datetime.now(timezone.utc).astimezone()).total_seconds()
    if remaining <= 0:
        return
    folder = str(item.get("folder") or "unknown")
    print(
        f"{folder}：等待重试时间，剩余 {int(remaining)} 秒",
        file=sys.stderr,
        flush=True,
    )
    while remaining > 0:
        step = min(remaining, 30)
        time.sleep(step)
        remaining -= step


def _queue_call_args(queue, **overrides):
    values = {
        "parent": queue["parent"],
        "source": queue["source"],
        "topic_path": queue["topic_map"],
        "preflight": queue["preflight"],
        "owner": queue.get("owner"),
        "dry_run": False,
        "verify": False,
        "start_index": int(queue.get("start_index") or 1),
        "visibility": str(queue.get("visibility") or "public"),
        "snapshot_manifest": queue.get("snapshot_manifest"),
    }
    values.update(overrides)
    return values


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


@workbook._locked_workbook
def _seed_task_rows(parent, tasks):
    path = parent / 'tasks.xlsx'
    rows = workbook.read_workbook(parent / "tasks.xlsx")
    by_name = {row["子文件夹名称"]: row for row in rows}
    if set(by_name) - {t["folder"] for t in tasks}:
        raise ValueError("工作簿含计划外任务，停止避免丢行")
    for task in tasks:
        if task["folder"] not in by_name:
            number = task.get("number")
            if number is None:
                number = int(task["task_id"][4:]) if re.fullmatch(r"s11-\d{6}", task["task_id"]) else task.get("sequence", 0)
            row = workbook.blank_record({"folder": task["folder"], "task_type": task["task_type"],
                                         "number": str(number)})
            rows.append(row)
        elif by_name[task["folder"]]["任务类型"] != task["task_type"]:
            raise ValueError("已有工作簿任务类型与计划不一致")
    if path.exists() and len(rows) == len(by_name):
        return
    config_path, config = workbook.load_cross_project_config(parent)
    if not config_path:
        workbook.write_workbook(path, rows)
        return
    registry = workbook.resolve_config_path(config['registry'], config_path.parent)
    with workbook.cross_registry_lib.registry_lock(registry):
        with workbook._workbook_transaction(path, parent=parent, registry=registry) as transaction:
            workbook.write_workbook(path, rows)
            workbook._register_workbook(registry, parent, path, rows, transaction)


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


def validate_preflight(path, source, commit, snapshot=None):
    evidence = read_json(path)
    required_true = (
        ("startup_ok", "cleaned_up", "dependency_ignored")
        if snapshot else
        ("startup_ok", "cleaned_up", "baseline_push_ok", "dependency_ignored")
    )
    if any(evidence.get(k) is not True for k in required_true):
        label = "启动、清理或依赖忽略" if snapshot else "启动、清理、基线推送或依赖忽略"
        raise ValueError(f"{label}证据未通过")
    if evidence.get("startup_commit") != commit or evidence.get("baseline_commit") != commit:
        raise ValueError("预检证据不属于最终基准提交")
    if snapshot:
        if evidence.get("source_mode") != "directory_snapshot":
            raise ValueError("本地快照模式预检必须填写 source_mode=directory_snapshot")
        if evidence.get("snapshot_commit") != commit:
            raise ValueError("预检 snapshot_commit 与冻结快照不一致")
        if evidence.get("snapshot_id") != snapshot.get("snapshot_id"):
            raise ValueError("预检 snapshot_id 与源码快照清单不一致")
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
                 verify=False, start_index=1, visibility="public", snapshot_manifest=None,
                 only_folders=None, write_batch_outputs=True, plan_only=False):
    source, parent = Path(source).resolve(), Path(parent).resolve()
    snapshot = (
        source_snapshot.validate_snapshot(source, snapshot_manifest)
        if snapshot_manifest else None
    )
    commit = git(source, "rev-parse", "HEAD")
    if git(source, "status", "--porcelain"):
        raise ValueError("原始仓库有未提交改动；请先使用基准提交的干净克隆")
    if not 1 <= start_index <= 999999:
        raise ValueError("start-index 必须在 1..999999")
    remote = None if snapshot else hub.source_remote_url(source)
    owner = hub.resolve_repository_owner(owner)
    source_reference = snapshot["snapshot_id"] if snapshot else remote
    base_repo_name = snapshot["source_name"] if snapshot else source_repo_name(remote)
    topic_map = load_topic_map(repo=source, parent=parent, explicit=topic_path,
                              required=True, expected_base_commit=commit)
    existing_batch = any(
        path.exists()
        for path in (
            parent / MANIFEST,
            parent / QUEUE_FILE,
        )
    ) or any(
        (child / "task.json").is_file()
        for child in parent.iterdir()
        if child.is_dir()
    ) if parent.exists() else False
    semantic_errors = semantic_dedup.map_errors(
        topic_map,
        require=not existing_batch,
    )
    if semantic_errors:
        raise ValueError("提示词语义查重契约未通过：" + "；".join(semantic_errors))
    domain_errors = domain_fit.map_errors(topic_map, source)
    if domain_errors:
        raise ValueError("领域题位契约未通过：" + "；".join(domain_errors))
    type_intent_errors = check_prompt_type_intent.hint_errors(topic_map)
    if type_intent_errors:
        raise ValueError("任务类型语义未通过：" + "；".join(type_intent_errors))
    difficulty_errors = difficulty_contract.map_errors(topic_map, source)
    if difficulty_errors:
        raise ValueError("困难设计契约未通过：" + "；".join(difficulty_errors))
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
        evidence = validate_preflight(preflight, source, commit, snapshot=snapshot)
        if snapshot is None:
            heads = hub.remote_heads(remote)
            if commit not in heads.values():
                raise ValueError("最终基准提交没有出现在原始仓库远端分支")
    if visibility == 'public' and not verify:
        from baseline_secrets import check_public_commit
        check_public_commit(source, commit)
    old = read_json(parent / MANIFEST) if (parent / MANIFEST).is_file() else {"tasks": []}
    known = {item["folder"]: item for item in old["tasks"]}
    if not known and len(topic_map["task_plan"]) != int(capacity["capacity"]):
        raise ValueError(
            "新批次必须按当前有效容量生成题位："
            f"capacity={capacity['capacity']}，task_plan={len(topic_map['task_plan'])}"
        )
    # A batch started before directory naming changed must retain every frozen ID.
    legacy = bool(known) and all(re.fullmatch(r"s11-\d{6}", name) for name in known)
    def folder_for(item):
        return (f"s11-{start_index + item['sequence'] - 1:06d}" if legacy else
                task_repo_name(base_repo_name, item["sequence"], item["slug"]))
    planned_names = {folder_for(item) for item in topic_map["task_plan"]}
    if set(known) - planned_names:
        raise ValueError("新计划遗漏既有任务或 start-index 已变；保留原任务编号和计划后再追加")
    journal_path=parent/'creation-journal.json'
    journal=read_json(journal_path) if journal_path.is_file() else {'source_repo':source_reference,'base_commit':commit,'tasks':{}}
    if journal.get('source_repo')!=source_reference or journal.get('base_commit')!=commit:raise ValueError('建仓恢复日志不属于当前基线')
    source_manifest_fields = {
        "source_repo": source_reference,
        "base_commit": commit,
        "source_mode": "directory_snapshot" if snapshot else "git_remote",
    }
    if snapshot:
        source_manifest_fields.update({
            "source_name": snapshot["source_name"],
            "source_dir": snapshot["source_dir"],
            "snapshot_manifest": str(Path(snapshot_manifest).expanduser().resolve()),
            "snapshot_id": snapshot["snapshot_id"],
            "snapshot_tree": snapshot["snapshot_tree"],
            "snapshot_file_count": snapshot["file_count"],
        })
    tasks, failed, created, skipped = [], [], [], []
    selected_folders = set(only_folders) if only_folders is not None else None
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
        if selected_folders is not None and folder not in selected_folders:
            if previous:
                entry.update({k: previous[k] for k in ("repo", "repo_url")})
            tasks.append(entry)
            continue
        if previous:
            for key in entry:
                if previous.get(key) != entry[key]:
                    raise ValueError(f"已有任务 {folder} 的 {key} 发生变化；保留旧计划后只向末尾追加")
        if plan_only:
            if previous:
                entry.update({k: previous[k] for k in ("repo", "repo_url")})
            else:
                name = task_repo_name(base_repo_name, item["sequence"], item["slug"])
                entry.update(repo=f"{owner}/{name}", repo_url=f"https://github.com/{owner}/{name}")
            tasks.append(entry)
            continue
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
            if exists and progress.get('stage') == 'creating' and progress.get('repo') == f'{owner}/{name}':
                # A throttled create request can leave the remote repository created without
                # returning success. Adopt it only when the recovery journal records the exact
                # owner/name/commit and the remote contains just that commit on main.
                heads = hub.remote_heads(entry['repo_url'])
                if set(heads) != {'main'} or heads.get('main') != commit:
                    raise ValueError(
                        f"建仓恢复发现未登记的已有仓库 {owner}/{name}，"
                        "且远端内容不属于当前基准；停止避免认领"
                    )
                progress = {**progress, 'stage': 'created'}
                journal['tasks'][folder] = progress
                save_json(journal_path, journal)
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
                save_json(parent / MANIFEST, {
                    "schema_version": 1,
                    **source_manifest_fields,
                    "branch": "main",
                    "tasks": list(known.values()),
                })
            created.append(entry)
        tasks.append(entry)
    if not dry_run and not verify and write_batch_outputs:
        _seed_task_rows(parent, tasks)
        config = parent / "gsb-cross-project.json"
        if not config.exists():
            registry = Path(os.environ.get("GSB_CROSS_PROJECT_REGISTRY") or
                            SCRIPT_DIR.parent.parent / "gsb-prompt-create/data/cross-project-registry.json")
            save_json(config, {"registry": str(registry), "checker": str(SCRIPT_DIR / "check_cross_project_prompts.py"),
                      "require_platform_corpus": True, "require_registry": True, "require_coverage": True, "min_peer_batches": 1,
                      "bootstrap_no_history": False, "root_fallback": False})
    return {"ok": not failed, "dry_run": dry_run, "planned_count": len(tasks), "created_count": len(created),
            "skipped_count": len(skipped), "failed_count": len(failed), "failed": failed,
            "type_counts": counts, "type_weights": TYPE_WEIGHTS, "capacity": capacity,
            "structure": "origin + workspace", "branches": ["main"], "tasks": tasks,
            "workbook": str(parent / "tasks.xlsx"), "preflight": evidence,
            "source_mode": source_manifest_fields["source_mode"],
            "source_name": base_repo_name,
            "source_repo": source_reference,
            "base_commit": commit,
            "next_step": "复制依赖后做无安装启动 smoke；生成提示词；prepare_handoff.py 交接"}


def freeze_queue(parent, source, topic_path, *, preflight, owner=None, start_index=1,
                 visibility="public", snapshot_manifest=None):
    parent = Path(parent).expanduser().resolve()
    source = Path(source).expanduser().resolve()
    topic_path = Path(topic_path).expanduser().resolve()
    preflight = Path(preflight).expanduser().resolve()
    snapshot_path = (
        Path(snapshot_manifest).expanduser().resolve()
        if snapshot_manifest
        else None
    )
    planned = create_batch(
        parent,
        source,
        topic_path,
        preflight=preflight,
        owner=owner,
        dry_run=True,
        plan_only=True,
        start_index=start_index,
        visibility=visibility,
        snapshot_manifest=snapshot_path,
    )
    if not planned.get("ok"):
        raise ValueError("无法冻结建仓队列：" + json.dumps(planned, ensure_ascii=False))
    previous = load_queue(parent) if queue_path(parent).is_file() else {}
    if previous and (
        previous.get("source_repo") != planned.get("source_repo")
        or previous.get("base_commit") != planned.get("base_commit")
    ):
        raise ValueError("已有建仓队列不属于当前源码基线；不能覆盖或混用")
    previous_tasks = {
        str(item.get("folder") or ""): item
        for item in previous.get("tasks", [])
        if isinstance(item, dict)
    }
    tasks = []
    for task in planned.get("tasks", []):
        folder = str(task["folder"])
        old = previous_tasks.get(folder, {})
        if old and (
            old.get("repo") not in (None, task.get("repo"))
            or old.get("initial_commit") not in (None, task.get("initial_commit"))
        ):
            raise ValueError(f"建仓队列中的 {folder} 与当前冻结计划不一致")
        tasks.append({
            "folder": folder,
            "repo": str(task.get("repo") or ""),
            "repo_url": str(task.get("repo_url") or ""),
            "initial_commit": str(task.get("initial_commit") or ""),
            "stage": str(old.get("stage") or "pending"),
            "status": str(old.get("status") or "pending"),
            "attempts": int(old.get("attempts") or 0),
            "next_retry_at": str(old.get("next_retry_at") or ""),
            "error": str(old.get("error") or ""),
            "updated_at": str(old.get("updated_at") or now_iso()),
        })
    queue_owner = str(owner or "").strip()
    if not queue_owner and tasks and tasks[0]["repo"]:
        queue_owner = tasks[0]["repo"].split("/", 1)[0]
    queue = {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "status": "queued" if any(item["status"] != "verified" for item in tasks) else "complete",
        "created_at": str(previous.get("created_at") or now_iso()),
        "updated_at": now_iso(),
        "source_repo": planned.get("source_repo"),
        "base_commit": planned.get("base_commit"),
        "source_mode": planned.get("source_mode"),
        "parent": str(parent),
        "source": str(source),
        "topic_map": str(topic_path),
        "preflight": str(preflight),
        "snapshot_manifest": str(snapshot_path) if snapshot_path else "",
        "owner": queue_owner,
        "start_index": int(start_index),
        "visibility": visibility,
        "worker": previous.get("worker") or {},
        "final_verification": previous.get("final_verification") or {},
        "tasks": tasks,
    }
    save_queue(parent, queue)
    return _queue_summary(parent, queue)


def queue_status(parent):
    queue = load_queue(parent)
    return _queue_summary(parent, queue)


def _queue_update_task(parent, folder, **changes):
    queue = load_queue(parent)
    for item in queue.get("tasks", []):
        if item.get("folder") == folder:
            item.update(changes)
            item["updated_at"] = now_iso()
            break
    else:
        raise ValueError(f"建仓队列中没有任务：{folder}")
    save_queue(parent, queue)
    return queue


def run_queue_worker(parent):
    parent = Path(parent).expanduser().resolve()
    queue = load_queue(parent)
    if queue.get("status") == "complete":
        return _queue_summary(parent, queue)
    with exclusive_file_lock(parent / ".creation-worker.lock"):
        queue = load_queue(parent)
        queue["status"] = "running"
        queue["worker"] = {
            "pid": os.getpid(),
            "started_at": now_iso(),
        }
        save_queue(parent, queue)
        stop_worker = False
        for item in list(queue.get("tasks", [])):
            if item.get("status") == "verified":
                continue
            if item.get("status") == "retry_wait":
                _wait_for_retry(item)
            folder = str(item["folder"])
            queue = _queue_update_task(
                parent,
                folder,
                status="running",
                error="",
                next_retry_at="",
            )
            item = next(
                task for task in queue["tasks"] if task.get("folder") == folder
            )
            try:
                result = create_batch(
                    **_queue_call_args(
                        queue,
                        only_folders={folder},
                        write_batch_outputs=False,
                    )
                )
                if not result.get("ok"):
                    raise ValueError(
                        f"{folder} 建仓任务未完成："
                        + json.dumps(result, ensure_ascii=False)
                    )
                journal_path = parent / "creation-journal.json"
                journal = read_json(journal_path) if journal_path.is_file() else {}
                stage = str(
                    ((journal.get("tasks") or {}).get(folder) or {}).get("stage")
                    or "verified"
                )
                _queue_update_task(
                    parent,
                    folder,
                    status="verified",
                    stage=stage,
                    attempts=int(item.get("attempts") or 0) + 1,
                    error="",
                    next_retry_at="",
                )
            except Exception as exc:  # noqa: BLE001 - preserve every item failure in the queue.
                message = str(exc)
                retryable = _retryable_queue_error(message)
                fatal = _fatal_queue_error(message)
                attempts = int(item.get("attempts") or 0) + 1
                retry_at = (
                    (
                        datetime.now(timezone.utc)
                        + timedelta(seconds=_retry_delay_seconds(attempts))
                    )
                    .astimezone()
                    .isoformat(timespec="seconds")
                    if retryable
                    else ""
                )
                _queue_update_task(
                    parent,
                    folder,
                    status="retry_wait" if retryable else "failed",
                    attempts=attempts,
                    error=message,
                    next_retry_at=retry_at,
                )
                if fatal:
                    stop_worker = True
            if stop_worker:
                break
        queue = load_queue(parent)
        statuses = {str(item.get("status") or "") for item in queue.get("tasks", [])}
        if statuses == {"verified"}:
            try:
                final = create_batch(
                    **_queue_call_args(queue, write_batch_outputs=True)
                )
                queue["final_verification"] = {
                    "ok": bool(final.get("ok")),
                    "completed_at": now_iso(),
                    "planned_count": final.get("planned_count"),
                    "failed_count": final.get("failed_count"),
                }
                queue["status"] = "complete" if final.get("ok") else "needs_attention"
            except Exception as exc:  # noqa: BLE001
                queue["final_verification"] = {
                    "ok": False,
                    "completed_at": now_iso(),
                    "error": str(exc),
                }
                queue["status"] = "retry_wait" if _retryable_queue_error(exc) else "needs_attention"
        elif "retry_wait" in statuses:
            queue["status"] = "retry_wait"
        else:
            queue["status"] = "needs_attention"
        queue["worker"]["finished_at"] = now_iso()
        save_queue(parent, queue)
        return _queue_summary(parent, queue)


def launch_queue_worker(parent):
    parent = Path(parent).expanduser().resolve()
    queue = load_queue(parent)
    if queue.get("status") == "complete":
        return {
            "ok": True,
            "detached": False,
            "status": "complete",
            "queue": _queue_summary(parent, queue),
        }
    log_path = parent / "creation-worker.log"
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--parent",
                str(parent),
                "--worker",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    queue = load_queue(parent)
    queue["worker"] = {
        "pid": process.pid,
        "started_at": now_iso(),
        "log": str(log_path),
    }
    save_queue(parent, queue)
    return {
        "ok": True,
        "detached": True,
        "pid": process.pid,
        "log": str(log_path),
        "queue": _queue_summary(parent, queue),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", required=True, help="<root>/batches/<batch>")
    parser.add_argument("--source")
    parser.add_argument(
        "--snapshot-manifest",
        help="本地目录快照清单；提供后跳过原仓库 origin 远端与 baseline_push_ok 要求",
    )
    parser.add_argument("--topic-map")
    parser.add_argument("--preflight")
    parser.add_argument("--owner")
    parser.add_argument("--visibility", choices=["public", "private", "internal"], default="public")
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--queue",
        action="store_true",
        help="冻结完整建仓计划到 creation-queue.json，不在前台逐个建仓",
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help="与 --queue/--resume 搭配，启动独立后台 worker 并立即返回状态",
    )
    parser.add_argument(
        "--worker",
        action="store_true",
        help="内部模式：前台消费已冻结的建仓队列",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="从 creation-queue.json 恢复未完成建仓",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="只读输出建仓队列状态",
    )
    args = parser.parse_args()
    try:
        if args.status:
            result = queue_status(args.parent)
        elif args.worker or args.resume:
            result = (
                launch_queue_worker(args.parent)
                if args.detach
                else run_queue_worker(args.parent)
            )
        else:
            if not args.source or not args.topic_map:
                raise ValueError("同步建仓或冻结队列需要 --source 和 --topic-map")
            if args.queue:
                if args.verify:
                    raise ValueError("--queue 不能与 --verify 混用")
                if args.dry_run and args.detach:
                    raise ValueError("--dry-run 不能启动后台 worker")
                result = freeze_queue(
                    args.parent,
                    args.source,
                    args.topic_map,
                    preflight=args.preflight,
                    owner=args.owner,
                    start_index=args.start_index,
                    visibility=args.visibility,
                    snapshot_manifest=args.snapshot_manifest,
                )
                if args.detach:
                    result = launch_queue_worker(args.parent)
            else:
                result = create_batch(
                    args.parent,
                    args.source,
                    args.topic_map,
                    preflight=args.preflight,
                    owner=args.owner,
                    dry_run=args.dry_run,
                    verify=args.verify,
                    start_index=args.start_index,
                    visibility=args.visibility,
                    snapshot_manifest=args.snapshot_manifest,
                )
    except (ValueError, OSError, RuntimeError) as exc:
        result = {"ok": False, "blocking_reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
