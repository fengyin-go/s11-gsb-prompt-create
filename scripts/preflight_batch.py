#!/usr/bin/env python3
"""Read-only aggregate preflight for a complete S11 prompt candidate batch."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import batch_prompt_workbook as workbook
import check_prompt_difficulty
import check_prompt_g16


RULES = (
    "coverage",
    "opportunity_map",
    "prompt_style",
    "prompt_naturalness",
    "domain",
    "type_intent",
    "difficulty_contract",
    "theme",
    "structural_difficulty",
    "semantic_intent",
    "batch_dedup",
    "g16",
    "g16_strict",
    "cross_project",
    "cross_project_coverage",
    "platform_dedup",
    "platform_forbidden",
    "write_gate",
)


def _load_candidates(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("候选文件必须是对象数组，或包含 records 对象数组")
    return rows


def overlay_candidates(
    records: list[dict[str, str]],
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[str]]:
    merged = [dict(record) for record in records]
    by_name = {str(record.get("子文件夹名称") or ""): record for record in merged}
    missing: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        folder = str(item.get("folder") or item.get("子文件夹名称") or "").strip()
        prompt = str(item.get("prompt") or item.get("提示词") or "").strip()
        if not folder or not prompt:
            raise ValueError("每个候选都必须包含 folder/子文件夹名称和 prompt/提示词")
        if folder in seen:
            raise ValueError(f"候选编号重复：{folder}")
        seen.add(folder)
        if folder not in by_name:
            missing.append(folder)
            continue
        row = by_name[folder]
        frozen_type = str(row.get("任务类型") or "").strip()
        candidate_type = str(item.get("task_type") or frozen_type).strip()
        if candidate_type != frozen_type:
            raise ValueError(
                f"{folder} 的任务类型为 {candidate_type}，与冻结计划 {frozen_type} 不一致"
            )
        row["提示词"] = prompt
        row["状态"] = "预检候选"
        if item.get("note") is not None:
            row["备注"] = str(item["note"])
        elif item.get("备注") is not None:
            row["备注"] = str(item["备注"])
    return merged, missing


def _folder_for_text(text: str, folders: list[str]) -> str:
    for folder in sorted(folders, key=len, reverse=True):
        if folder and folder in text:
            return folder
    return "__batch__"


class FindingCollector:
    def __init__(self, folders: list[str]) -> None:
        self.folders = list(folders)
        self.findings: list[dict[str, Any]] = []
        self._seen: set[tuple[str, str, str]] = set()
        self.rules: dict[str, dict[str, Any]] = {
            rule: {"ok": True, "finding_count": 0, "findings": []}
            for rule in RULES
        }
        self.by_candidate: dict[str, dict[str, Any]] = {
            folder: {"ok": True, "findings": []}
            for folder in folders
        }

    def add(
        self,
        rule: str,
        message: str,
        *,
        candidate: str = "",
        related_candidate: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        text = str(message or "").strip()
        if not text:
            return
        folder = candidate if candidate in self.by_candidate else _folder_for_text(
            text, self.folders
        )
        related = (
            related_candidate
            if related_candidate in self.by_candidate
            else _folder_for_text(related_candidate, self.folders)
            if related_candidate
            else ""
        )
        signature = (rule, folder, text)
        if signature in self._seen:
            return
        self._seen.add(signature)
        finding = {
            "rule": rule,
            "candidate": folder,
            "message": text,
        }
        if related:
            finding["related_candidate"] = related
        if details:
            finding["details"] = details
        self.findings.append(finding)
        rule_info = self.rules.setdefault(
            rule, {"ok": True, "finding_count": 0, "findings": []}
        )
        rule_info["ok"] = False
        rule_info["finding_count"] += 1
        rule_info["findings"].append(finding)
        candidate_info = self.by_candidate.setdefault(
            folder, {"ok": True, "findings": []}
        )
        candidate_info["ok"] = False
        candidate_info["findings"].append(finding)

    def add_strings(self, rule: str, errors: list[Any]) -> None:
        for error in errors:
            self.add(rule, str(error))

    def result(self) -> dict[str, Any]:
        counts = Counter(item["rule"] for item in self.findings)
        return {
            "ok": not self.findings,
            "finding_count": len(self.findings),
            "rule_counts": dict(sorted(counts.items())),
            "rules": self.rules,
            "candidates": self.by_candidate,
            "findings": self.findings,
        }


def _gate_items(records: list[dict[str, str]]) -> list[tuple[str, str, str]]:
    return [
        (
            str(record.get("子文件夹名称") or ""),
            str(record.get("任务类型") or ""),
            str(record.get("提示词") or ""),
        )
        for record in records
        if str(record.get("提示词") or "").strip()
    ]


def _collect_write_gate(
    collector: FindingCollector,
    gate: dict[str, Any],
) -> None:
    collector.add_strings(
        "prompt_style",
        list((gate.get("prompt_style") or {}).get("problems") or []),
    )
    collector.add_strings(
        "prompt_naturalness",
        list((gate.get("prompt_naturalness") or {}).get("problems") or []),
    )
    collector.add_strings(
        "domain",
        list((gate.get("domain") or {}).get("problems") or []),
    )
    collector.add_strings(
        "type_intent",
        list((gate.get("type_intent") or {}).get("problems") or []),
    )
    collector.add_strings(
        "difficulty_contract",
        list((gate.get("difficulty_contract") or {}).get("problems") or []),
    )
    collector.add_strings(
        "semantic_intent",
        list((gate.get("semantic_identity") or {}).get("problems") or []),
    )
    for violation in (gate.get("theme") or {}).get("violations") or []:
        collector.add("theme", json.dumps(violation, ensure_ascii=False), details=violation)
    for violation in (gate.get("difficulty") or {}).get("violations") or []:
        reasons = "；".join(str(reason) for reason in violation.get("reasons") or [])
        collector.add(
            "structural_difficulty",
            f"{violation.get('label') or '未命名'}：{reasons}",
            candidate=str(violation.get("label") or "").split("[", 1)[0],
            details=violation,
        )
    for violation in (gate.get("dedup") or {}).get("violations") or []:
        left = str(violation.get("left") or "").split("[", 1)[0]
        right = str(violation.get("right") or "").split("[", 1)[0]
        rule = (
            "semantic_intent"
            if str(violation.get("kind") or "").startswith("semantic_identity_")
            else "batch_dedup"
        )
        collector.add(
            rule,
            (
                f"{left or '批内候选'} 与 {right or '批内候选'} "
                f"{violation.get('kind')} {violation.get('score')}"
            ),
            candidate=left,
            related_candidate=right,
            details=violation,
        )


def _collect_g16(
    collector: FindingCollector,
    items: list[tuple[str, str, str]],
    justifications: dict[str, str],
    *,
    strict: bool,
) -> dict[str, Any]:
    result = check_prompt_g16.check(
        items,
        justifications,
        strict=strict,
        notes_can_override=False,
    )
    rule = "g16_strict" if strict else "g16"
    for item in result.get("items") or []:
        if item.get("gate_ok"):
            continue
        reasons = "；".join(str(reason) for reason in item.get("reasons") or [])
        collector.add(
            rule,
            f"{item.get('label') or '未命名'}：{reasons}",
            candidate=str(item.get("label") or ""),
            details={
                "violation_kinds": item.get("violation_kinds"),
                "reasons": item.get("reasons"),
                "task_type": item.get("task_type"),
            },
        )
    return result


def _collect_cross_project(
    collector: FindingCollector,
    result: dict[str, Any],
) -> None:
    if not result:
        collector.add("cross_project", "跨项目检查没有返回结果")
        return
    for violation in result.get("violations") or []:
        candidate = str((violation.get("candidate") or {}).get("label") or "")
        peer = str((violation.get("peer") or {}).get("label") or "")
        kinds = [
            str(item.get("kind") or "")
            for item in violation.get("violations") or []
        ]
        collector.add(
            "cross_project",
            (
                f"{candidate or '候选'} 与 {peer or '历史题'} 命中 "
                f"{'、'.join(kind for kind in kinds if kind) or '跨项目风险'}"
            ),
            candidate=candidate,
            details=violation,
        )
    for issue in result.get("coverage_issues") or []:
        collector.add(
            "cross_project_coverage",
            json.dumps(issue, ensure_ascii=False),
            details=issue,
        )
    for violation in result.get("platform_dedup_violations") or []:
        candidate = str((violation.get("candidate") or {}).get("label") or "")
        peer = str((violation.get("peer") or {}).get("label") or "")
        kinds = [
            str(item.get("kind") or "")
            for item in violation.get("violations") or []
        ]
        collector.add(
            "platform_dedup",
            (
                f"{candidate or '候选'} 与平台题 {peer or '历史题'} 命中 "
                f"{'、'.join(kind for kind in kinds if kind) or '平台相似风险'}"
            ),
            candidate=candidate,
            details=violation,
        )
    for violation in result.get("platform_forbidden_topic_violations") or []:
        candidate = str((violation.get("candidate") or {}).get("label") or "")
        collector.add(
            "platform_forbidden",
            str(violation.get("message") or "命中平台禁用题眼"),
            candidate=candidate,
            details=violation,
        )


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        Path(temp_name).replace(path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def check(
    parent: str | Path,
    *,
    repo: str | Path,
    topic_map: str | Path,
    candidates_file: str | Path,
    opportunity_map: str | Path | None = None,
    require_opportunity_map: bool = False,
    allow_pending: bool = False,
    no_cache: bool = False,
    output: str | Path | None = None,
) -> dict[str, Any]:
    parent = Path(parent).expanduser().resolve()
    repo = Path(repo).expanduser().resolve()
    topic_path = Path(topic_map).expanduser().resolve()
    workbook_path = parent / workbook.DEFAULT_WORKBOOK
    if not workbook_path.is_file():
        raise ValueError(f"批次工作簿不存在：{workbook_path}")
    candidate_rows = _load_candidates(candidates_file)
    records, missing_candidate_rows = overlay_candidates(
        workbook.read_workbook(workbook_path),
        candidate_rows,
    )
    folders = [
        str(record.get("子文件夹名称") or "")
        for record in records
        if record.get("子文件夹名称")
    ]
    collector = FindingCollector(folders)
    if missing_candidate_rows:
        for folder in missing_candidate_rows:
            collector.add("coverage", f"候选工作簿中没有冻结题位：{folder}", candidate=folder)
    provided_folders = {
        str(item.get("folder") or item.get("子文件夹名称") or "").strip()
        for item in candidate_rows
    }
    if not allow_pending:
        for folder in sorted(set(folders) - provided_folders):
            collector.add(
                "coverage",
                f"{folder} 没有出现在本次完整候选文件；不能用工作簿旧题面替代预检",
                candidate=folder,
            )
    missing_prompts = [
        str(record.get("子文件夹名称") or "")
        for record in records
        if not str(record.get("提示词") or "").strip()
    ]
    if missing_prompts and not allow_pending:
        for folder in missing_prompts:
            collector.add(
                "coverage",
                f"{folder} 尚未提供最终题面；完整预检不接受待生成题位",
                candidate=folder,
            )

    from repo_topic_policy import load_topic_map

    topic: dict[str, Any] = {}
    gate: dict[str, Any] = {}
    try:
        topic = load_topic_map(
            repo=repo,
            parent=parent,
            explicit=topic_path,
            required=True,
        )
    except (OSError, ValueError, SystemExit) as exc:
        collector.add("write_gate", f"topic map 无法加载：{exc}")
    if topic:
        try:
            gate = workbook.run_write_gates(
                parent,
                records,
                repo=repo,
                max_per_mode=3,
                topic_map=topic,
            )
            _collect_write_gate(collector, gate)
        except (OSError, ValueError, SystemExit) as exc:
            collector.add("write_gate", f"批量写表闸无法完成：{exc}")

    items = _gate_items(records)
    justifications = {
        str(record.get("子文件夹名称") or ""): str(record.get("备注") or "")
        for record in records
        if str(record.get("备注") or "").startswith(
            check_prompt_difficulty.JUSTIFICATION_PREFIX
        )
    }
    try:
        g16 = _collect_g16(collector, items, justifications, strict=False)
        g16_strict = _collect_g16(collector, items, justifications, strict=True)
    except Exception as exc:  # noqa: BLE001 - keep other independent gates running.
        g16 = {"ok": False, "error": str(exc)}
        g16_strict = {"ok": False, "error": str(exc)}
        collector.add("g16", f"G16/G19 检查无法完成：{exc}")

    opportunity: dict[str, Any] | None = None
    try:
        opportunity = workbook._validate_opportunity_map(
            repo,
            topic_path,
            opportunity_map,
            required=require_opportunity_map,
        )
    except (SystemExit, OSError, ValueError) as exc:
        collector.add("opportunity_map", str(exc))

    cross: dict[str, Any] = {}
    config_path, config = workbook.load_cross_project_config(parent)
    if config_path is None:
        collector.add("cross_project", "缺少 gsb-cross-project.json 跨项目配置")
    else:
        try:
            cross_config = (
                {**config, "cache_enabled": False}
                if no_cache
                else config
            )
            cross = workbook.run_cross_project_batch_gate(
                parent,
                config_path,
                cross_config,
                records,
                raise_on_failure=False,
            )
            _collect_cross_project(collector, cross)
        except (OSError, ValueError, SystemExit) as exc:
            collector.add("cross_project", f"跨项目预检无法完成：{exc}")

    report_path = (
        Path(output).expanduser().resolve()
        if output
        else parent / "preflight-report.json"
    )
    summary = collector.result()
    result = {
        "ok": summary["ok"],
        "scope": "read-only aggregate preflight; formal workbook and final handoff gates still required",
        "checked_prompts": len(items),
        "workbook": str(workbook_path),
        "repo": str(repo),
        "topic_map": str(topic_path),
        "candidate_file": str(Path(candidates_file).expanduser().resolve()),
        "missing_prompts": missing_prompts,
        "allow_pending": allow_pending,
        "cache_enabled": not no_cache,
        "opportunity_map": opportunity,
        "write_gate": gate,
        "g16": g16,
        "g16_strict": g16_strict,
        "cross_project": cross,
        **summary,
        "report": str(report_path),
    }
    _save_json(report_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--topic-map", required=True)
    parser.add_argument("--candidates-file", required=True)
    parser.add_argument("--opportunity-map")
    parser.add_argument("--require-opportunity-map", action="store_true")
    parser.add_argument(
        "--allow-pending",
        action="store_true",
        help="允许工作簿保留未生成题位；正式写表前不得使用",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="禁用跨项目比较增量缓存并执行全量重算",
    )
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = check(
            args.parent,
            repo=args.repo,
            topic_map=args.topic_map,
            candidates_file=args.candidates_file,
            opportunity_map=args.opportunity_map,
            require_opportunity_map=args.require_opportunity_map,
            allow_pending=args.allow_pending,
            no_cache=args.no_cache,
            output=args.output,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"ok": False, "blocking_reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
