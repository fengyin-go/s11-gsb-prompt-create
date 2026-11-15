#!/usr/bin/env python3
"""Deterministic checks for the per-task domain contract.

The semantic judgement still belongs to the producing AI. These checks make the
judgement harder to fake: every task needs a source-anchored domain chain and
the final prompt must carry that input -> operation -> output chain in its main
request instead of decorating a generic engineering task with domain nouns.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


FOLDER_PLAN_RE = re.compile(r"-(0[1-9]|[1-9][0-9])-(development|bugfix|optimization|refactor|migration)$")
DOMAIN_GATE_VERSION = 2
ANCHOR_SUPPORTS = {"input", "operation", "output", "observable"}
DOMAIN_PROFILES = {
    "数据工程": {
        "artifact_terms": (
            "数据", "数据集", "数据表", "字段", "日志", "批次", "分区", "指标", "血缘", "采集",
            "管道", "ETL", "湖", "仓", "质量报告", "明细", "源记录", "事件流", "样本", "水位",
            "文件", "快照",
        ),
        "operation_terms": (
            "采集", "接入", "抓取", "爬取", "导入", "读取", "解析", "清洗", "转换", "标准化",
            "关联", "聚合", "去重", "校验", "入湖", "入库", "落库", "存储", "写入", "查询",
            "计算", "调度", "回填", "迁移", "导出", "切分", "归并", "合并", "归档", "水位",
        ),
    },
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_text(raw) for raw in value) if item]


def _list_values(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def _anchor_errors(
    repo: Path | None,
    fit: dict[str, Any],
    where: str,
    domain: str,
) -> list[str]:
    anchors = fit.get("domain_anchors")
    if not isinstance(anchors, list) or not anchors:
        return [f"{where} 的 domain_fit 缺少 domain_anchors"]
    errors: list[str] = []
    supported: set[str] = set()
    valid_paths: set[str] = set()
    for index, anchor in enumerate(anchors):
        label = f"{where} domain_anchors[{index}]"
        if not isinstance(anchor, dict):
            errors.append(f"{label} 必须是对象")
            continue
        raw_path = _text(anchor.get("path"))
        problem = _source_problem(repo, raw_path)
        if problem:
            errors.append(f"{label}：{problem}")
            continue
        path = Path(raw_path)
        try:
            start = int(anchor.get("start_line"))
            end = int(anchor.get("end_line"))
        except (TypeError, ValueError):
            errors.append(f"{label} 的 start_line/end_line 必须是整数")
            continue
        supports = _text(anchor.get("supports"))
        excerpt = _text(anchor.get("excerpt"))
        if supports not in ANCHOR_SUPPORTS:
            errors.append(f"{label} 的 supports 必须是 {'/'.join(sorted(ANCHOR_SUPPORTS))}")
        if not excerpt or len(excerpt) < 8:
            errors.append(f"{label} 的 excerpt 至少要有 8 个字符")
        if start < 1 or end < start:
            errors.append(f"{label} 的行号范围无效")
            continue
        target = Path(repo) / path
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            errors.append(f"{label} 无法读取源码：{exc}")
            continue
        if end > len(lines):
            errors.append(f"{label} 的结束行超出文件长度")
            continue
        region = "\n".join(lines[start - 1:end])
        if excerpt and excerpt not in region:
            errors.append(f"{label} 的 excerpt 不在声明的源码行范围内")
        profile = DOMAIN_PROFILES.get(domain)
        if profile and excerpt:
            if supports == "operation" and not _profile_hits(domain, [excerpt], "operation_terms"):
                errors.append(f"{label} 的源码原文没有体现{domain}核心数据操作")
            if supports in {"input", "output"} and not _profile_hits(
                domain, [excerpt], "artifact_terms"
            ):
                errors.append(f"{label} 的源码原文没有体现{domain}数据产物")
        if supports in ANCHOR_SUPPORTS:
            supported.add(supports)
        valid_paths.add(raw_path)
    if "operation" not in supported:
        errors.append(f"{where} 的 domain_anchors 必须有一条 supports=operation 的源码锚点")
    if not ({"input", "output"} & supported):
        errors.append(f"{where} 的 domain_anchors 还必须至少锚定 input 或 output")
    declared = set(_text_list(fit.get("source_evidence")))
    for path in valid_paths - declared:
        errors.append(f"{where} 的 domain_anchors 路径没有写入 source_evidence：{path}")
    return errors


def _profile_hits(domain: str, values: list[str], key: str) -> list[str]:
    profile = DOMAIN_PROFILES.get(domain, {})
    terms = profile.get(key, ())
    text = "\n".join(values)
    return [term for term in terms if term in text]


def _domain_chain_errors(topic_map: dict[str, Any], fit: dict[str, Any], where: str) -> list[str]:
    errors: list[str] = []
    try:
        fit_version = int(fit.get("version") or 0)
    except (TypeError, ValueError):
        fit_version = 0
    if fit_version != DOMAIN_GATE_VERSION:
        errors.append(f"{where} 的 domain_fit.version 必须是 {DOMAIN_GATE_VERSION}")
    chain = fit.get("domain_chain")
    if not isinstance(chain, dict):
        errors.append(f"{where} 的 domain_fit 缺少 domain_chain")
        chain = {}
    values: dict[str, str] = {
        key: _text(chain.get(key)) for key in ("input", "operation", "output", "observable")
    }
    for key, value in values.items():
        if not value:
            errors.append(f"{where} 的 domain_chain 缺少 {key}")
    main_request = _text(fit.get("prompt_main_request"))
    if not main_request:
        errors.append(f"{where} 缺少 prompt_main_request")
    elif not main_request.startswith("请"):
        errors.append(f"{where} 的 prompt_main_request 必须是明确的“请...”主动作句")
    for key in ("input", "operation", "output"):
        if values[key] and values[key] not in main_request:
            errors.append(f"{where} 的 prompt_main_request 没有逐字包含 domain_chain.{key}")
    main_task = _text(fit.get("main_task"))
    business_result = _text(fit.get("business_result"))
    if values["operation"] and values["operation"] not in main_task:
        errors.append(f"{where} 的 main_task 没有包含 domain_chain.operation")
    if values["output"] and values["output"] not in main_task:
        errors.append(f"{where} 的 main_task 没有包含 domain_chain.output")
    if values["output"] and values["output"] not in business_result:
        errors.append(f"{where} 的 business_result 没有包含 domain_chain.output")
    generic = fit.get("genericization")
    if not isinstance(generic, dict):
        errors.append(f"{where} 缺少 genericization 反事实检查")
    else:
        if not _text(generic.get("rewritten_with_generic_objects")):
            errors.append(f"{where} 的 genericization 缺少改写后的通用工程任务")
        if generic.get("same_task_without_domain_semantics") is not False:
            errors.append(
                f"{where} 的 genericization.same_task_without_domain_semantics 必须为 false；"
                "替换领域对象后仍是同一任务，说明题面借壳"
            )
    domain = _text(topic_map.get("domain"))
    if domain in DOMAIN_PROFILES:
        artifacts = _profile_hits(domain, [values["input"], values["output"]], "artifact_terms")
        operations = _profile_hits(domain, [values["operation"]], "operation_terms")
        observable_artifacts = _profile_hits(
            domain, [values["observable"]], "artifact_terms"
        )
        if not artifacts:
            errors.append(
                f"{where} 的领域输入/输出没有任何{domain}数据产物，"
                f"至少命中：{'、'.join(DOMAIN_PROFILES[domain]['artifact_terms'][:8])}"
            )
        if not operations:
            errors.append(
                f"{where} 的 domain_chain.operation 没有{domain}核心数据操作，"
                f"至少命中：{'、'.join(DOMAIN_PROFILES[domain]['operation_terms'][:8])}"
            )
        elif set(artifacts) <= {"文件", "快照"}:
            errors.append(
                f"{where} 的领域输入/输出只出现泛化的“文件/快照”，"
                "需要说明其中承载的具体数据产物"
            )
        if not observable_artifacts:
            errors.append(
                f"{where} 的 domain_chain.observable 没有可检查的{domain}数据结果"
            )
    return errors


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
        if not _text_list(topic.get("evidence")):
            errors.append(f"topic {topic.get('id')} 缺少 evidence")
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
        where = f"task_plan sequence {sequence}"
        errors.extend(_domain_chain_errors(topic_map, fit, where))
        errors.extend(_anchor_errors(repo, fit, where, _text(topic_map.get("domain"))))
        topic = topics.get(_text(plan.get("topic")))
        if topic is None:
            errors.append(f"task_plan sequence {sequence} 指向不存在的 topic")
        else:
            anchor_paths = {
                _text(anchor.get("path"))
                for anchor in _list_values(fit.get("domain_anchors"))
                if isinstance(anchor, dict)
            }
            if not anchor_paths & set(_text_list(topic.get("evidence"))):
                errors.append(
                    f"{where} 的 domain_anchors 与 topic evidence 没有交集；"
                    "不能把数据链锚在另一个模块后继续复用通用 topic"
                )
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
        fit = plan.get("domain_fit")
        if not isinstance(fit, dict):
            errors.append(f"{folder} 缺少可核对的 domain_fit")
            continue
        chain = fit.get("domain_chain")
        if not isinstance(chain, dict):
            errors.append(f"{folder} 缺少 domain_chain")
            continue
        for key in ("input", "operation", "output", "observable"):
            value = _text(chain.get(key))
            if value and value not in prompt:
                errors.append(f"{folder} 题面没有逐字保留 domain_chain.{key}：{value}")
        main_request = _text(fit.get("prompt_main_request"))
        if not main_request:
            errors.append(f"{folder} 缺少 prompt_main_request")
        elif main_request not in prompt:
            errors.append(f"{folder} 题面没有逐字使用 prompt_main_request")
        else:
            index = prompt.index(main_request)
            limit = max(80, int(len(prompt) * 0.55))
            if index > limit:
                errors.append(
                    f"{folder} 的领域主动作句出现在题面 {index}/{len(prompt)} 字符处；"
                    f"必须在前 {limit} 字符内出现，不能把领域动作放在末尾兜底"
                )
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
        "version": DOMAIN_GATE_VERSION,
        "domain": topic_map.get("domain"),
        "subdirection": topic_map.get("subdirection"),
        "topic_id": topic.get("id"),
        "topic_name": topic.get("name"),
        "main_task": fit.get("main_task"),
        "business_result": fit.get("business_result"),
        "source_evidence": _text_list(fit.get("source_evidence")),
        "domain_chain": dict(fit.get("domain_chain") or {}),
        "prompt_main_request": fit.get("prompt_main_request"),
        "domain_anchors": _list_values(fit.get("domain_anchors")),
        "genericization": dict(fit.get("genericization") or {}),
        "domain_objects": _text_list(topic.get("domain_objects")),
        "domain_results": _text_list(topic.get("domain_results")),
        "conclusion": fit.get("conclusion"),
    }


def check_batch(parent: Path, *, repo: Path | None = None, topic_map_path: str | Path | None = None) -> dict[str, Any]:
    """Run the domain gate against a complete workbook for standalone diagnostics."""
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    import batch_prompt_workbook
    from repo_topic_policy import load_topic_map

    topic_map = load_topic_map(
        repo=repo,
        parent=parent,
        explicit=topic_map_path,
        required=True,
    )
    rows = batch_prompt_workbook.read_workbook(parent / "tasks.xlsx")
    errors = map_errors(topic_map, repo)
    errors.extend(prompt_errors(rows, topic_map))
    return {
        "ok": not errors,
        "domain_gate_version": DOMAIN_GATE_VERSION,
        "domain": topic_map.get("domain"),
        "subdirection": topic_map.get("subdirection"),
        "checked_prompts": sum(bool(str(row.get("提示词") or "").strip()) for row in rows),
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="检查 S11 批次的数据链路与领域主任务契约")
    parser.add_argument("--parent", required=True, help="批次目录")
    parser.add_argument("--repo", help="当前基准仓库或任一任务 origin")
    parser.add_argument("--map", help="repo-topic-map.json；默认从 parent/repo 查找")
    args = parser.parse_args()
    result = check_batch(
        Path(args.parent).expanduser().resolve(),
        repo=Path(args.repo).expanduser().resolve() if args.repo else None,
        topic_map_path=args.map,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
