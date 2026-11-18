#!/usr/bin/env python3
"""Type-independent semantic identity checks for S11 prompt plans.

Task type and family describe how a prompt is written, not whether its business
intent differs from another prompt.  This module compares the canonical object,
operation, outcome, artifact and acceptance identities before repositories are
created and again when final prompt text is written.
"""

from __future__ import annotations

import re
from typing import Any


CONTRACT_VERSION = 1
IDENTITY_VERSION = 1

OPERATION_KEYS = {
    "create",
    "update",
    "query",
    "export",
    "validate",
    "retry_recover",
    "archive",
    "rollback",
    "publish",
    "deduplicate",
    "schedule",
    "notify",
    "migrate",
    "delete",
}

CONTRAST_DIMENSIONS = {
    "operation",
    "observable_result",
    "artifact",
    "acceptance",
}

_KEY_RE = re.compile(r"[a-z][a-z0-9_]*")
_TASK_FOLDER_RE = re.compile(
    r"-(?P<sequence>\d+)-(?:development|bugfix|optimization|refactor|migration)$"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _text(item))]


def _key_list_errors(value: Any, where: str, *, minimum: int) -> list[str]:
    values = _text_list(value)
    if len(values) < minimum:
        return [f"{where} 至少需要 {minimum} 个值"]
    errors: list[str] = []
    if len(values) != len(set(values)):
        errors.append(f"{where} 不能重复")
    invalid = [item for item in values if not _KEY_RE.fullmatch(item)]
    if invalid:
        errors.append(f"{where} 必须使用小写蛇形标识：{'、'.join(invalid)}")
    return errors


def identity_errors(identity: Any, topic: str, where: str) -> list[str]:
    """Validate one canonical semantic identity."""
    if not isinstance(identity, dict):
        return [f"{where} 缺少 semantic_identity"]
    errors: list[str] = []
    try:
        version = int(identity.get("version"))
    except (TypeError, ValueError):
        version = 0
    if version != IDENTITY_VERSION:
        errors.append(f"{where}.version 必须为 {IDENTITY_VERSION}")

    object_key = _text(identity.get("object_key"))
    if not object_key:
        errors.append(f"{where}.object_key 不能为空")
    elif object_key != topic:
        errors.append(
            f"{where}.object_key 必须等于 topic={topic!r}，"
            "不能用更细的对象名规避同主题查重"
        )

    operation = _text(identity.get("operation_key"))
    if operation not in OPERATION_KEYS:
        errors.append(
            f"{where}.operation_key 不可用：{operation or '空'}；"
            f"允许值：{', '.join(sorted(OPERATION_KEYS))}"
        )

    errors.extend(
        _key_list_errors(
            identity.get("outcome_keys"),
            f"{where}.outcome_keys",
            minimum=2,
        )
    )
    errors.extend(
        _key_list_errors(
            identity.get("artifact_keys"),
            f"{where}.artifact_keys",
            minimum=1,
        )
    )
    acceptances = _text_list(identity.get("acceptance_entities"))
    if not acceptances:
        errors.append(f"{where}.acceptance_entities 至少需要 1 个值")
    elif len(acceptances) != len(set(acceptances)):
        errors.append(f"{where}.acceptance_entities 不能重复")
    return errors


