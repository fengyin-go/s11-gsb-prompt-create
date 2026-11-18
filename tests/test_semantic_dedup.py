from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_prompt_dedup  # noqa: E402
import semantic_dedup  # noqa: E402


def identity(
    topic: str,
    operation: str,
    outcomes: list[str],
    artifacts: list[str],
    acceptance: list[str],
) -> dict:
    return {
        "version": 1,
        "object_key": topic,
        "operation_key": operation,
        "outcome_keys": outcomes,
        "artifact_keys": artifacts,
        "acceptance_entities": acceptance,
    }


def topic_map(*plans: dict, version: int | None = 1) -> dict:
    payload = {
        "version": 1,
        "profile": "gsb-semantic-v2",
        "topics": [
            {
                "id": topic,
                "name": topic,
                "keywords": [topic],
                "evidence": ["src/log.py"],
                "seat_capacity": 2,
            }
            for topic in sorted({plan["topic"] for plan in plans})
        ],
        "task_plan": list(plans),
    }
    if version is not None:
        payload["dedup_contract_version"] = version
    return payload


def test_cross_task_type_retry_prompts_are_semantic_duplicates() -> None:
    left = {
        "sequence": 5,
        "topic": "log",
        "task_type": "Bugfix",
        "semantic_identity": identity(
            "log",
            "retry_recover",
            ["history_preservation", "idempotent_retry", "state_transition"],
            ["execution_log_record"],
            ["LOG-0002", "LOG-0003"],
        ),
        "semantic_contrast": [],
    }
    right = {
        "sequence": 20,
        "topic": "log",
        "task_type": "功能优化",
        "semantic_identity": identity(
            "log",
            "retry_recover",
            ["history_preservation", "idempotent_retry", "resume_from_failure"],
            ["execution_log_record"],
            ["LOG-0002", "LOG-0003"],
        ),
        "semantic_contrast": [],
    }

    result = semantic_dedup.pair_result(left, right)
    errors = semantic_dedup.map_errors(topic_map(left, right))

    assert result["collision"] is True
    assert result["shared_outcome_keys"] == [
        "history_preservation",
        "idempotent_retry",
    ]
    assert any("同 topic 语义重复" in error for error in errors)


def test_platform_admin_duplicate_incident_is_a_regression_fixture() -> None:
    fixture = json.loads(
        (ROOT / "tests/fixtures/semantic-duplicate-log-retry.json").read_text(
            encoding="utf-8"
        )
    )
    result = check_prompt_dedup.check(
        [
            (fixture["left"]["label"], fixture["left"]["prompt"]),
            (fixture["right"]["label"], fixture["right"]["prompt"]),
        ],
        0.2,
        semantic_items={
            fixture["left"]["label"]: {
                "sequence": 5,
                "topic": "log",
                "semantic_identity": identity(
                    "log",
                    "retry_recover",
                    [
                        "history_preservation",
                        "idempotent_retry",
                        "state_transition",
                    ],
                    ["execution_log_record"],
                    ["LOG-0002", "LOG-0003"],
                ),
            },
            fixture["right"]["label"]: {
                "sequence": 20,
                "topic": "log",
                "semantic_identity": identity(
                    "log",
                    "retry_recover",
                    [
                        "history_preservation",
                        "idempotent_retry",
                        "resume_from_failure",
                    ],
                    ["execution_log_record"],
                    ["LOG-0002", "LOG-0003"],
                ),
            },
        },
    )

    assert result["ok"] is False
    assert result["semantic_identity_collision_count"] == 1


def test_same_topic_distinct_operations_require_two_differences() -> None:
    left = {
        "sequence": 1,
        "topic": "log",
        "task_type": "Bugfix",
        "semantic_identity": identity(
            "log",
            "retry_recover",
            ["history_preservation", "independent_retry_record"],
            ["execution_log_record"],
            ["LOG-0002"],
        ),
        "semantic_contrast": [],
    }
    right = {
        "sequence": 2,
        "topic": "log",
        "task_type": "功能优化",
        "semantic_identity": identity(
            "log",
            "export",
            ["filtered_export", "traceable_export"],
            ["log_export_file"],
            ["LOG-0003"],
        ),
        "semantic_contrast": [
            {
                "peer_sequence": 1,
                "differences": [
                    {
                        "dimension": "operation",
                        "self": "导出筛选后的日志结果",
                        "peer": "恢复失败日志的重试记录",
                    },
                    {
                        "dimension": "observable_result",
                        "self": "用户得到可追溯的导出文件",
                        "peer": "用户得到独立的重试记录",
                    },
                ],
            }
        ],
    }

    assert semantic_dedup.map_errors(topic_map(left, right)) == []

    right["semantic_contrast"][0]["differences"] = right[
        "semantic_contrast"
    ][0]["differences"][:1]
    errors = semantic_dedup.map_errors(topic_map(left, right))
    assert any("至少需要两个不同维度" in error for error in errors)


def test_new_batch_requires_contract_but_legacy_plan_remains_readable() -> None:
    plan = {
        "sequence": 1,
        "topic": "log",
        "semantic_identity": identity(
            "log",
            "retry_recover",
            ["history_preservation", "independent_retry_record"],
            ["execution_log_record"],
            ["LOG-0002"],
        ),
    }
    legacy = topic_map(plan, version=None)

    assert semantic_dedup.map_errors(legacy) == []
    assert any(
        "dedup_contract_version" in error
        for error in semantic_dedup.map_errors(legacy, require=True)
    )


def test_prompt_dedup_uses_semantic_identity_before_task_type_skip() -> None:
    semantic_items = {
        "repo-05-bugfix[Bugfix]": {
            "sequence": 5,
            "topic": "log",
            "semantic_identity": identity(
                "log",
                "retry_recover",
                ["history_preservation", "idempotent_retry"],
                ["execution_log_record"],
                ["LOG-0002", "LOG-0003"],
            ),
        },
        "repo-20-optimization[功能优化]": {
            "sequence": 20,
            "topic": "log",
            "semantic_identity": identity(
                "log",
                "retry_recover",
                ["history_preservation", "idempotent_retry"],
                ["execution_log_record"],
                ["LOG-0002", "LOG-0003"],
            ),
        },
    }

    result = check_prompt_dedup.check(
        [
            (
                "repo-05-bugfix[Bugfix]",
                "修复执行日志重试后原记录状态被覆盖。",
            ),
            (
                "repo-20-optimization[功能优化]",
                "优化执行日志重试后的续做与历史保留。",
            ),
        ],
        0.2,
        semantic_items=semantic_items,
    )

    assert result["ok"] is False
    assert result["semantic_identity_collision_count"] == 1
    assert result["violations"][0]["kind"] == "semantic_identity_collision"


def test_final_prompt_must_name_a_declared_acceptance_entity() -> None:
    plan = {
        "sequence": 1,
        "topic": "log",
        "semantic_identity": identity(
            "log",
            "retry_recover",
            ["history_preservation", "independent_retry_record"],
            ["execution_log_record"],
            ["LOG-0002"],
        ),
    }
    mapping = topic_map(plan)

    errors = semantic_dedup.prompt_errors(
        [
            {
                "子文件夹名称": "repo-01-bugfix",
                "提示词": "修复执行日志重试后原记录被覆盖。",
            }
        ],
        mapping,
    )

    assert any("LOG-0002" in error for error in errors)
