"""Canonical SOLO2 field values used while generating an S11 handoff."""
from __future__ import annotations

import copy
import re


DOMAIN_SUBDIRECTIONS = {
    "安全": ["身份认证", "网络与通信安全", "系统安全", "漏洞检测与攻防", "其他"],
    "游戏、图形与多媒体": ["游戏开发", "图形渲染", "音视频处理", "图像处理", "其他"],
    "数据工程": ["数据采集、爬虫与 RPA", "数仓与数据湖", "数据分析与数据应用", "其他"],
}
TASK_TYPES = ("功能开发", "Bugfix", "代码重构", "功能优化", "依赖升级与迁移")
DIFFICULTIES = ("困难", "地狱")
OS_PLATFORM = "MacOS/Linux"
REPRO_LEVELS = (
    "无外部依赖",
    "有外部依赖，未容器化",
    "已容器化，可一键起环境",
)
VERSION_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")


def _normalize_repro(item: dict) -> dict:
    """Split descriptive environment evidence from the exact platform enum."""
    result = copy.deepcopy(item)
    value = str(result.get("repro_level") or "").strip()
    evidence = str(result.get("repro_evidence") or "").strip()
    if value in REPRO_LEVELS:
        result["repro_level"] = value
        result["repro_evidence"] = evidence
        return result
    if value.startswith("已容器化"):
        suffix = value[len("已容器化"):].lstrip("，,；;:： ")
        result["repro_level"] = "已容器化，可一键起环境"
        result["repro_evidence"] = "；".join(part for part in (evidence, suffix) if part)
        return result
    if value.startswith("有外部依赖") and "未容器化" in value:
        suffix = value.replace("有外部依赖", "", 1).replace("未容器化", "", 1).strip("，,；;:： ")
        result["repro_level"] = "有外部依赖，未容器化"
        result["repro_evidence"] = "；".join(part for part in (evidence, suffix) if part)
        return result
    if value.startswith("无外部依赖"):
        suffix = value[len("无外部依赖"):].lstrip("，,；;:： ")
        result["repro_level"] = "无外部依赖"
        result["repro_evidence"] = "；".join(part for part in (evidence, suffix) if part)
        return result
    result["repro_level"] = value
    result["repro_evidence"] = evidence
    return result


def normalize_metadata(meta: dict) -> dict:
    """Normalize safe aliases before any handoff file is written."""
    result = copy.deepcopy(meta)
    common = result.setdefault("common", {})
    common.update(_normalize_repro(common))
    common["os_platform"] = OS_PLATFORM
    for task in (result.get("tasks") or {}).values():
        if isinstance(task, dict) and (
            "repro_level" in task or "repro_evidence" in task
        ):
            normalized = _normalize_repro({**common, **task})
            task["repro_level"] = normalized["repro_level"]
            task["repro_evidence"] = normalized.get("repro_evidence", "")
    return result


def _errors_for_fields(label: str, values: dict, *, expected_domain: str, expected_subdirection: str) -> list[str]:
    errors = []
    domain = values.get("domain")
    subdirection = values.get("subdirection")
    if domain not in DOMAIN_SUBDIRECTIONS:
        errors.append(f"{label}.domain 不是平台枚举：{domain!r}；允许 {list(DOMAIN_SUBDIRECTIONS)}")
    elif subdirection not in DOMAIN_SUBDIRECTIONS[domain]:
        errors.append(
            f"{label}.subdirection 不在领域 {domain} 的选项内：{subdirection!r}；"
            f"允许 {DOMAIN_SUBDIRECTIONS[domain]}"
        )
    if domain != expected_domain or subdirection != expected_subdirection:
        errors.append(
            f"{label} 的 domain/subdirection 必须与 repo-topic-map.json 一致："
            f"{expected_domain}/{expected_subdirection}"
        )
    if values.get("repro_level") not in REPRO_LEVELS:
        errors.append(
            f"{label}.repro_level 不是平台枚举：{values.get('repro_level')!r}；"
            f"允许 {list(REPRO_LEVELS)}，说明写入 repro_evidence"
        )
    if not str(values.get("repo_id") or "").strip():
        errors.append(f"{label}.repo_id 不能为空")
    if not str(values.get("language") or "").strip():
        errors.append(f"{label}.language 不能为空")
    if not str(values.get("frameworks") or "").strip():
        errors.append(f"{label}.frameworks 不能为空")
    return errors


def validate(meta: dict, rows: dict, topic_map: dict, cfg: dict) -> tuple[list[str], dict]:
    """Validate all immutable platform fields before handoff generation."""
    normalized = normalize_metadata(meta)
    common = normalized.get("common") if isinstance(normalized.get("common"), dict) else {}
    errors = _errors_for_fields(
        "common", {**common, "os_platform": OS_PLATFORM},
        expected_domain=str(topic_map.get("domain") or ""),
        expected_subdirection=str(topic_map.get("subdirection") or ""),
    )
    if common.get("harness") not in (None, "", cfg.get("harness")):
        errors.append("common.harness 必须与真实运行 Harness 一致")
    if common.get("harness_version") not in (None, "", cfg.get("harness_version")):
        errors.append("common.harness_version 必须与真实运行 Harness 版本一致")
    for task_id, task in (normalized.get("tasks") or {}).items():
        if not isinstance(task, dict):
            errors.append(f"tasks.{task_id} 必须是对象")
            continue
        merged = {**common, **task}
        errors.extend(_errors_for_fields(
            f"tasks.{task_id}", merged,
            expected_domain=str(topic_map.get("domain") or ""),
            expected_subdirection=str(topic_map.get("subdirection") or ""),
        ))
        row = rows.get(task_id, {})
        if row.get("任务类型") not in TASK_TYPES:
            errors.append(f"tasks.{task_id} 的任务类型不在 S11 五类内：{row.get('任务类型')!r}")
        difficulty = merged.get("difficulty") or row.get("任务难度") or "困难"
        if difficulty not in DIFFICULTIES:
            errors.append(f"tasks.{task_id}.difficulty 不在平台枚举内：{difficulty!r}")
        plan = merged.get("recording_plan")
        if not isinstance(plan, dict):
            errors.append(f"tasks.{task_id}.recording_plan 必须是对象")
        elif plan.get("mode") == "web":
            pages = plan.get("pages")
            if not isinstance(pages, list) or not pages:
                errors.append(f"tasks.{task_id}.recording_plan 新 Web 方案必须声明 pages")
            else:
                page_ids = {p.get("id") for p in pages if isinstance(p, dict)}
                for step in plan.get("steps") or []:
                    if not isinstance(step, dict) or step.get("page") not in page_ids:
                        errors.append(
                            f"tasks.{task_id}.recording_plan 的每个页面步骤必须指向真实 page.id"
                        )
                        break
    return list(dict.fromkeys(errors)), normalized
