#!/usr/bin/env python3
"""Read-only admission gate for S11 previews before showing them to the user."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import check_prompt_type_intent
import difficulty_contract
import domain_fit
import h3_selection
import check_prompt_naturalness
import prompt_style


def check(candidate: dict, repo: Path) -> dict:
    rows = candidate.get("records")
    topic_map = candidate.get("topic_map")
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        return {"ok": False, "errors": ["样稿 records 必须是非空对象数组"]}
    if not isinstance(topic_map, dict):
        return {"ok": False, "errors": ["样稿必须附带 topic_map，不能只提交题面文本"]}
    if any(not str(row.get("提示词") or "").strip() for row in rows):
        return {"ok": False, "errors": ["样稿不能包含空题面"]}
    stages = {
        "selection": h3_selection.selection_errors(topic_map, repo, required=True, new=True),
        "style": prompt_style.prompt_errors(rows),
        "naturalness": check_prompt_naturalness.check(rows)["errors"],
        "type_intent": check_prompt_type_intent.prompt_errors(rows, topic_map),
        "domain": domain_fit.map_errors(topic_map, repo) + domain_fit.prompt_errors(rows, topic_map),
        "difficulty": difficulty_contract.prompt_errors(rows, {**topic_map, "final_review_version": 2}, repo),
    }
    return {
        "ok": all(not errors for errors in stages.values()),
        "scope": "preview admission only; not full batch/handoff validation",
        "checked_prompts": len(rows),
        "stages": {name: {"ok": not errors, "errors": errors} for name, errors in stages.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, help="JSON containing records and topic_map for preview tasks only")
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()
    result = check(
        json.loads(Path(args.candidate).read_text(encoding="utf-8")),
        Path(args.repo).expanduser().resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
