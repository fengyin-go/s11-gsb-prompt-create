"""Reject conversational request wrappers from generated S11 prompts."""

from __future__ import annotations

import re
from typing import Any


STYLE_GATE_VERSION = 6

# These phrases describe the act of asking for a task rather than the task itself.
# Keep business time language such as "本次结算周期" untouched.
FRAME_RULES = (
    (
        "显式委托设计取舍",
        re.compile(
            r"(?:由你|由设计者|请你|请自行|自行|自己)[^。；！？\n]{0,18}"
            r"(?:判断|决定|选择|权衡|取舍|确定|设计)"
            r"|(?:判断|决定|选择|权衡|取舍|设计)[^。；！？\n]{0,18}(?:由你|由设计者)"
            r"|(?:全量|增量)[^。；！？\n]{0,28}(?:均可|都可以|可自行选择)"
        ),
    ),
    (
        "上下文来源限制",
        re.compile(
            r"(?:只(?:能)?|仅(?:能|限)?)(?:依据|基于|使用|参考|读取|根据|允许使用)"
            r"[^。；！？\n]{0,60}(?:当前仓库|本(?:轮|次)题面|本轮提示词|当前工作区|本项目代码|本仓库)"
            r"|(?:不(?:得|要|允许)?|禁止|不能|不可|避免|忽略)(?:读取|使用|参考|访问|依赖)?"
            r"[^。；！？\n]{0,40}(?:其他会话|跨会话|答案笔记|历史对话|对话缓存|会话记忆|之前的对话|历史会话)"
            r"|(?:禁止|不得|不要|不允许)[^。；！？\n]{0,20}(?:跨会话记忆|答案笔记|历史对话缓存)"
            r"|(?:上下文隔离(?:声明|要求)?|会话隔离声明)\s*[：:]"
            r"|(?:仅|只)(?:以|限于|能查看|可参考)[^。；！？\n]{0,30}(?:当前代码|当前仓库|本仓库|本轮题面|当前工作区)"
            r"|(?:当前仓库|当前工作区|本轮题面)[^。；！？\n]{0,30}(?:唯一|全部)(?:信息|上下文|参考|依据)来源"
            r"|(?:不得|禁止|不要)[^。；！？\n]{0,20}(?:外部上下文|历史答案|已有答案)"
            r"|(?:only\s+(?:use|rely\s+on)|use\s+only|do\s+not\s+(?:read|use|access)|ignore)"
            r"[^.!?\n]{0,100}(?:current\s+(?:repository|workspace)|other\s+sessions|"
            r"cross-session|answer\s+notes|conversation\s+history|previous\s+conversations)",
            re.IGNORECASE,
        ),
    ),
    (
        "空泛模板拼接",
        re.compile(
            r"结果要关联另一个入口"
            r"|保留后续取数所需的状态"
            r"|(?:遇到|存在)两版来源时如何取舍"
        ),
    ),
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
        if "`" in prompt or "｀" in prompt:
            errors.append(f"{label} 题面使用了反引号，请直接保留字段、表名或命令的原文")
        if "水位" in prompt:
            errors.append(
                f'{label} 题面使用了“水位”，请说明具体的日志位置、提交记录或采集截止点'
            )
        for kind, phrase in frame_hits(prompt):
            if kind == "显式委托设计取舍":
                errors.append(
                    f"{label} 题面使用了{kind}“{phrase}”；"
                    "将真实冲突、处理边界和可观察结果写进业务要求，"
                    "不要把选择权声明或内部方案评审字段写进题面"
                )
                continue
            if kind == "空泛模板拼接":
                errors.append(
                    f"{label} 题面使用了{kind}“{phrase}”；"
                    "写清真实业务对象、关联模块及预期行为，不靠拼接通用尾句满足质量闸"
                )
                continue
            if kind == "上下文来源限制":
                errors.append(
                    f"{label} 题面使用了{kind}“{phrase}”；"
                    "删除该限定，仅保留业务问题、目标行为、边界和可观察结果"
                )
                continue
            errors.append(
                f"{label} 题面使用了{kind}“{phrase}”；删除需求叙述外壳，"
                "按题型自然起句，直接写业务问题、目标行为、边界和可观察结果"
            )
    return errors
