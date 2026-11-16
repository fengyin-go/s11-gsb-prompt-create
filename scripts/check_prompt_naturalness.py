#!/usr/bin/env python3
"""Reject high-confidence mechanical prompt stitching while preserving natural prose."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


VERSION = 1
MECHANICAL_PATTERNS = (
    (
        "通用跨模块追加句",
        re.compile(
            r"(?:另一个|其他|其它|配套)(?:业务)?(?:模块|入口|链路)"
            r"[^。；！？\n]{0,32}(?:必须|要|需要)"
            r"[^。；！？\n]{0,32}(?:同步|生成|写入|形成|回写)"
        ),
    ),
    (
        "固定消费句",
        re.compile(r"必须消费同一份|必须读取同一份|跟着处理产物|随动作结果同步更新"),
    ),
    (
        "同轴条件堆叠",
        re.compile(
            r"(?:同一批次|同一对象|同一记录)[^。；！？\n]{0,8}(?:必须|要|需要)"
            r"[^。；！？\n]{0,20}(?:同时|并且|还要|并且还要)"
        ),
    ),
    (
        "显式设计裁决指令",
        re.compile(
            r"(?:需要|请)(?:自行)?判定[^。；！？\n]{0,48}(?:还是|或者)|"
            r"由你(?:根据[^。；！？\n]{0,20})?决定"
        ),
    ),
    (
        "重复业务名词",
        re.compile(r"(台账|记录|清单|批次|数据|结果)\1"),
    ),
)
GENERIC_PARAGRAPH_OPENER_RE = re.compile(
    r"^(?:另一个|相邻|配套|其它|其他|跨模块|下游业务入口|上游业务入口|"
    r"业务链路另一端|处理结论|串联主入口|同一个动作还要)"
)
BATCH_BOILERPLATE = (
    "跟着处理产物",
    "随动作结果同步更新",
    "对应起来",
    "仍可回查",
    "可相互追溯核对",
    "随之生成",
)
CONSTRAINT_WORDS = ("必须", "不能", "不得", "不允许", "只能", "需要")
SENTENCE_SPLIT_RE = re.compile(r"[。；！？!?\n]+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _compact(text: str) -> str:
    return "".join(CJK_RE.findall(text))


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_SPLIT_RE.split(text) if len(_compact(part)) >= 6]


def _record_errors(record: dict[str, Any]) -> list[str]:
    label = _text(record.get("子文件夹名称")) or "未命名"
    prompt = _text(record.get("提示词"))
    if not prompt:
        return [f"{label} 题面为空"]
    errors: list[str] = []
    compact = _compact(prompt)
    if len(compact) < 80:
        errors.append(f"{label} 题面过短，无法形成完整业务叙述")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", prompt) if part.strip()]
    if not 1 <= len(paragraphs) <= 6:
        errors.append(f"{label} 段落数 {len(paragraphs)} 不在 1..6，疑似机械拼接")
    for index, paragraph in enumerate(paragraphs[1:], start=2):
        if GENERIC_PARAGRAPH_OPENER_RE.match(paragraph):
            errors.append(
                f"{label} 第 {index} 段以通用跨模块套话开头：{paragraph[:48]}"
            )
    sentences = _sentences(prompt)
    if len(sentences) < 4:
        errors.append(f"{label} 有效业务句不足 4 句")
    for kind, pattern in MECHANICAL_PATTERNS:
        match = pattern.search(prompt)
        if match:
            errors.append(f"{label} 命中{kind}：{match.group(0)[:60]}")
    constraint_count = sum(prompt.count(word) for word in CONSTRAINT_WORDS)
    if constraint_count > 8:
        errors.append(f"{label} 硬约束词出现 {constraint_count} 次，疑似条件清单拼接")
    imperative_run = 0
    max_run = 0
    for sentence in sentences:
        if sentence.startswith(("必须", "不能", "不得", "不允许", "同一批次必须")):
            imperative_run += 1
            max_run = max(max_run, imperative_run)
        else:
            imperative_run = 0
    if max_run >= 3:
        errors.append(f"{label} 连续出现 {max_run} 个无主语约束句")
    return errors


def check(records: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            errors.append("候选记录必须是对象")
            continue
        errors.extend(_record_errors(record))

    sentence_counts: Counter[str] = Counter()
    for record in records:
        prompt = _text(record.get("提示词")) if isinstance(record, dict) else ""
        for sentence in _sentences(prompt):
            normalized = _compact(sentence)
            if len(normalized) >= 12:
                sentence_counts[normalized] += 1
    max_repeat = max(3, math.ceil(len(records) * 0.15))
    duplicate_sentences = [
        {"sentence": sentence, "count": count}
        for sentence, count in sentence_counts.items()
        if count > max_repeat
    ]
    for item in duplicate_sentences[:12]:
        errors.append(
            f"整批有 {item['count']} 条使用同一业务句：{item['sentence'][:80]}"
        )
    phrase_counts = {
        phrase: sum(phrase in _text(record.get("提示词")) for record in records if isinstance(record, dict))
        for phrase in BATCH_BOILERPLATE
    }
    repeated_phrases = [
        {"phrase": phrase, "count": count}
        for phrase, count in phrase_counts.items()
        if count > max_repeat
    ]
    for item in repeated_phrases:
        errors.append(
            f"整批有 {item['count']} 条复用模板短语：{item['phrase']}"
        )

    return {
        "ok": not errors,
        "version": VERSION,
        "checked_count": len(records),
        "error_count": len(errors),
        "duplicate_sentence_threshold": max_repeat,
        "duplicate_sentences": duplicate_sentences,
        "repeated_boilerplate": repeated_phrases,
        "errors": errors,
    }


def _read_records(parent: Path | None, workbook: str | None, prompts_file: str | None) -> list[dict[str, Any]]:
    if prompts_file:
        records = []
        for index, line in enumerate(Path(prompts_file).read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                label, task_type, prompt = parts[0], parts[1], "\t".join(parts[2:])
            else:
                label, task_type, prompt = f"prompt-{index}", "", line
            records.append({
                "子文件夹名称": label,
                "任务类型": task_type,
                "提示词": prompt,
            })
        return records
    if parent is None:
        raise ValueError("需要 --parent 或 --prompts-file")
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    import batch_prompt_workbook
    path = parent / (workbook or "tasks.xlsx")
    return batch_prompt_workbook.read_workbook(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent")
    parser.add_argument("--workbook")
    parser.add_argument("--prompts-file")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        records = _read_records(
            Path(args.parent).expanduser().resolve() if args.parent else None,
            args.workbook,
            args.prompts_file,
        )
        result = check(records)
    except (OSError, ValueError) as exc:
        result = {"ok": False, "version": VERSION, "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
