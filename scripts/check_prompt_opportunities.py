#!/usr/bin/env python3
"""Read-only validation for an external prompt-opportunity map."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import check_repo_theme
import semantic_dedup

VERSION = 1
RESERVE_PER_MODE = 1
LEGACY_TARGET_COUNT = 36
LEGACY_SELECTION_POLICY = "12-modes-x3-plus-one-reserve-per-mode"
CAPACITY_SELECTION_POLICY = "capacity-balanced-up-to-three-per-mode"
FORBIDDEN_PROMPT_FIELDS = {"prompt", "final_prompt", "prompt_txt", "prompt_text"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_text(raw) for raw in value) if item]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_mode_counts(total: int, modes: list[str]) -> dict[str, int]:
    """Spread the available plan over usable modes, never above three per mode."""
    if total < 1:
        raise ValueError("正式机会数量必须大于 0")
    if total > len(modes) * 3:
        raise ValueError(f"正式机会数量 {total} 超过 {len(modes)} 个模式、每模式 3 条的上限")
    base, extra = divmod(total, len(modes))
    return {
        mode: base + (1 if index < extra else 0)
        for index, mode in enumerate(modes)
    }


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _anchor_errors(repo: Path, anchors: Any, label: str) -> list[str]:
    if not isinstance(anchors, list) or len(anchors) < 2:
        return [f"{label} 至少需要两条 source_anchors"]
    errors: list[str] = []
    paths: set[str] = set()
    for index, anchor in enumerate(anchors):
        where = f"{label}.source_anchors[{index}]"
        if not isinstance(anchor, dict):
            errors.append(f"{where} 必须是对象")
            continue
        raw_path = _text(anchor.get("path"))
        path = Path(raw_path)
        if not raw_path or path.is_absolute() or ".." in path.parts:
            errors.append(f"{where}.path 必须是仓库内相对路径")
            continue
        target = repo / path
        if not target.is_file():
            errors.append(f"{where}.path 不存在：{raw_path}")
            continue
        try:
            start = int(anchor.get("start_line"))
            end = int(anchor.get("end_line"))
        except (TypeError, ValueError):
            errors.append(f"{where} 行号必须是整数")
            continue
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        if start < 1 or end < start or end > len(lines):
            errors.append(f"{where} 行号范围无效")
            continue
        region = "\n".join(lines[start - 1:end])
        if _text(anchor.get("excerpt")) != region:
            errors.append(f"{where}.excerpt 必须逐字等于完整连续原文")
        paths.add(raw_path)
    if len(paths) < 2:
        errors.append(f"{label}.source_anchors 必须来自至少两个不同源码文件")
    return errors


def _brief_errors(repo: Path, brief: Any, label: str, *, selected: bool) -> list[str]:
    if not isinstance(brief, dict):
        return [f"{label} 必须是对象"]
    errors: list[str] = []
    forbidden = FORBIDDEN_PROMPT_FIELDS & set(brief)
    if forbidden:
        errors.append(f"{label} 不得保存最终题面字段：{'、'.join(sorted(forbidden))}")
    for key in (
        "opportunity_id",
        "task_type",
        "family",
        "story_summary",
        "current_behavior",
        "desired_change",
        "natural_prompt_angle",
    ):
        if not _text(brief.get(key)):
            errors.append(f"{label}.{key} 不能为空")
    chain = brief.get("data_chain")
    if not isinstance(chain, dict):
        errors.append(f"{label}.data_chain 缺失")
    else:
        for key in ("input", "operation", "output", "observable"):
            if not _text(chain.get(key)):
                errors.append(f"{label}.data_chain.{key} 不能为空")
    modules = brief.get("modules")
    if not isinstance(modules, dict):
        errors.append(f"{label}.modules 缺失")
    else:
        for key in ("primary", "secondary", "artifact", "consumer_behavior"):
            if not _text(modules.get(key)):
                errors.append(f"{label}.modules.{key} 不能为空")
    if len(_text_list(brief.get("constraints"))) < 2:
        errors.append(f"{label}.constraints 至少两条")
    tradeoff = brief.get("design_tradeoff")
    if not isinstance(tradeoff, dict):
        errors.append(f"{label}.design_tradeoff 缺失")
    else:
        if not _text(tradeoff.get("axis")):
            errors.append(f"{label}.design_tradeoff.axis 不能为空")
        if len(set(_text_list(tradeoff.get("options")))) < 2:
            errors.append(f"{label}.design_tradeoff.options 至少两个")
        if not _text(tradeoff.get("invariant")):
            errors.append(f"{label}.design_tradeoff.invariant 不能为空")
    if not _text_list(brief.get("acceptance_signals")):
        errors.append(f"{label}.acceptance_signals 不能为空")
    if not _text_list(brief.get("avoid_patterns")):
        errors.append(f"{label}.avoid_patterns 不能为空")
    errors.extend(_anchor_errors(repo, brief.get("source_anchors"), label))
    if selected:
        if brief.get("status") != "selected":
            errors.append(f"{label}.status 必须为 selected")
        try:
            int(brief.get("sequence"))
        except (TypeError, ValueError):
            errors.append(f"{label}.sequence 无效")
    else:
        if brief.get("status") != "reserve":
            errors.append(f"{label}.status 必须为 reserve")
        if not _text(brief.get("reserve_for_mode")):
            errors.append(f"{label}.reserve_for_mode 不能为空")
    return errors


def check(repo: str | Path, topic_map_path: str | Path, opportunity_path: str | Path) -> dict:
    repo = Path(repo).expanduser().resolve()
    map_path = Path(topic_map_path).expanduser().resolve()
    opportunity_path = Path(opportunity_path).expanduser().resolve()
    errors: list[str] = []
    if not repo.is_dir():
        errors.append(f"仓库不存在：{repo}")
    if opportunity_path.is_relative_to(repo):
        errors.append("机会地图不得位于生成仓库内部")
    if not map_path.is_file():
        errors.append(f"topic map 不存在：{map_path}")
    if not opportunity_path.is_file():
        errors.append(f"机会地图不存在：{opportunity_path}")
    if errors:
        return {"ok": False, "version": VERSION, "errors": errors}

    topic_map = json.loads(map_path.read_text(encoding="utf-8"))
    payload = json.loads(opportunity_path.read_text(encoding="utf-8"))
    if not isinstance(topic_map, dict) or not isinstance(payload, dict):
        return {"ok": False, "version": VERSION, "errors": ["topic map 和机会地图顶层必须是对象"]}

    head = _git(repo, "rev-parse", "HEAD")
    if head.returncode:
        return {"ok": False, "version": VERSION, "errors": [head.stderr.strip() or "无法读取仓库 HEAD"]}
    if _text(payload.get("source_commit")) != head.stdout.strip():
        errors.append("机会地图 source_commit 与当前仓库 HEAD 不一致")
    if _text(payload.get("source_base_commit")) != _text(topic_map.get("base_commit")):
        errors.append("机会地图 source_base_commit 与 topic map.base_commit 不一致")
    if _text(payload.get("repo_topic_map_sha256")) != _sha(map_path):
        errors.append("repo_topic_map_sha256 与当前 topic map 不一致")
    if payload.get("schema_version") != VERSION:
        errors.append(f"schema_version 必须为 {VERSION}")
    contract_version = topic_map.get("dedup_contract_version")
    semantic_contract = contract_version is not None
    if semantic_contract:
        errors.extend(semantic_dedup.map_errors(topic_map, require=True))
        try:
            payload_contract = int(payload.get("dedup_contract_version"))
        except (TypeError, ValueError):
            payload_contract = 0
        if payload_contract != int(contract_version):
            errors.append(
                "dedup_contract_version 必须与 repo-topic-map.json 一致"
            )

    raw_plans = [
        plan for plan in topic_map.get("task_plan", [])
        if isinstance(plan, dict)
    ]
    plans = {
        int(plan["sequence"]): plan
        for plan in raw_plans
        if str(plan.get("sequence")).isdigit()
    }
    if len(plans) != len(raw_plans):
        errors.append("task_plan 的 sequence 必须为唯一正整数")
    target_count = len(plans)
    if target_count < 1:
        errors.append("topic map 的 task_plan 不能为空")
    if int(payload.get("target_count") or 0) != target_count:
        errors.append(f"target_count 必须等于 task_plan 数量 {target_count}")
    if int(payload.get("selected_count") or 0) != target_count:
        errors.append(f"selected_count 必须等于 task_plan 数量 {target_count}")
    selection_policy = _text(payload.get("selection_policy"))
    if selection_policy == LEGACY_SELECTION_POLICY:
        if target_count != LEGACY_TARGET_COUNT:
            errors.append(
                f"旧 selection_policy 仅适用于 {LEGACY_TARGET_COUNT} 条题位；"
                f"当前 task_plan 为 {target_count} 条，请改用 {CAPACITY_SELECTION_POLICY}"
            )
    elif selection_policy != CAPACITY_SELECTION_POLICY:
        errors.append(
            f"selection_policy 必须为 {CAPACITY_SELECTION_POLICY}"
            f" 或兼容旧值 {LEGACY_SELECTION_POLICY}"
        )

    tracked = _git(repo, "ls-files", "--error-unmatch", str(opportunity_path.relative_to(repo))) if opportunity_path.is_relative_to(repo) else None
    if tracked and tracked.returncode == 0:
        errors.append("机会地图不得被生成仓库跟踪")

    opportunities = payload.get("opportunities")
    if not isinstance(opportunities, list):
        return {"ok": False, "version": VERSION, "errors": errors + ["opportunities 必须是数组"]}
    selected = [item for item in opportunities if isinstance(item, dict) and item.get("status") == "selected"]
    reserve = [item for item in opportunities if isinstance(item, dict) and item.get("status") == "reserve"]
    if len(selected) != target_count:
        errors.append(f"正式机会必须 {target_count} 条，实际 {len(selected)}")
    usable_modes = list(check_repo_theme.USABLE_MODES)
    try:
        expected_modes = expected_mode_counts(target_count, usable_modes)
    except ValueError as exc:
        errors.append(str(exc))
        expected_modes = {mode: 0 for mode in usable_modes}
    expected_reserve = sum(
        RESERVE_PER_MODE for count in expected_modes.values() if count > 0
    )
    if len(reserve) != expected_reserve:
        errors.append(f"备用机会必须 {expected_reserve} 条，实际 {len(reserve)}")
    selected_modes: Counter[str] = Counter()
    seen_sequences: set[int] = set()
    seen_opportunity_ids: set[str] = set()
    for index, item in enumerate(selected):
        errors.extend(_brief_errors(repo, item, f"selected[{index}]", selected=True))
        opportunity_id = _text(item.get("opportunity_id"))
        if opportunity_id and opportunity_id in seen_opportunity_ids:
            errors.append(f"selected opportunity_id 重复：{opportunity_id}")
        elif opportunity_id:
            seen_opportunity_ids.add(opportunity_id)
        try:
            sequence = int(item.get("sequence"))
        except (TypeError, ValueError):
            continue
        if sequence in seen_sequences:
            errors.append(f"selected sequence 重复：{sequence}")
        seen_sequences.add(sequence)
        plan = plans.get(sequence)
        if plan is None:
            errors.append(f"selected sequence={sequence} 不在 topic map")
            continue
        expected_topic = _text(plan.get("topic"))
        brief_topic = _text(item.get("topic_id") or item.get("topic"))
        if brief_topic != expected_topic:
            errors.append(f"selected sequence={sequence} topic 与 map 不一致")
        for key in ("task_type", "family"):
            if _text(item.get(key)) != _text(plan.get(key)):
                errors.append(f"selected sequence={sequence} {key} 与 map 不一致")
        if semantic_contract:
            if item.get("semantic_identity") != plan.get("semantic_identity"):
                errors.append(
                    f"selected sequence={sequence} semantic_identity 与 map 不一致"
                )
            if item.get("semantic_contrast", []) != plan.get("semantic_contrast", []):
                errors.append(
                    f"selected sequence={sequence} semantic_contrast 与 map 不一致"
                )
        mode = _text(item.get("demand_mode"))
        if mode not in check_repo_theme.DEMAND_MODE_LEXICON or mode == "代码理解":
            errors.append(f"selected sequence={sequence} demand_mode 不可用：{mode or '空'}")
        else:
            selected_modes[mode] += 1
    if seen_sequences != set(plans):
        errors.append(
            f"正式机会 sequence 必须与 topic map 的 {target_count} 条题位完全一致"
        )
    for mode in usable_modes:
        expected = expected_modes.get(mode, 0)
        if selected_modes.get(mode, 0) != expected:
            errors.append(
                f"模式 {mode!r} 必须 {expected} 条，"
                f"实际 {selected_modes.get(mode, 0)}"
            )

    reserve_modes: Counter[str] = Counter()
    for index, item in enumerate(reserve):
        errors.extend(_brief_errors(repo, item, f"reserve[{index}]", selected=False))
        opportunity_id = _text(item.get("opportunity_id"))
        if opportunity_id and opportunity_id in seen_opportunity_ids:
            errors.append(f"reserve opportunity_id 重复：{opportunity_id}")
        elif opportunity_id:
            seen_opportunity_ids.add(opportunity_id)
        mode = _text(item.get("reserve_for_mode"))
        if mode not in usable_modes:
            errors.append(f"reserve[{index}] reserve_for_mode 不可用：{mode or '空'}")
        else:
            reserve_modes[mode] += 1
    for mode in usable_modes:
        expected = (
            RESERVE_PER_MODE if expected_modes.get(mode, 0) > 0 else 0
        )
        if reserve_modes.get(mode, 0) != expected:
            errors.append(
                f"备用模式 {mode!r} 必须 {expected} 条，"
                f"实际 {reserve_modes.get(mode, 0)}"
            )
    if payload.get("selected_count") != len(selected):
        errors.append("selected_count 与 opportunities 中的正式卡数量不一致")
    if payload.get("reserve_count") != len(reserve):
        errors.append("reserve_count 与 opportunities 中的备用卡数量不一致")
    if payload.get("mode_counts") != dict(selected_modes):
        errors.append("mode_counts 与正式机会卡实际模式统计不一致")
    if payload.get("reserve_mode_counts") != dict(reserve_modes):
        errors.append("reserve_mode_counts 与备用机会卡实际模式统计不一致")

    return {
        "ok": not errors,
        "version": VERSION,
        "repo": str(repo),
        "topic_map": str(map_path),
        "opportunity_map": str(opportunity_path),
        "opportunity_sha256": _sha(opportunity_path),
        "repo_topic_map_sha256": _sha(map_path),
        "source_commit": head.stdout.strip(),
        "source_base_commit": topic_map.get("base_commit"),
        "dedup_contract_version": topic_map.get("dedup_contract_version"),
        "target_count": target_count,
        "selection_policy": selection_policy,
        "selected_count": len(selected),
        "reserve_count": len(reserve),
        "mode_counts": dict(selected_modes),
        "reserve_mode_counts": dict(reserve_modes),
        "error_count": len(errors),
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--topic-map", required=True)
    parser.add_argument("--opportunity-map", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = check(args.repo, args.topic_map, args.opportunity_map)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"ok": False, "version": VERSION, "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
