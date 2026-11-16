#!/usr/bin/env python3
"""Reject prompts whose opening intent contradicts the declared S11 task type."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


TYPE_GATE_VERSION = 1
TYPE_BY_SLUG = {
    "development": "功能开发",
    "bugfix": "Bugfix",
    "optimization": "功能优化",
    "refactor": "代码重构",
    "migration": "依赖升级与迁移",
}
SLUG_BY_TYPE = {value: key for key, value in TYPE_BY_SLUG.items()}
TYPE_ALIASES = {
    **{value: value for value in TYPE_BY_SLUG.values()},
    **{key: value for key, value in TYPE_BY_SLUG.items()},
    "Bug修复": "Bugfix",
    "bug修复": "Bugfix",
    "feature迭代": "功能优化",
    "依赖升级": "依赖升级与迁移",
    "迁移": "依赖升级与迁移",
}
FOLDER_RE = re.compile(
    r"-(0[1-9]|[1-9][0-9])-(development|bugfix|optimization|refactor|migration)$"
)

REQUEST_PREFIXES = {
    "功能开发": ("请新增", "请实现", "请构建", "请建立", "请开发", "请提供", "请设计"),
    "Bugfix": ("请修复", "请纠正", "请修正"),
    "功能优化": ("请优化", "请改善", "请提升", "请稳定", "请降低", "请精简"),
    "代码重构": ("请重构", "请整理", "请收拢", "请抽象"),
    "依赖升级与迁移": ("请迁移", "请升级", "请替换", "请切换"),
}
MAIN_TASK_PREFIXES = {
    "功能开发": ("新增", "实现", "构建", "建立", "开发", "提供", "设计"),
    "Bugfix": ("修复", "纠正", "修正"),
    "功能优化": ("优化", "改善", "提升", "稳定", "降低", "精简"),
    "代码重构": ("重构", "整理", "收拢", "抽象"),
    "依赖升级与迁移": ("迁移", "升级", "替换", "切换"),
}
DEFECT_TERMS = (
    "错误", "失效", "报错", "不生效", "不一致", "错位", "回退", "丢失", "重复",
    "冲突", "截获", "无法", "为空", "不更新", "不变化", "覆盖", "缺失", "异常",
    "返回空页", "对不上", "读取不到", "残留", "停留", "丢失",
)
EXISTING_TERMS = ("现有", "已有", "当前", "原本", "既有", "原有")
OPTIMIZATION_TARGET_TERMS = (
    "性能", "稳定", "吞吐", "耗时", "重复", "失败", "续做", "去重", "一致",
    "一致性", "分页", "整批", "扫描", "覆盖", "冲突", "水位", "效率", "可靠",
)
REFACTOR_INVARIANT_TERMS = (
    "行为不变", "既有行为", "原有行为", "输入输出不变", "口径不变", "历史兼容",
)
MIGRATION_TERMS = (
    "版本", "依赖", "兼容", "迁移", "升级", "替换", "切换", "存量", "回滚",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def canonical_type(raw: Any) -> str | None:
    text = _text(raw)
    return TYPE_ALIASES.get(text) or TYPE_ALIASES.get(text.lower())


def sequence_for_folder(folder: str) -> tuple[int, str] | None:
    match = FOLDER_RE.search(folder or "")
    return (int(match.group(1)), match.group(2)) if match else None


def _intent_clause(main_request: str) -> str:
    return re.split(r"[；。！？\n]", main_request, maxsplit=1)[0].strip()


def _starts_with(text: str, prefixes: tuple[str, ...]) -> bool:
    return any(text.startswith(prefix) for prefix in prefixes)


def _intent_errors(task_type: str, main_request: str, *, label: str) -> list[str]:
    errors: list[str] = []
    if not main_request:
        return [f"{label} 缺少 prompt_main_request"]
    if not _starts_with(main_request, REQUEST_PREFIXES[task_type]):
        expected = "、".join(REQUEST_PREFIXES[task_type])
        errors.append(
            f"{label} 的 prompt_main_request 必须以任务类型主动作开头（{expected}），"
            f"当前为：{main_request[:32]}"
        )
    clause = _intent_clause(main_request)
    if task_type == "Bugfix":
        if not any(term in clause for term in DEFECT_TERMS):
            errors.append(
                f"{label} 的 Bugfix 主请求开头句没有写清真实缺陷、错误现象或失败后果"
            )
        if any(term in clause for term in ("新增", "首次实现", "从零构建")):
            errors.append(f"{label} 的 Bugfix 主请求开头句被写成了新增功能")
    elif task_type == "功能优化":
        if not any(term in clause for term in EXISTING_TERMS):
            errors.append(f"{label} 的功能优化主请求开头句没有明确针对现有或已有能力")
        if not any(term in main_request for term in OPTIMIZATION_TARGET_TERMS):
            errors.append(f"{label} 的功能优化主请求没有写清性能、稳定性、去重、失败恢复等优化目标")
        if any(term in clause for term in ("新增", "首次实现", "从零构建")):
            errors.append(f"{label} 的功能优化主请求开头句被写成了首次新增")
    elif task_type == "代码重构":
        if not any(term in main_request for term in REFACTOR_INVARIANT_TERMS):
            errors.append(f"{label} 的代码重构主请求没有写明既有行为或输入输出保持不变")
    elif task_type == "依赖升级与迁移":
        if not any(term in main_request for term in MIGRATION_TERMS):
            errors.append(f"{label} 的依赖升级与迁移主请求没有真实版本、兼容、存量或迁移目标")
    return errors


def hint_errors(topic_map: dict[str, Any]) -> list[str]:
    """Validate type intent at topic-plan time, before repositories or prompts exist."""
    errors: list[str] = []
    for plan in topic_map.get("task_plan", []) or []:
        if not isinstance(plan, dict):
            continue
        sequence = int(plan.get("sequence") or 0)
        task_type = canonical_type(plan.get("task_type"))
        expected_type = TYPE_BY_SLUG.get(_text(plan.get("slug")))
        if task_type is None or expected_type is None:
            errors.append(f"task_plan sequence {sequence} 的任务类型或 slug 不受支持")
            continue
        if task_type != expected_type:
            errors.append(
                f"task_plan sequence {sequence} 的 task_type={task_type} 与 slug={plan.get('slug')} 不一致"
            )
            continue
        fit = plan.get("domain_fit")
        if not isinstance(fit, dict):
            errors.append(f"task_plan sequence {sequence} 缺少 domain_fit，无法检查类型语义")
            continue
        where = f"task_plan sequence {sequence}"
        main_task = _text(fit.get("main_task"))
        if main_task and not _starts_with(main_task, MAIN_TASK_PREFIXES[task_type]):
            errors.append(
                f"{where} 的 domain_fit.main_task 必须以{task_type}动作开头，"
                f"当前为：{main_task[:32]}"
            )
        if task_type == "Bugfix" and main_task and not any(term in main_task for term in DEFECT_TERMS):
            errors.append(f"{where} 的 Bugfix main_task 没有写清真实缺陷或错误后果")
        errors.extend(
            _intent_errors(
                task_type,
                _text(fit.get("prompt_main_request")),
                label=where,
            )
        )
    return errors


def prompt_errors(records: list[dict[str, Any]], topic_map: dict[str, Any]) -> list[str]:
    """Check that the final prompt starts with the correctly typed main request."""
    errors = hint_errors(topic_map)
    plans = {
        int(plan.get("sequence")): plan
        for plan in topic_map.get("task_plan", []) or []
        if isinstance(plan, dict) and plan.get("sequence") is not None
    }
    for row in records:
        prompt = _text(row.get("提示词"))
        if not prompt:
            continue
        folder = _text(row.get("子文件夹名称"))
        parsed = sequence_for_folder(folder)
        if parsed is None:
            continue
        sequence, slug = parsed
        plan = plans.get(sequence)
        if plan is None:
            errors.append(f"{folder} 找不到对应的 task_plan 题位")
            continue
        if _text(plan.get("slug")) != slug:
            errors.append(f"{folder} 的目录 slug 与 task_plan 不一致")
            continue
        record_type = canonical_type(row.get("任务类型"))
        plan_type = canonical_type(plan.get("task_type"))
        if record_type is None or plan_type is None:
            errors.append(f"{folder} 的任务类型不受支持")
            continue
        if record_type != plan_type:
            errors.append(f"{folder} 的工作簿任务类型 {record_type} 与题位 {plan_type} 不一致")
            continue
        fit = plan.get("domain_fit")
        if not isinstance(fit, dict):
            errors.append(f"{folder} 缺少 domain_fit，无法检查类型语义")
            continue
        main_request = _text(fit.get("prompt_main_request"))
        if not main_request:
            errors.append(f"{folder} 缺少 prompt_main_request")
            continue
        if not prompt.startswith(main_request):
            errors.append(
                f"{folder} 的最终题面没有以 typed prompt_main_request 原样开头；"
                "任务类型不能藏在后句"
            )
        errors.extend(
            _intent_errors(
                record_type,
                main_request,
                label=f"{folder} {record_type}",
            )
        )
    return errors


def check_batch(parent: Path, *, topic_map_path: str | Path | None = None) -> dict[str, Any]:
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    import batch_prompt_workbook
    from repo_topic_policy import load_topic_map

    topic_map = load_topic_map(
        parent=parent,
        explicit=topic_map_path,
        required=True,
    )
    rows = batch_prompt_workbook.read_workbook(parent / "tasks.xlsx")
    errors = prompt_errors(rows, topic_map)
    return {
        "ok": not errors,
        "type_gate_version": TYPE_GATE_VERSION,
        "checked_prompts": sum(bool(_text(row.get("提示词"))) for row in rows),
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="检查 S11 提示词任务类型与主请求语义")
    parser.add_argument("--parent", required=True, help="批次目录")
    parser.add_argument("--map", help="repo-topic-map.json；默认从 parent 查找")
    args = parser.parse_args()
    result = check_batch(
        Path(args.parent).expanduser().resolve(),
        topic_map_path=args.map,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
