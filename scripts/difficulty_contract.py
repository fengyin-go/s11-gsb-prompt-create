#!/usr/bin/env python3
"""Validate the structured H1/H2/H3 contract for a prompt plan.

The canonical G16/G19 judge remains the source of truth for difficulty labels.
This module closes the process gap exposed by the 2026-11-15 batch: 13 prompts
were admitted through one generic "难度复核" note and later discarded by the
platform because the prompt itself only described medium-sized work.

The contract does not replace semantic review. It makes the review auditable by
requiring a source-anchored reasoning challenge, a real data-flow module
integration, an open design tradeoff, and prompt excerpts that prove those
decisions reached the final prompt.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


CONTRACT_VERSION = 1
FOLDER_PLAN_RE = re.compile(
    r"-(0[1-9]|[1-9][0-9])-(development|bugfix|optimization|refactor|migration)$"
)
CONSTRAINT_KINDS = {
    "concurrency",
    "idempotency",
    "state_transition",
    "transaction_consistency",
    "failure_recovery",
    "versioning",
    "migration",
    "permission_ownership",
    "dependency_order",
    "conflict_resolution",
    "historical_compatibility",
}
SECONDARY_ROLES = {
    "consumer",
    "writer",
    "downstream",
    "audit",
    "persistence",
    "orchestrator",
    "notification",
    "authorization",
}
ANTI_PATTERNS = (
    "single_surface_only",
    "secondary_observation_only",
    "solution_prescribed",
    "single_constraint",
    "hard_evidence_tail_only",
    "example_data_only",
    "cross_call_merge_only",
)
OPEN_DECISION_RE = re.compile(
    r"(自行|自定|自拟|权衡|取舍|选择|决定|以谁为准|谁优先|优先级|冲突时|"
    r"兼容|回退|不变量|必须保证|不得|不能)"
)
PRESCRIPTION_MARKERS = (
    "第一步",
    "第二步",
    "第三步",
    "先",
    "再",
    "然后",
    "接着",
    "逐项",
    "依次",
    "按顺序",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_text(raw) for raw in value) if item]


def _sequence_for_folder(folder: str) -> int | None:
    match = FOLDER_PLAN_RE.search(folder or "")
    return int(match.group(1)) if match else None


def _source_region(repo: Path | None, anchor: dict[str, Any], label: str) -> tuple[list[str], str]:
    errors: list[str] = []
    if repo is None:
        return errors, ""
    raw_path = _text(anchor.get("path"))
    path = Path(raw_path)
    if not raw_path or path.is_absolute() or ".." in path.parts:
        return [f"{label} 的 path 不安全"], ""
    target = Path(repo) / path
    if not target.is_file() or target.is_symlink():
        return [f"{label} 的源码文件不存在或为符号链接：{raw_path}"], ""
    try:
        start = int(anchor.get("start_line"))
        end = int(anchor.get("end_line"))
    except (TypeError, ValueError):
        return [f"{label} 的 start_line/end_line 必须是整数"], ""
    if start < 1 or end < start:
        return [f"{label} 的行号范围无效"], ""
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    if end > len(lines):
        return [f"{label} 的结束行超出文件长度"], ""
    excerpt = _text(anchor.get("excerpt"))
    if len(excerpt) < 8:
        errors.append(f"{label} 的 excerpt 至少要有 8 个字符")
    elif excerpt not in "\n".join(lines[start - 1:end]):
        errors.append(f"{label} 的 excerpt 不在声明源码行内")
    return errors, "\n".join(lines[start - 1:end])


def _hard_evidence_keys() -> set[str]:
    from check_prompt_g16 import HARD_EVIDENCE_KEYS

    return set(HARD_EVIDENCE_KEYS)


def _design_errors(
    design: Any,
    *,
    where: str,
    repo: Path | None,
    prompt: str = "",
) -> list[str]:
    errors: list[str] = []
    if not isinstance(design, dict):
        return [f"{where} 缺少 difficulty_design"]
    try:
        version = int(design.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version != CONTRACT_VERSION:
        errors.append(f"{where} 的 difficulty_design.version 必须是 {CONTRACT_VERSION}")

    reasoning = design.get("reasoning_challenge")
    if not isinstance(reasoning, dict):
        errors.append(f"{where} 缺少 reasoning_challenge")
        reasoning = {}
    constraints = reasoning.get("constraints")
    if not isinstance(constraints, list) or len(constraints) < 2:
        errors.append(f"{where} 的 reasoning_challenge.constraints 至少要有两条硬约束")
        constraints = []
    kinds: list[str] = []
    for index, item in enumerate(constraints):
        label = f"{where} reasoning_challenge.constraints[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} 必须是对象")
            continue
        kind = _text(item.get("kind"))
        if kind not in CONSTRAINT_KINDS:
            errors.append(f"{label}.kind 不合法：{kind or '空'}")
        else:
            kinds.append(kind)
        for key in ("statement", "failure_if_omitted"):
            if len(_text(item.get(key))) < 8:
                errors.append(f"{label}.{key} 至少要有 8 个字符")
    if len(set(kinds)) < 2:
        errors.append(f"{where} 的 reasoning_challenge 至少要有两种不同约束类型")
    if len(_text(reasoning.get("why_interact"))) < 12:
        errors.append(f"{where} 的 reasoning_challenge.why_interact 必须说明约束如何相互作用")
    if len(_text(reasoning.get("prompt_excerpt"))) < 8:
        errors.append(f"{where} 的 reasoning_challenge.prompt_excerpt 缺失")

    integration = design.get("module_integration")
    if not isinstance(integration, dict):
        errors.append(f"{where} 缺少 module_integration")
        integration = {}
    primary = _text(integration.get("primary_module"))
    if len(primary) < 2:
        errors.append(f"{where} 的 module_integration.primary_module 缺失")
    secondary = integration.get("secondary_modules")
    if not isinstance(secondary, list) or not secondary:
        errors.append(f"{where} 的 module_integration.secondary_modules 至少要有 1 个")
        secondary = []
    secondary_names: list[str] = []
    for index, item in enumerate(secondary):
        label = f"{where} module_integration.secondary_modules[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} 必须是对象")
            continue
        name = _text(item.get("name"))
        role = _text(item.get("role"))
        if len(name) < 2:
            errors.append(f"{label}.name 缺失")
        else:
            secondary_names.append(name)
        if role not in SECONDARY_ROLES:
            errors.append(
                f"{label}.role 必须是真实数据流角色：{'/'.join(sorted(SECONDARY_ROLES))}"
            )
        if len(_text(item.get("artifact"))) < 4:
            errors.append(f"{label}.artifact 至少要有 4 个字符")
        if len(_text(item.get("relationship"))) < 8:
            errors.append(f"{label}.relationship 至少要有 8 个字符")
    if len(_text(integration.get("data_artifact"))) < 4:
        errors.append(f"{where} 的 module_integration.data_artifact 缺失")
    if len(_text(integration.get("prompt_excerpt"))) < 8:
        errors.append(f"{where} 的 module_integration.prompt_excerpt 缺失")

    tradeoff = design.get("design_tradeoff")
    if not isinstance(tradeoff, dict):
        errors.append(f"{where} 缺少 design_tradeoff")
        tradeoff = {}
    if len(_text(tradeoff.get("decision"))) < 8:
        errors.append(f"{where} 的 design_tradeoff.decision 至少要有 8 个字符")
    options = _text_list(tradeoff.get("options"))
    if len(set(options)) < 2:
        errors.append(f"{where} 的 design_tradeoff.options 至少要有两个不同方案")
    if len(_text(tradeoff.get("outcome_invariant"))) < 8:
        errors.append(f"{where} 的 design_tradeoff.outcome_invariant 缺失")
    if len(_text(tradeoff.get("why_open"))) < 12:
        errors.append(f"{where} 的 design_tradeoff.why_open 必须说明为何没有写死实现")
    if tradeoff.get("implementation_not_prescribed") is not True:
        errors.append(f"{where} 的 design_tradeoff.implementation_not_prescribed 必须为 true")
    if len(_text(tradeoff.get("prompt_excerpt"))) < 8:
        errors.append(f"{where} 的 design_tradeoff.prompt_excerpt 缺失")

    counterexample = design.get("medium_counterexample")
    if not isinstance(counterexample, dict):
        errors.append(f"{where} 缺少 medium_counterexample")
    else:
        for key in ("generic_task", "why_medium"):
            if len(_text(counterexample.get(key))) < 12:
                errors.append(f"{where} medium_counterexample.{key} 至少要有 12 个字符")

    evidence = _text_list(design.get("hard_evidence"))
    allowed_evidence = _hard_evidence_keys()
    if not evidence:
        errors.append(f"{where} 的 hard_evidence 至少命中一项")
    for item in evidence:
        if item not in allowed_evidence:
            errors.append(f"{where} 的 hard_evidence 不在统一判据内：{item}")

    anti = design.get("anti_patterns")
    if not isinstance(anti, dict):
        errors.append(f"{where} 缺少 anti_patterns")
        anti = {}
    for name in ANTI_PATTERNS:
        if anti.get(name) is not False:
            errors.append(f"{where} 的 anti_patterns.{name} 必须为 false")

    anchors = design.get("module_anchors")
    if not isinstance(anchors, list) or len(anchors) < 2:
        errors.append(f"{where} 的 module_anchors 至少要有两条跨模块源码锚点")
        anchors = []
    anchor_modules: list[str] = []
    anchor_paths: list[str] = []
    for index, anchor in enumerate(anchors):
        label = f"{where} module_anchors[{index}]"
        if not isinstance(anchor, dict):
            errors.append(f"{label} 必须是对象")
            continue
        module = _text(anchor.get("module"))
        supports = _text(anchor.get("supports"))
        if len(module) < 2:
            errors.append(f"{label}.module 缺失")
        else:
            anchor_modules.append(module)
        if supports not in {"primary", "secondary", "data_flow"}:
            errors.append(f"{label}.supports 必须是 primary/secondary/data_flow")
        raw_path = _text(anchor.get("path"))
        if raw_path:
            anchor_paths.append(raw_path)
        errors.extend(_source_region(repo, anchor, label)[0])
    if len(set(anchor_modules)) < 2:
        errors.append(f"{where} 的 module_anchors 必须覆盖至少两个不同模块")
    if len(set(anchor_paths)) < 2:
        errors.append(f"{where} 的 module_anchors 必须引用至少两个不同源码文件")
    if primary and anchor_modules and primary not in anchor_modules:
        errors.append(f"{where} 的 module_anchors 没有覆盖 primary_module")
    if secondary_names and not (set(secondary_names) & set(anchor_modules)):
        errors.append(f"{where} 的 module_anchors 没有覆盖任何 secondary_module")

    if prompt:
        excerpts = {
            "reasoning_challenge": _text(reasoning.get("prompt_excerpt")),
            "module_integration": _text(integration.get("prompt_excerpt")),
            "design_tradeoff": _text(tradeoff.get("prompt_excerpt")),
        }
        for key, excerpt in excerpts.items():
            if excerpt and excerpt not in prompt:
                errors.append(f"{where}.{key}.prompt_excerpt 不在最终题面中")
        artifact = _text(integration.get("data_artifact"))
        module_excerpt = excerpts["module_integration"]
        if artifact and module_excerpt and artifact not in module_excerpt:
            errors.append(f"{where} 的第二模块题面摘录没有包含数据产物：{artifact}")
        decision_excerpt = excerpts["design_tradeoff"]
        if decision_excerpt and not OPEN_DECISION_RE.search(decision_excerpt):
            errors.append(f"{where} 的 design_tradeoff 题面摘录必须写出需要判断的取舍或结果不变量")
        prescription_count = sum(prompt.count(marker) for marker in PRESCRIPTION_MARKERS)
        if prescription_count >= 4 and not OPEN_DECISION_RE.search(decision_excerpt):
            errors.append(
                f"{where} 的题面步骤写得太死且没有留下实现取舍；"
                "减少先/再/逐项等过程指令，改为写结果不变量和可选裁决轴"
            )
    return errors


def map_errors(topic_map: dict[str, Any], repo: Path | None) -> list[str]:
    errors: list[str] = []
    try:
        version = int(topic_map.get("difficulty_contract_version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version != CONTRACT_VERSION:
        errors.append(f"topic map.difficulty_contract_version 必须是 {CONTRACT_VERSION}")
    plans = topic_map.get("task_plan") or []
    if not isinstance(plans, list):
        return errors + ["topic map.task_plan 必须是数组"]
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        sequence = plan.get("sequence")
        errors.extend(
            _design_errors(
                plan.get("difficulty_design"),
                where=f"task_plan sequence {sequence}",
                repo=repo,
            )
        )
    return errors


def prompt_errors(
    records: list[dict[str, Any]],
    topic_map: dict[str, Any],
    repo: Path | None,
) -> list[str]:
    errors: list[str] = []
    plans = {
        int(plan.get("sequence")): plan
        for plan in topic_map.get("task_plan") or []
        if isinstance(plan, dict) and str(plan.get("sequence") or "").isdigit()
    }
    seen: set[int] = set()
    for record in records:
        prompt = _text(record.get("提示词"))
        if not prompt:
            continue
        folder = _text(record.get("子文件夹名称"))
        sequence = _sequence_for_folder(folder)
        if sequence is None or sequence not in plans:
            errors.append(f"{folder or '未命名'} 找不到对应的 task_plan sequence")
            continue
        seen.add(sequence)
        plan = plans[sequence]
        if _text(record.get("任务类型")) != _text(plan.get("task_type")):
            errors.append(f"{folder} 的任务类型与 task_plan sequence {sequence} 不一致")
        errors.extend(
            _design_errors(
                plan.get("difficulty_design"),
                where=f"{folder} (sequence {sequence})",
                repo=repo,
                prompt=prompt,
            )
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", help="批次目录，读取 repo-topic-map.json 和 tasks.xlsx")
    parser.add_argument("--map", help="显式 repo-topic-map.json")
    parser.add_argument("--repo", help="源码仓库")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    from repo_topic_policy import load_topic_map
    from batch_prompt_workbook import read_workbook

    parent = Path(args.parent).expanduser().resolve() if args.parent else None
    repo = Path(args.repo).expanduser().resolve() if args.repo else None
    if parent and repo is None:
        candidates = sorted(path for path in parent.iterdir() if (path / "origin").is_dir())
        repo = candidates[0] / "origin" if candidates else None
    topic_map = load_topic_map(
        parent=parent,
        repo=repo,
        explicit=args.map,
        required=True,
    )
    errors = map_errors(topic_map or {}, repo)
    if parent and (parent / "tasks.xlsx").is_file():
        errors += prompt_errors(read_workbook(parent / "tasks.xlsx"), topic_map or {}, repo)
    result = {
        "ok": not errors,
        "version": CONTRACT_VERSION,
        "task_count": len((topic_map or {}).get("task_plan") or []),
        "error_count": len(errors),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
