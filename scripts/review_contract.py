#!/usr/bin/env python3
"""Validate handoff metadata that downstream acceptance uses to write Feedback.

The 2026-11-15 batch showed six avoidable PENDING_FIX outcomes:
- environment/tool failures were used as model-quality deductions;
- compressed "telegraph" prose obscured the business behavior;
- "15 tests passed" was reported without saying what behavior was checked;
- two traces read cross-session answer notes.

This contract makes the expected business behavior, test meaning, excluded
environment noise, language focus, and workspace isolation explicit before run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

VERSION = 1
EVIDENCE_KINDS = {"http", "test", "ui", "artifact", "probe", "log"}
REQUIRED_ENVIRONMENT_EXCLUSIONS = {
    "dependency_install",
    "gateway_network",
    "port_conflict",
    "process_cleanup",
    "tool_availability",
}
CONTEXT_STATEMENT = (
    "只依据当前仓库、本轮题面和实际运行结果完成任务，"
    "不读取其他会话、答案笔记或历史对话缓存。"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _contract_errors(record: dict[str, Any], prompt: str) -> list[str]:
    folder = _text(record.get("子文件夹名称")) or "未命名"
    contract = record.get("review_contract")
    if not isinstance(contract, dict):
        return [f"{folder} 缺少 review_contract"]
    errors: list[str] = []
    try:
        version = int(contract.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version != VERSION:
        errors.append(f"{folder} 的 review_contract.version 必须是 {VERSION}")

    requirements = _list(record.get("requirements"))
    requirement_ids = {
        _text(item.get("id")) for item in requirements if isinstance(item, dict) and item.get("id")
    }
    checks = _list(contract.get("business_checks"))
    seen_ids: set[str] = set()
    for index, item in enumerate(checks):
        label = f"{folder} review_contract.business_checks[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} 必须是对象")
            continue
        requirement_id = _text(item.get("requirement_id"))
        if requirement_id not in requirement_ids:
            errors.append(f"{label}.requirement_id 不在 requirements 中：{requirement_id or '空'}")
        else:
            seen_ids.add(requirement_id)
        if len(_text(item.get("behavior"))) < 12:
            errors.append(f"{label}.behavior 必须写清用户可观察的业务行为")
        evidence_kind = _text(item.get("evidence_kind"))
        if evidence_kind not in EVIDENCE_KINDS:
            errors.append(f"{label}.evidence_kind 必须是 {'/'.join(sorted(EVIDENCE_KINDS))}")
        if len(_text(item.get("success_signal"))) < 8:
            errors.append(f"{label}.success_signal 缺失")
    missing = requirement_ids - seen_ids
    if missing:
        errors.append(f"{folder} 的 business_checks 未覆盖需求：{'、'.join(sorted(missing))}")

    tests = _list(contract.get("test_behaviors"))
    for index, item in enumerate(tests):
        label = f"{folder} review_contract.test_behaviors[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} 必须是对象")
            continue
        ids = [_text(raw) for raw in _list(item.get("requirement_ids")) if _text(raw)]
        if not ids or any(req not in requirement_ids for req in ids):
            errors.append(f"{label}.requirement_ids 必须引用真实需求")
        for key in ("selector", "behavior"):
            if len(_text(item.get(key))) < 6:
                errors.append(f"{label}.{key} 至少要有 6 个字符")

    criterion = _text(contract.get("key_business_constraint"))
    if len(criterion) < 12:
        errors.append(f"{folder} 的 key_business_constraint 至少要有 12 个字符")
    elif criterion not in prompt:
        errors.append(f"{folder} 的 key_business_constraint 不在最终题面中")

    excluded = set(_text(item) for item in _list(contract.get("environment_exclusions")))
    missing_exclusions = REQUIRED_ENVIRONMENT_EXCLUSIONS - excluded
    if missing_exclusions:
        errors.append(
            f"{folder} 的 environment_exclusions 缺少：{'、'.join(sorted(missing_exclusions))}"
        )

    focus = contract.get("model_process_focus")
    if not isinstance(focus, list) or any(len(_text(item)) < 10 for item in focus):
        errors.append(f"{folder} 的 model_process_focus 必须是具体模型行为列表")
    for item in focus or []:
        if any(term in _text(item).lower() for term in ("gateway", "dependency", "pkill", "exit code")):
            errors.append(f"{folder} 的 model_process_focus 不能放环境或工具噪声：{item}")

    isolation = contract.get("context_isolation")
    if not isinstance(isolation, dict):
        errors.append(f"{folder} 缺少 context_isolation")
    else:
        if isolation.get("workspace_only") is not True:
            errors.append(f"{folder} 的 context_isolation.workspace_only 必须为 true")
        if isolation.get("cross_session_memory") is not False:
            errors.append(f"{folder} 的 context_isolation.cross_session_memory 必须为 false")
        if isolation.get("answer_notes") is not False:
            errors.append(f"{folder} 的 context_isolation.answer_notes 必须为 false")
        statement = _text(isolation.get("prompt_statement"))
        if statement != CONTEXT_STATEMENT:
            errors.append(f"{folder} 的 context_isolation.prompt_statement 必须使用统一隔离声明")
        elif statement not in prompt:
            errors.append(f"{folder} 的上下文隔离声明不在最终题面中")

    style_focus = _text(contract.get("style_focus"))
    if len(style_focus) < 12:
        errors.append(f"{folder} 的 style_focus 必须说明 Feedback 应保留的业务行为和语言口径")
    language_terms = _list(contract.get("language_terms"))
    for index, item in enumerate(language_terms):
        label = f"{folder} review_contract.language_terms[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} 必须是对象")
            continue
        for key in ("source", "public"):
            if len(_text(item.get(key))) < 2:
                errors.append(f"{label}.{key} 缺失")
    return errors


def errors(
    records: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> list[str]:
    task_meta = metadata.get("tasks") if isinstance(metadata, dict) else {}
    task_meta = task_meta if isinstance(task_meta, dict) else {}
    result: list[str] = []
    for row in records:
        name = _text(row.get("子文件夹名称"))
        prompt = _text(row.get("提示词"))
        if not name or not prompt:
            continue
        item = task_meta.get(name)
        if not isinstance(item, dict):
            result.append(f"{name} 在 handoff-metadata.tasks 中缺少任务元数据")
            continue
        merged = {**item, "子文件夹名称": name, "requirements": item.get("requirements") or []}
        result.extend(_contract_errors(merged, prompt))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", required=True, help="批次目录")
    parser.add_argument("--metadata", required=True, help="handoff-metadata.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    from batch_prompt_workbook import read_workbook

    parent = Path(args.parent).expanduser().resolve()
    metadata = json.loads(Path(args.metadata).expanduser().read_text(encoding="utf-8"))
    rows = read_workbook(parent / "tasks.xlsx")
    problems = errors(rows, metadata)
    result = {
        "ok": not problems,
        "version": VERSION,
        "checked_count": sum(bool(_text(row.get("提示词"))) for row in rows),
        "error_count": len(problems),
        "errors": problems,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
