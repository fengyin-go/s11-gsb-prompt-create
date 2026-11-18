#!/usr/bin/env python3
"""Validate and summarize a GSB semantic-v2 repo-topic-map.json."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from repo_topic_policy import TopicMapError, load_topic_map, topic_slots
from s11_policy import validate_plan
import domain_fit
import difficulty_contract
import check_prompt_type_intent
import semantic_dedup


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map", help="repo-topic-map.json；不传时按 parent/repo 自动查找")
    parser.add_argument("--parent", help="项目父目录")
    parser.add_argument("--repo", help="原始仓库目录")
    parser.add_argument("--expected-base-commit", default="", help="要求 topic map 匹配的基准提交")
    parser.add_argument("--require-domain-fit", action="store_true", help="核对领域题位、源码依据和领域结果")
    parser.add_argument(
        "--require-difficulty-design",
        action="store_true",
        help="核对每题 H1/H2/H3 困难设计与跨模块源码依据",
    )
    parser.add_argument(
        "--require-semantic-dedup",
        action="store_true",
        help="要求新批次提供 semantic_identity 和同 topic 对比契约",
    )
    args = parser.parse_args()

    try:
        topic_map = load_topic_map(
            repo=Path(args.repo).expanduser().resolve() if args.repo else None,
            parent=Path(args.parent).expanduser().resolve() if args.parent else None,
            explicit=args.map,
            required=True,
            expected_base_commit=args.expected_base_commit.strip(),
        )
        validate_plan(topic_map)
        type_intent_errors = check_prompt_type_intent.hint_errors(topic_map)
        if type_intent_errors:
            raise TopicMapError("；".join(type_intent_errors))
        if args.require_domain_fit:
            errors = domain_fit.map_errors(
                topic_map,
                Path(args.repo).expanduser().resolve() if args.repo else None,
            )
            if errors:
                raise TopicMapError("；".join(errors))
        if args.require_difficulty_design:
            errors = difficulty_contract.map_errors(
                topic_map,
                Path(args.repo).expanduser().resolve() if args.repo else None,
            )
            if errors:
                raise TopicMapError("；".join(errors))
        semantic_errors = semantic_dedup.map_errors(
            topic_map,
            require=args.require_semantic_dedup,
        )
        if semantic_errors:
            raise TopicMapError("；".join(semantic_errors))
    except (TopicMapError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1) from None

    task_types = Counter(str(item.get("task_type") or "") for item in topic_map.get("task_plan", []))
    result = {
        "ok": True,
        "source": topic_map.get("source"),
        "profile": topic_map.get("profile"),
        "domain": topic_map.get("domain"),
        "subdirection": topic_map.get("subdirection"),
        "version": topic_map.get("version"),
        "base_commit": topic_map.get("base_commit"),
        "max_capacity": topic_map.get("max_capacity"),
        "dedup_contract_version": topic_map.get("dedup_contract_version"),
        "topic_count": len(topic_map.get("topics", [])),
        "topic_slots": topic_slots(topic_map),
        "task_plan_count": len(topic_map.get("task_plan", [])),
        "task_type_counts": dict(task_types),
        "topics": [
            {
                "id": topic.get("id"),
                "name": topic.get("name"),
                "module": topic.get("module"),
                "seat_capacity": topic.get("seat_capacity"),
            }
            for topic in topic_map.get("topics", [])
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
