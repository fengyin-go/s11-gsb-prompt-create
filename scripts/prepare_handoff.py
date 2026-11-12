#!/usr/bin/env python3
"""Freeze S11 prompts, baseline and recording plans for s11-feedback-run."""
from __future__ import annotations
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

import batch_prompt_workbook as workbook
from create_batch_tasks import MANIFEST, git, read_json, save_json, verify_task
import github_helpers as hub
from s11_policy import TYPE_WEIGHTS, validate_type_counts


def runner_lib():
    path = Path(__file__).resolve().parents[2] / "s11-feedback-run/scripts/fbrlib.py"
    spec = importlib.util.spec_from_file_location("s11_runner_lib", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_generation(parent, rows, origin):
    from check_prompt_g16 import check as g16_check
    from repo_topic_policy import load_topic_map
    prompts = [(row["子文件夹名称"], row["任务类型"], row["提示词"]) for row in rows]
    notes = {row["子文件夹名称"]: row.get("备注", "") for row in rows}
    if not g16_check(prompts, notes)["ok"]:
        raise ValueError("整批 G16/G19 难度闸门未通过")
    topic_map = load_topic_map(parent=parent, repo=origin, required=True,
                               expected_base_commit=git(origin, "rev-parse", "HEAD"))
    gates = workbook.run_write_gates(parent, rows, repo=origin, topic_map=topic_map, max_per_mode=3)
    if not gates["ok"]:
        raise ValueError(f"整批主题/难度/去重闸门未通过: {gates}")
    path, config = workbook.load_cross_project_config(parent)
    if not path:
        raise ValueError("缺少跨项目查重配置")
    result = workbook.run_cross_project_batch_gate(parent,path,config,rows)
    if not result.get('ok'):
        raise ValueError('整批跨项目查重未通过')


def prepare(root, batch, metadata_file):
    lib = runner_lib()
    root = lib.find_root(str(root))
    if not lib.BATCH_ID_RE.fullmatch(batch):
        raise ValueError("batch 必须为 bMMDD-NN")
    parent = root / "batches" / batch
    cfg = lib.settings(root)
    meta = read_json(metadata_file)
    manifest = read_json(parent / MANIFEST)
    entries = manifest["tasks"]
    rows = workbook.read_workbook(parent / "tasks.xlsx")
    by_name = {row["子文件夹名称"]: row for row in rows}
    if len(by_name) != len(rows) or set(by_name) != {e["task_id"] for e in entries}:
        raise ValueError("tasks.xlsx 与仓库清单任务缺失、重复或不一致")
    counts = {name: sum(r["任务类型"] == name for r in rows) for name in TYPE_WEIGHTS}
    topic_policy = read_json(parent / "repo-topic-map.json").get("type_policy", "capacity-first")
    validate_type_counts(counts, len(rows), policy=topic_policy)
    if not entries:
        raise ValueError("空批次不能交接")
    validate_generation(parent, rows, parent / entries[0]["task_id"] / "origin")
    import sys
    runtime_scripts = Path(__file__).resolve().parents[2]/'s11-feedback-run/scripts'
    if str(runtime_scripts) not in sys.path: sys.path.insert(0,str(runtime_scripts))
    import fbr_common
    common = dict(meta.get('common', {}))
    for key in fbr_common.FIELDS:
        candidates = [row.get(key) for row in [common,*meta.get('tasks',{}).values()] if row.get(key) not in (None,'')]
        if candidates and any(value != candidates[0] for value in candidates):
            raise ValueError(f'同一基线公共字段 {key} 存在冲突，不逐题生成或覆盖')
        if candidates: common[key]=candidates[0]
    for key in ('harness','harness_version','os'):
        if common.get(key) and common[key] != cfg[key]: raise ValueError(f'公共字段 {key} 与真实运行环境不符')
        common[key]=cfg[key]
    common_values={key:common.get(key) for key in fbr_common.FIELDS}
    profile_path, shared_profile=fbr_common.profile(root,
        {'source_repo':manifest.get('source_repo') or common.get('repo_id'), 'initial_commit':entries[0]['initial_commit'],'image_id':cfg['image_id']},
        common_values, {'metadata':str(Path(metadata_file).resolve()),'sha256':__import__('hashlib').sha256(Path(metadata_file).read_bytes()).hexdigest()})
    common.update(shared_profile['fields'])
    tasks, baselines, staged = [], {}, []
    for entry in entries:
        name = entry["task_id"]
        if not lib.TASK_ID_RE.fullmatch(name):
            raise ValueError(f"非法任务编号: {name}")
        target = parent / name
        if (target / "state.json").exists():
            raise ValueError(f"{name} 已有运行状态，禁止覆盖现有运行交接")
        problems = verify_task(target, entry)
        if problems:
            raise ValueError(f"{name}: {'；'.join(problems)}")
        row = by_name[name]
        prompt = row["提示词"]
        if not prompt.strip() or row["任务类型"] != entry["task_type"]:
            raise ValueError(f"{name} 缺少题面或题型与计划不一致")
        data = {**common, **meta.get("tasks", {}).get(name, {})}
        for key in ("repo_id", "domain", "subdirection", "language", "frameworks", "repro_level",
                    "requirements", "recording_plan", "dependency_smoke"):
            if not data.get(key):
                raise ValueError(f"{name} 缺少交接元数据 {key}")
        smoke = data["dependency_smoke"]
        if (smoke.get("commit") != entry["initial_commit"] or smoke.get("ok") is not True
                or not smoke.get("command") or not smoke.get("evidence") or smoke.get("cleaned_up") is not True):
            raise ValueError(f"{name} 无安装启动 smoke 证据不完整")
        if not isinstance(data["requirements"], list) or any(not r.get("id") or not r.get("text") or not r.get("check") for r in data["requirements"]):
            raise ValueError(f"{name} requirements 必须逐项含 id/text/check")
        recording = dict(data["recording_plan"], task_id=name)
        if not recording.get("setup_cmd") or not recording.get("demo"):
            raise ValueError(f"{name} 缺少录屏准备命令或演示命令")
        if any(not s.get("cmd") or not s.get("show") for s in recording["demo"]):
            raise ValueError(f"{name} 录屏 demo 需含 cmd/show")
        if not cfg.get("image_id"):
            raise ValueError("settings 缺少本机架构镜像 image_id；先真实预检，不填猜测值")
        initial = entry["initial_commit"]
        record = {k: data[k] for k in ("repo_id", "domain", "subdirection", "language", "frameworks", "repro_level", "requirements")}
        record.update(common_profile={"path":str(profile_path.relative_to(root)),"sha256":__import__("hashlib").sha256(profile_path.read_bytes()).hexdigest()}, task_id=name, batch=batch, task_type=entry["task_type"], prompt=prompt,
                      difficulty=data.get("difficulty", "困难"), initial_commit=initial, base_commit=initial,
                      task_repo=entry["repo"].split("/", 1)[1], task_repo_full=entry["repo"],
                      handoff_version=2, producer="s11-gsb-prompt-create", execution_mode="single",
                      branch="main", origin=f"{name}/origin", workspace=f"{name}/workspace")
        for key in ("image", "image_id", "harness", "harness_version", "os"):
            record[key] = cfg[key]
        baseline = {k: record[k] for k in ("repo_id", "domain", "subdirection", "language", "frameworks", "repro_level",
                                          "image", "image_id", "harness", "harness_version", "os")}
        baseline.update(commit=initial, tree=git(target / "origin", "rev-parse", "HEAD^{tree}"))
        record.update(baseline=baseline, baseline_id=lib.content_hash(baseline))
        record["publish"] = {"state": "published", "repo_url": entry["repo_url"],
                             "initial_snapshot": f"{entry['repo_url']}/commit/{initial}"}
        record["handoff_hash"] = lib.content_hash(lib.handoff_payload(record, recording))
        workspace_dir = target / "workspace"
        if not any(workspace_dir.iterdir()):
            # Clone committed source only. Cached host dependencies stay in origin and are not mounted into the task container.
            hub.clone_branch(entry["repo_url"], "main", workspace_dir, full_clone=False,
                             git_user_name=cfg.get("git_user_name"), git_user_email=cfg.get("git_user_email"), local_source=target/"origin")
        if git(workspace_dir, "rev-parse", "HEAD") != initial or git(workspace_dir, "status", "--porcelain", "--ignored"):
            raise ValueError(f"{name} workspace 不在干净初始提交")
        row_out = {k: record[k] for k in ("task_id", "repo_id", "initial_commit", "task_type", "difficulty", "baseline_id", "handoff_hash", "task_repo")}
        row_out.update(task_file=f"{name}/task.json", workspace=f"{name}/workspace", origin=f"{name}/origin",
                       producer=record["producer"], execution_mode="single", branch="main")
        tasks.append(row_out)
        baselines[record["baseline_id"]] = baseline
        staged.append((target, record, recording))
    batch_manifest = {"schema_version": 2, "batch_id": batch, "count": len(tasks), "tasks": tasks, "baselines": baselines,
                      "producer": "s11-gsb-prompt-create", "execution_mode": "single", "branch": "main"}
    for target, record, recording in staged:
        save_json(target / "task.json", record)
        save_json(target / "recording-plan.json", recording)
        (target / "prompt.txt").write_text(record["prompt"], encoding="utf-8")
    save_json(parent / "batch-manifest.json", batch_manifest)
    for target, _, _ in staged:
        lib.load_task(target)
    return {"ok": True, "count": len(tasks), "manifest": str(parent / "batch-manifest.json"),
            "next_step": f"fbr_run.py run --batch {batch}（先演练）"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--metadata", required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.root, args.batch, args.metadata)
    except Exception as exc:
        print(json.dumps({"ok": False, "blocking_reason": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