def _contrast_entries(plan: dict[str, Any]) -> list[dict[str, Any]]:
    raw = plan.get("semantic_contrast")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _contrast_error(
    plan: dict[str, Any],
    peer: dict[str, Any],
) -> str | None:
    peer_sequence = int(peer["sequence"])
    candidates = [
        item
        for item in _contrast_entries(plan)
        if str(item.get("peer_sequence")).isdigit()
        and int(item["peer_sequence"]) == peer_sequence
    ]
    if not candidates:
        return (
            f"sequence={plan['sequence']} 与同 topic 的 sequence={peer_sequence} "
            "缺少 semantic_contrast"
        )
    entry = candidates[-1]
    differences = entry.get("differences")
    if not isinstance(differences, list):
        return (
            f"sequence={plan['sequence']} 对 sequence={peer_sequence} 的 "
            "semantic_contrast.differences 必须是数组"
        )
    valid: dict[str, tuple[str, str]] = {}
    for index, item in enumerate(differences):
        where = (
            f"sequence={plan['sequence']} 对 sequence={peer_sequence} "
            f"的 semantic_contrast.differences[{index}]"
        )
        if not isinstance(item, dict):
            return f"{where} 必须是对象"
        dimension = _text(item.get("dimension"))
        self_text = _text(item.get("self"))
        peer_text = _text(item.get("peer"))
        if dimension not in CONTRAST_DIMENSIONS:
            return (
                f"{where}.dimension 不可用：{dimension or '空'}；"
                f"允许值：{', '.join(sorted(CONTRAST_DIMENSIONS))}"
            )
        if len(self_text) < 4 or len(peer_text) < 4:
            return f"{where} 的 self/peer 必须写清具体差异"
        if self_text == peer_text:
            return f"{where} 的 self/peer 不能相同"
        valid[dimension] = (self_text, peer_text)
    if len(valid) < 2:
        return (
            f"sequence={plan['sequence']} 对 sequence={peer_sequence} "
            "至少需要两个不同维度的 semantic_contrast"
        )
    if not ({"operation", "observable_result"} & set(valid)):
        return (
            f"sequence={plan['sequence']} 对 sequence={peer_sequence} "
            "的差异必须包含 operation 或 observable_result"
        )
    return None


