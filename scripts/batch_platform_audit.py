#!/usr/bin/env python3
"""Summarize SOLO2 QC failures into prompt, runtime, and Feedback owners."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


CHECK_POLICY: dict[str, dict[str, str]] = {
    "trace_complexity_floor": {
        "category": "prompt_difficulty",
        "owner": "s11-gsb-prompt-create",
        "action": "rebuild_h3_or_change_task",
    },
    "trace_tool_call_floor": {
        "category": "prompt_workload",
        "owner": "s11-gsb-prompt-create",
        "action": "rebuild_workload_and_block_low_call_runs",
    },
    "dedup": {
        "category": "prompt_dedup",
        "owner": "s11-gsb-prompt-create",
        "action": "change_story_shape_and_raw_a5_fragment",
    },
    "dedup_semantic": {
        "category": "prompt_dedup",
        "owner": "s11-gsb-prompt-create",
        "action": "change_story_shape_and_raw_a5_fragment",
    },
    "admin_semantic_duplicate": {
        "category": "prompt_dedup",
        "owner": "s11-gsb-prompt-create",
        "action": "change_semantic_identity_or_remove_second_topic_seat",
    },
    "trace_memory": {
        "category": "runtime_memory",
        "owner": "s11-feedback-run",
        "action": "rerun_without_cross_session_transcript_read",
    },
    "trace_skill": {
        "category": "runtime_policy",
        "owner": "s11-feedback-run",
        "action": "rerun_under_clean_harness_policy",
    },
    "trace_standard_leak": {
        "category": "runtime_policy",
        "owner": "s11-feedback-run",
        "action": "rerun_under_clean_harness_policy",
    },
    "trace_docker_call": {
        "category": "runtime_policy",
        "owner": "s11-feedback-run",
        "action": "rerun_under_clean_harness_policy",
    },
}

DOMAIN_PROBLEM_TO_POLICY = {
    "DOMAIN_MISMATCH": {
        "category": "prompt_domain",
        "owner": "s11-gsb-prompt-create",
        "action": "replace_main_task_with_a_data_engineering_result",
    }
}

DEFAULT_FEEDBACK_POLICY = {
    "category": "feedback_or_delivery",
    "owner": "s11-feedback-run",
    "action": "inspect_local_check_and_repair_feedback_if_evidence_changed",
}


def _load(path: Path, default: Any) -> Any:
    if not path.is_file() or path.is_symlink():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _policy(check: dict[str, Any]) -> dict[str, str] | None:
    if check.get("passed") is not False:
        return None
    evidence = check.get("evidence") if isinstance(check.get("evidence"), dict) else {}
    problem_type = str(evidence.get("problem_type") or "")
    if problem_type in DOMAIN_PROBLEM_TO_POLICY:
        return DOMAIN_PROBLEM_TO_POLICY[problem_type]
    return CHECK_POLICY.get(str(check.get("check_key") or ""), DEFAULT_FEEDBACK_POLICY)


def audit(batch_dir: Path) -> dict[str, Any]:
    batch_dir = Path(batch_dir).expanduser().resolve()
    if not batch_dir.is_dir():
        raise ValueError(f"批次目录不存在：{batch_dir}")
    tasks: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    owner_counts: Counter[str] = Counter()
    for task_dir in sorted(path for path in batch_dir.iterdir() if path.is_dir()):
        receipt_path = task_dir / "review/upload.json"
        receipt = _load(receipt_path, {})
        if not isinstance(receipt, dict) or not receipt:
            continue
        status = str(receipt.get("remote_status") or receipt.get("status") or "UNKNOWN")
        status_counts[status] += 1
        failures: list[dict[str, Any]] = []
        owners: set[str] = set()
        categories: set[str] = set()
        for check in receipt.get("qc_checks") or []:
            if not isinstance(check, dict):
                continue
            policy = _policy(check)
            if policy is None:
                continue
            owners.add(policy["owner"])
            categories.add(policy["category"])
            category_counts[policy["category"]] += 1
            owner_counts[policy["owner"]] += 1
            failures.append({
                "check_key": check.get("check_key"),
                "check_name": check.get("check_name"),
                "summary": check.get("summary"),
                **policy,
            })
        summary = str(receipt.get("qc_summary") or "")
        if (
            status == "DISCARDED"
            and "重复" in summary
            and "相似" in summary
        ):
            policy = CHECK_POLICY["admin_semantic_duplicate"]
            owners.add(policy["owner"])
            categories.add(policy["category"])
            category_counts[policy["category"]] += 1
            owner_counts[policy["owner"]] += 1
            failures.append({
                "check_key": "admin_semantic_duplicate",
                "check_name": "管理员判定语义重复",
                "summary": summary,
                **policy,
            })
        tasks.append({
            "task_id": task_dir.name,
            "submission_id": receipt.get("submission_id"),
            "status": status,
            "owners": sorted(owners),
            "categories": sorted(categories),
            "failures": failures,
        })
    return {
        "version": 1,
        "batch": batch_dir.name,
        "checked_at": __import__("datetime").datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "task_count": len(tasks),
        "status_counts": dict(sorted(status_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "owner_counts": dict(sorted(owner_counts.items())),
        "tasks": tasks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = audit(Path(args.batch_dir))
        text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            output = Path(args.output).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
        print(text, end="")
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
