"""Reject conversational request wrappers from generated S11 prompts."""

from __future__ import annotations

import re
from typing import Any


STYLE_GATE_VERSION = 1

# These phrases describe the act of asking for a task rather than the task itself.
# Keep business time language such as "本次结算周期" untouched.
FRAME_RULES = (
    (
        "需求/任务自指",
        re.compile(r"(?:本(?:次)?|这次|此次|这个|该)(?:需求|任务)"),
    ),
    (
        "第一人称请求",
        re.compile(
            r"(?:这次|本次|此次)(?:我)?(?:需要|希望|要求|想(?:要)?)"
            r"|我(?:需要|希望|要求|想要|想)"
            r"|请你帮我"
            r"|(?:^|[，。；！？\n])\s*帮我(?:实现|完成|处理|调整|修改|优化|增加|新增)"
        ),
    ),
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def frame_hits(prompt: str) -> list[tuple[str, str]]:
    """Return unique banned frame kind/phrase pairs in prompt order."""
    hits: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind, pattern in FRAME_RULES:
        for match in pattern.finditer(prompt):
            phrase = match.group(0).lstrip("，。；！？\n ")
            key = (kind, phrase)
            if phrase and key not in seen:
                hits.append(key)
                seen.add(key)
    return hits


def prompt_errors(records: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for record in records:
        prompt = _text(record.get("提示词"))
        if not prompt:
            continue
        label = _text(record.get("子文件夹名称")) or "未命名"
        for kind, phrase in frame_hits(prompt):
            errors.append(
                f"{label} 题面使用了{kind}“{phrase}”；删除需求叙述外壳，"
                "直接用“请……”写目标行为、边界和可观察结果"
            )
    return errors