def pair_result(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Compare two normalized task plans using their semantic identities."""
    left_identity = left.get("semantic_identity") or {}
    right_identity = right.get("semantic_identity") or {}
    same_topic = _text(left.get("topic")) == _text(right.get("topic"))
    same_object = (
        same_topic
        and _text(left_identity.get("object_key"))
        and _text(left_identity.get("object_key"))
        == _text(right_identity.get("object_key"))
    )
    same_operation = (
        _text(left_identity.get("operation_key"))
        and _text(left_identity.get("operation_key"))
        == _text(right_identity.get("operation_key"))
    )
    shared_outcomes = sorted(
        set(_text_list(left_identity.get("outcome_keys")))
        & set(_text_list(right_identity.get("outcome_keys")))
    )
    shared_artifacts = sorted(
        set(_text_list(left_identity.get("artifact_keys")))
        & set(_text_list(right_identity.get("artifact_keys")))
    )
    shared_acceptance = sorted(
        set(_text_list(left_identity.get("acceptance_entities")))
        & set(_text_list(right_identity.get("acceptance_entities")))
    )
    collision = bool(
        same_object
        and same_operation
        and (shared_outcomes or shared_artifacts or shared_acceptance)
    )
    return {
        "same_topic": same_topic,
        "same_object": bool(same_object),
        "same_operation": bool(same_operation),
        "shared_outcome_keys": shared_outcomes,
        "shared_artifact_keys": shared_artifacts,
        "shared_acceptance_entities": shared_acceptance,
        "collision": collision,
        "score": (
            int(bool(same_object))
            + int(bool(same_operation))
            + int(bool(shared_outcomes))
            + int(bool(shared_artifacts))
            + int(bool(shared_acceptance))
        ),
    }


def map_errors(topic_map: dict[str, Any], *, require: bool = False) -> list[str]:
    """Validate the v1 semantic identity contract and same-topic contrasts."""
    if not isinstance(topic_map, dict):
        return ["topic map 必须是对象"]
    raw_version = topic_map.get("dedup_contract_version")
    if raw_version is None:
        return (
            ["新批次必须填写 dedup_contract_version: 1"]
            if require
            else []
        )
    try:
        version = int(raw_version)
    except (TypeError, ValueError):
        version = 0
    if version != CONTRACT_VERSION:
        return [f"dedup_contract_version 必须为 {CONTRACT_VERSION}"]

    plans = [
        item
        for item in topic_map.get("task_plan", [])
        if isinstance(item, dict)
    ]
    topics = {
        _text(item.get("id")): item
        for item in topic_map.get("topics", [])
        if isinstance(item, dict)
    }
    errors: list[str] = []
    by_sequence: dict[int, dict[str, Any]] = {}
    for plan in plans:
        try:
            sequence = int(plan.get("sequence"))
        except (TypeError, ValueError):
            errors.append("task_plan 存在无效 sequence，无法校验 semantic_identity")
            continue
        by_sequence[sequence] = plan
        topic = _text(plan.get("topic"))
        if topic not in topics:
            errors.append(f"sequence={sequence} 引用了不存在的 topic={topic!r}")
        errors.extend(
            identity_errors(
                plan.get("semantic_identity"),
                topic,
                f"sequence={sequence}",
            )
        )

    for plan in plans:
        for entry in _contrast_entries(plan):
            raw_peer = entry.get("peer_sequence")
            try:
                peer_sequence = int(raw_peer)
            except (TypeError, ValueError):
                errors.append(
                    f"sequence={plan.get('sequence')} 的 semantic_contrast.peer_sequence 无效"
                )
                continue
            peer = by_sequence.get(peer_sequence)
            if peer is None:
                errors.append(
                    f"sequence={plan.get('sequence')} 的 semantic_contrast "
                    f"引用了不存在的 sequence={peer_sequence}"
                )
            elif _text(plan.get("topic")) != _text(peer.get("topic")):
                errors.append(
                    f"sequence={plan.get('sequence')} 对 sequence={peer_sequence} "
                    "的 semantic_contrast 只能比较同 topic 题位"
                )

    for index, left in enumerate(plans):
        for right in plans[:index]:
            if _text(left.get("topic")) != _text(right.get("topic")):
                continue
            compared = pair_result(left, right)
            if compared["collision"]:
                errors.append(
                    f"同 topic 语义重复：sequence={left.get('sequence')} 与 "
                    f"sequence={right.get('sequence')} 共用 "
                    f"object={_text((left.get('semantic_identity') or {}).get('object_key'))}、"
                    f"operation={_text((left.get('semantic_identity') or {}).get('operation_key'))}，"
                    f"共享 outcome={','.join(compared['shared_outcome_keys']) or '无'}、"
                    f"artifact={','.join(compared['shared_artifact_keys']) or '无'}、"
                    f"acceptance={','.join(compared['shared_acceptance_entities']) or '无'}"
                )
                continue
            left_error = _contrast_error(left, right)
            right_error = _contrast_error(right, left)
            if left_error and right_error:
                errors.append(left_error)
    return errors


def sequence_from_folder(folder: str) -> int | None:
    match = _TASK_FOLDER_RE.search(_text(folder))
    return int(match.group("sequence")) if match else None


def items_for_labels(
    topic_map: dict[str, Any],
    labels: list[str],
) -> dict[str, dict[str, Any]]:
    """Bind topic-map semantic identities to workbook labels."""
    by_sequence = {
        int(plan["sequence"]): plan
        for plan in topic_map.get("task_plan", [])
        if isinstance(plan, dict) and str(plan.get("sequence")).isdigit()
    }
    bound: dict[str, dict[str, Any]] = {}
    for label in labels:
        folder = _text(label).split("[", 1)[0]
        sequence = sequence_from_folder(folder)
        if sequence is None or sequence not in by_sequence:
            continue
        plan = by_sequence[sequence]
        bound[label] = {
            "sequence": sequence,
            "topic": _text(plan.get("topic")),
            "semantic_identity": plan.get("semantic_identity"),
            "semantic_contrast": plan.get("semantic_contrast"),
        }
    return bound


def prompt_errors(
    records: list[dict[str, Any]],
    topic_map: dict[str, Any],
) -> list[str]:
    """Require final prompts to name at least one declared acceptance entity."""
    if topic_map.get("dedup_contract_version") is None:
        return []
    by_sequence = {
        int(plan["sequence"]): plan
        for plan in topic_map.get("task_plan", [])
        if isinstance(plan, dict) and str(plan.get("sequence")).isdigit()
    }
    errors: list[str] = []
    for record in records:
        prompt = _text(record.get("提示词"))
        folder = _text(record.get("子文件夹名称"))
        if not prompt or not folder:
            continue
        sequence = sequence_from_folder(folder)
        plan = by_sequence.get(sequence or -1)
        if plan is None:
            continue
        identity = plan.get("semantic_identity") or {}
        entities = _text_list(identity.get("acceptance_entities"))
        if entities and not any(entity in prompt for entity in entities):
            errors.append(
                f"{folder} 的题面没有写出 semantic_identity 的任一验收对象："
                + "、".join(entities)
            )
    return errors
