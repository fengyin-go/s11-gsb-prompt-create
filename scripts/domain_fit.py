#!/usr/bin/env python3
"""Deterministic checks for the per-task domain contract.

The semantic judgement still belongs to the producing AI. These checks ensure a
prompt cannot pass with only a project-level domain label or a decorative
business term: it must keep the planned topic, a real domain object, an
observable domain result, and source evidence in the repository.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


FOLDER_PLAN_RE = re.compile(r"-(0[1-9]|[1-9][0-9])-(development|bugfix|optimization|refactor|migration)$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_text(raw) for raw in value) if item]


def sequence_for_folder(folder: str) -> tuple[int, str] | None:
    match = FOLDER_PLAN_RE.search(folder or "")
    return (int(match.group(1)), match.group(2)) if match else None


def _source_problem(repo: Path | None, raw: str) -> str:
    if repo is None:
        return "缺少仓库，无法核对领域源码依据"
    rel = raw.split(":", 1)[0].strip().strip("`")
    path = Path(rel)
    if not rel or path.is_absolute() or ".." in path.parts:
        return f"领域源码依据不安全：{raw}"
    target = Path(repo) / path
    if not target.exists():
        return f"领域源码依据不存在：{raw}"
    return ""


def map_errors(topic_map: dict[str, Any], repo: Path | None) -> list[str]:
    errors: list[str] = []
    if not _text(topic_map.get("domain")):
        errors.append("topic map 缺少 domain")
    if not _text(topic_map.get("subdirection")):
        errors.append("topic map 缺少 subdirection")
    plan_by_sequence = {int(item.get("sequence")): item for item in topic_map.get("task_plan", [])}
    topics = {str(item.get("id") or ""): item for item in topic_map.get("topics", [])}
    for topic in topics.values():
        if not _text_list(topic.get("domain_objects")):
            errors.append(f"topic {topic.get('id')} 缺少 domain_objects")
        if not _text_list(topic.get("domain_results")):
            errors.append(f"topic {topic.get('id')} 缺少 domain_results")
    for sequence, plan in sorted(plan_by_sequence.items()):
        fit = plan.get("domain_fit")
        if not isinstance(fit, dict):
            errors.append(f"task_plan sequence {sequence} 缺少 domain_fit")
            continue
        if fit.get("conclusion") != "pass":
            errors.append(f"task_plan sequence {sequence} 的 domain_fit.conclusion 必须是 pass")
        for key in ("main_task", "business_result"):
            if not _text(fit.get(key)):
                errors.append(f"task_plan sequence {sequence} 的 domain_fit 缺少 {key}")
        evidence = _text_list(fit.get("source_evidence"))
        if not evidence:
            errors.append(f"task_plan sequence {sequence} 的 domain_fit 缺少 source_evidence")
        for raw in evidence:
            problem = _source_problem(repo, raw)
            if problem:
                errors.append(f"task_plan sequence {sequence}：{problem}")
        topic = topics.get(_text(plan.get("topic")))
        if topic is None:
            errors.append(f"task_plan sequence {sequence} 指向不存在的 topic")
    return errors


def prompt_errors(records: list[dict[str, str]], topic_map: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    topics = {str(item.get("id") or ""): item for item in topic_map.get("topics", [])}
    plans = {int(item.get("sequence")): item for item in topic_map.get("task_plan", [])}
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
        if plan is None or plan.get("slug") != slug:
            errors.append(f"{folder} 找不到对应的 task_plan 题位")
            continue
        topic = topics.get(_text(plan.get("topic")))
        if topic is None:
            errors.append(f"{folder} 指向不存在的 topic")
            continue
        objects = _text_list(topic.get("domain_objects"))
        results = _text_list(topic.get("domain_results"))
        if not any(term in prompt for term in objects):
            errors.append(f"{folder} 题面没有出现领域对象：{'、'.join(objects)}")
        if not any(term in prompt for term in results):
            errors.append(f"{folder} 题面没有出现可观察领域结果：{'、'.join(results)}")
        if not any(keyword in prompt for keyword in _text_list(topic.get("keywords"))):
            errors.append(f"{folder} 题面没有落到计划中的领域 topic {topic.get('id')}")
    return errors


def contract_for_sequence(topic_map: dict[str, Any], sequence: int) -> dict[str, Any]:
    plan = next(
        (item for item in topic_map.get("task_plan", []) if int(item.get("sequence")) == int(sequence)),
        None,
    )
    if plan is None:
        raise ValueError(f"topic map 没有 sequence {sequence}")
    topic = next(
        (item for item in topic_map.get("topics", []) if item.get("id") == plan.get("topic")),
        None,
    )
    if topic is None:
        raise ValueError(f"task_plan sequence {sequence} 指向不存在的 topic")
    fit = plan.get("domain_fit")
    if not isinstance(fit, dict):
        raise ValueError(f"task_plan sequence {sequence} 缺少 domain_fit")
    return {
        "version": 1,
        "domain": topic_map.get("domain"),
        "subdirection": topic_map.get("subdirection"),
        "topic_id": topic.get("id"),
        "topic_name": topic.get("name"),
        "main_task": fit.get("main_task"),
        "business_result": fit.get("business_result"),
        "source_evidence": _text_list(fit.get("source_evidence")),
        "domain_objects": _text_list(topic.get("domain_objects")),
        "domain_results": _text_list(topic.get("domain_results")),
        "conclusion": fit.get("conclusion"),
    }
