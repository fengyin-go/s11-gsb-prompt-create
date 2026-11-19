#!/usr/bin/env python3
"""Freeze S11 prompts, baseline and recording plans for s11-feedback-run."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import batch_prompt_workbook as workbook
import check_prompt_opportunities
import domain_fit
import prompt_style
import platform_contract
import review_contract
from create_batch_tasks import MANIFEST, git, read_json, save_json, verify_task
import github_helpers as hub
from repo_topic_policy import load_topic_map
from s11_policy import TYPE_WEIGHTS, validate_type_counts


def runner_lib():
    path = Path(__file__).resolve().parents[2] / "s11-feedback-run/scripts/fbrlib.py"
    spec = importlib.util.spec_from_file_location("s11_runner_lib", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_generation(parent, rows, origin, metadata=None):
    from check_prompt_g16 import check as g16_check
    from repo_topic_policy import load_topic_map
    prompts = [(row["子文件夹名称"], row["任务类型"], row["提示词"]) for row in rows]
    if not g16_check(prompts, batch_dir=parent)["ok"]:
        raise ValueError("整批 G16/G19 难度闸门未通过")
    style_errors = prompt_style.prompt_errors(rows)
    if style_errors:
        raise ValueError("题面表达闸门未通过：" + "；".join(style_errors))
    topic_map = load_topic_map(parent=parent, repo=origin, required=True,
                               expected_base_commit=git(origin, "rev-parse", "HEAD"))
    review_errors = review_contract.errors(rows, metadata or {})
    if review_errors:
        raise ValueError("验收反馈契约未通过：" + "；".join(review_errors))
    gates = workbook.run_write_gates(parent, rows, repo=origin, topic_map=topic_map, max_per_mode=3)
    if not gates["ok"]:
        raise ValueError(f"整批主题/难度/去重闸门未通过: {gates}")
    path, config = workbook.load_cross_project_config(parent)
    if not path:
        raise ValueError("缺少跨项目查重配置")
    result = workbook.run_cross_project_batch_gate(parent,path,config,rows)
    if not result.get('ok'):
        raise ValueError('整批跨项目查重未通过')


def validate_prompt_opportunity(parent, origin, topic_map_path):
    """Revalidate the generation provenance before freezing a handoff."""
    manifest_path = parent / workbook.MANIFEST_FILENAME
    try:
        manifest = read_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"缺少或无法读取提示词生成清单：{manifest_path}: {exc}") from exc
    expected_workbook = parent / "tasks.xlsx"
    if Path(manifest.get("workbook") or "").expanduser().resolve() != expected_workbook.resolve():
        raise ValueError("提示词生成清单的 workbook 与当前批次不一致")
    if Path(manifest.get("repo") or "").expanduser().resolve() != origin.resolve():
        raise ValueError("提示词生成清单的 repo 与当前任务 origin 不一致")
    recorded_workbook_sha = str(manifest.get("workbook_sha256") or "")
    if not recorded_workbook_sha:
        raise ValueError("提示词生成清单缺少 workbook_sha256")
    actual_workbook_sha = hashlib.sha256(expected_workbook.read_bytes()).hexdigest()
    if recorded_workbook_sha != actual_workbook_sha:
        raise ValueError("tasks.xlsx 在提示词生成后发生变化，必须重新执行完整写入闸门")

    recorded_opportunity = manifest.get("prompt_opportunities")
    if not isinstance(recorded_opportunity, dict) or recorded_opportunity.get("ok") is not True:
        raise ValueError("提示词生成清单缺少已通过的外部机会地图记录")
    raw_path = str(recorded_opportunity.get("opportunity_map") or "").strip()
    if not raw_path:
        raise ValueError("提示词生成清单缺少机会地图路径")
    opportunity_path = Path(raw_path).expanduser()
    if not opportunity_path.is_absolute():
        opportunity_path = parent / opportunity_path
    opportunity_path = opportunity_path.resolve()
    if opportunity_path.is_relative_to(origin.resolve()):
        raise ValueError("机会地图不得位于任务 origin 仓库内")
    for workspace in parent.glob("*/workspace"):
        if opportunity_path.is_relative_to(workspace.resolve()):
            raise ValueError("机会地图不得进入 workspace")

    checked = check_prompt_opportunities.check(origin, topic_map_path, opportunity_path)
    if checked.get("ok") is not True:
        raise ValueError(
            "交接前机会地图复核未通过："
            + "；".join(checked.get("errors") or ["未知错误"])
        )
    recorded_map_sha = (
        recorded_opportunity.get("sha256")
        or recorded_opportunity.get("opportunity_sha256")
    )
    if str(recorded_map_sha or "") != str(checked.get("opportunity_sha256") or ""):
        raise ValueError("提示词生成清单的 sha256 与当前机会地图不一致")
    for key in ("repo_topic_map_sha256", "source_commit"):
        if str(recorded_opportunity.get(key) or "") != str(checked.get(key) or ""):
            raise ValueError(f"提示词生成清单的 {key} 与当前机会地图不一致")
    return {
        "path": (
            str(opportunity_path.relative_to(parent))
            if opportunity_path.is_relative_to(parent)
            else str(opportunity_path)
        ),
        "sha256": checked["opportunity_sha256"],
        "source_commit": checked["source_commit"],
        "source_base_commit": checked.get("source_base_commit"),
        "repo_topic_map_sha256": checked.get("repo_topic_map_sha256"),
        "dedup_contract_version": checked.get("dedup_contract_version"),
        "target_count": checked.get("target_count"),
        "selection_policy": checked.get("selection_policy"),
        "selected_count": checked.get("selected_count"),
        "reserve_count": checked.get("reserve_count"),
        "mode_counts": checked.get("mode_counts"),
        "reserve_mode_counts": checked.get("reserve_mode_counts"),
    }


def prepare(root, batch, metadata_file):
    lib = runner_lib()
    root = lib.find_root(str(root))
    hub.active_github_account()
    if not lib.BATCH_ID_RE.fullmatch(batch):
        raise ValueError("batch 必须为 bMMDD-NN")
    parent = root / "batches" / batch
    return _prepare_locked(parent, root, batch, metadata_file, lib)


@workbook._locked_workbook
def _prepare_locked(parent, root, batch, metadata_file, lib):
    cfg = lib.settings(root)
    meta = read_json(metadata_file)
    manifest = read_json(parent / MANIFEST)
    entries = manifest["tasks"]
    if not entries:
        raise ValueError("空批次不能交接")
    rows = workbook.read_workbook(parent / "tasks.xlsx")
    by_name = {row["子文件夹名称"]: row for row in rows}
    if len(by_name) != len(rows) or set(by_name) != {e["task_id"] for e in entries}:
        raise ValueError("tasks.xlsx 与仓库清单任务缺失、重复或不一致")
    counts = {name: sum(r["任务类型"] == name for r in rows) for name in TYPE_WEIGHTS}
    topic_map = load_topic_map(parent=parent, repo=parent / entries[0]["task_id"] / "origin", required=True)
    topic_policy = topic_map.get("type_policy", "capacity-first")
    validate_type_counts(counts, len(rows), policy=topic_policy)
    platform_errors, meta = platform_contract.validate(meta, by_name, topic_map, cfg)
    if platform_errors:
        raise ValueError("平台字段生成校验未通过：" + "；".join(platform_errors))
    first_origin = parent / entries[0]["task_id"] / "origin"
    validate_generation(parent, rows, first_origin, meta)
    opportunity_binding = validate_prompt_opportunity(parent, first_origin, parent / "repo-topic-map.json")
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
    if common.get("domain") != topic_map.get("domain") or common.get("subdirection") != topic_map.get("subdirection"):
        raise ValueError("handoff metadata 的 domain/subdirection 必须与 repo-topic-map.json 一致")
    receipt_path = parent / workbook.MANIFEST_FILENAME
    receipt = read_json(receipt_path)
    cross = receipt.get('cross_project')
    if not isinstance(cross, dict) or receipt.get('receipt_version') != 2 or any(value is not True for value in (
            receipt.get('gate_ok'), receipt.get('raw_a5_ok'),
            cross.get('ok'), cross.get('platform_dedup_ok'),
            cross.get('platform_corpus_used'))):
        raise ValueError('交接需要完整的查重回执，请用 update-many 刷新本批生成清单')
    generation_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    topic_sha = hashlib.sha256((parent / 'repo-topic-map.json').read_bytes()).hexdigest()
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
        plan_sequence = int(entry["sequence"])
        domain_contract = domain_fit.contract_for_sequence(topic_map, plan_sequence)
        review_contract_doc = data.get("review_contract")
        if not isinstance(review_contract_doc, dict):
            raise ValueError(f"{name} 缺少 review_contract")
        review_contract_hash = __import__("hashlib").sha256(
            json.dumps(review_contract_doc, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        record = {k: data[k] for k in (
            "repo_id", "domain", "subdirection", "language", "frameworks",
            "repro_level", "repro_evidence", "requirements",
        )}
        record.update(common_profile={"path":str(profile_path.relative_to(root)),"sha256":__import__("hashlib").sha256(profile_path.read_bytes()).hexdigest()}, task_id=name, batch=batch, task_type=entry["task_type"], prompt=prompt,
                      difficulty=data.get("difficulty", "困难"), initial_commit=initial, base_commit=initial,
                      task_repo=entry["repo"].split("/", 1)[1], task_repo_full=entry["repo"],
                      sequence=plan_sequence, contract_binding_version=1,
                      topic_map_sha256=topic_sha, generation_sha256=generation_sha,
                      handoff_version=2, producer="s11-gsb-prompt-create", execution_mode="single",
                      branch="main", origin=f"{name}/origin", workspace=f"{name}/workspace",
                      domain_contract=domain_contract,
                      domain_gate_version=domain_fit.DOMAIN_GATE_VERSION,
                      feedback_policy_version=3,
                      review_contract=review_contract_doc,
                      review_contract_sha256=review_contract_hash)
        for key in ("image", "image_id", "harness", "harness_version", "os"):
            record[key] = cfg[key]
        baseline = {k: record[k] for k in (
            "repo_id", "domain", "subdirection", "language", "frameworks",
            "repro_level", "repro_evidence", "image", "image_id", "harness",
            "harness_version", "os",
        )}
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
                       review_contract_file=f"{name}/review-contract.json",
                       producer=record["producer"], execution_mode="single", branch="main")
        tasks.append(row_out)
        baselines[record["baseline_id"]] = baseline
        staged.append((target, record, recording))
    generation_manifest_path = parent / workbook.MANIFEST_FILENAME
    batch_manifest = {"schema_version": 2, "contract_binding_version": 1, "batch_id": batch, "count": len(tasks), "tasks": tasks, "baselines": baselines,
                      "review_contract_required": True,
                      "producer": "s11-gsb-prompt-create", "execution_mode": "single", "branch": "main",
                      "generation_manifest": {
                          "path": str(generation_manifest_path.relative_to(parent)),
                          "sha256": hashlib.sha256(generation_manifest_path.read_bytes()).hexdigest(),
                      },
                      "prompt_opportunities": opportunity_binding}
    for target, record, recording in staged:
        save_json(target / "task.json", record)
        save_json(target / "recording-plan.json", recording)
        save_json(target / "review-contract.json", record["review_contract"])
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
