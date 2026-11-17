#!/usr/bin/env python3
"""Validate the structured H1/H2/H3 contract for a prompt plan.

The canonical G16/G19 judge remains the source of truth for difficulty labels.
This module closes the process gap exposed by the 2026-11-15 batch: 13 prompts
were admitted through one generic "难度复核" note and later discarded by the
platform because the prompt itself only described medium-sized work.

The contract does not replace semantic review. It makes the review auditable by
requiring a source-anchored reasoning challenge, a real data-flow module
integration, an open design tradeoff, and prompt excerpts that prove those
decisions reached the final prompt. Version 3 uses the 2026-11-16 platform
calibration:

- dfzr10012-14-bugfix passed P5 with a real completion-precedence decision;
- dfzr10012-10-development and dfzr10012-11-bugfix were discarded with H1/H2
  present but H3 absent, because the prompt already selected the winning branch.

The tradeoff check therefore verifies that every option is independently valid
under all H1 constraints, that options have distinct correctness effects, and
that the final prompt does not already resolve the same dispute. New prompts
express that axis through business conditions, not through instructions asking
the implementer to choose.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


CONTRACT_VERSION = 3
WORKLOAD_CONTRACT_VERSION = 1
MIN_EXPECTED_EFFECTIVE_TOOL_CALLS = 55
MIN_WORKLOAD_SLICES = 4
MIN_VERIFICATION_PATHS = 2
FOLDER_PLAN_RE = re.compile(
    r"-(0[1-9]|[1-9][0-9])-(development|bugfix|optimization|refactor|migration)$"
)
CONSTRAINT_KINDS = {
    "concurrency",
    "idempotency",
    "state_transition",
    "transaction_consistency",
    "failure_recovery",
    "versioning",
    "migration",
    "permission_ownership",
    "dependency_order",
    "conflict_resolution",
    "historical_compatibility",
}
SECONDARY_ROLES = {
    "consumer",
    "writer",
    "downstream",
    "audit",
    "persistence",
    "orchestrator",
    "notification",
    "authorization",
}
ANTI_PATTERNS = (
    "single_surface_only",
    "secondary_observation_only",
    "solution_prescribed",
    "single_constraint",
    "hard_evidence_tail_only",
    "example_data_only",
    "cross_call_merge_only",
)
DECISION_AXES = {
    "source_of_truth",
    "effective_time",
    "completion_precedence",
    "version_adoption",
    "retention_vs_reclaim",
    "recovery_vs_cleanup",
    "old_vs_new_priority",
    "authority_conflict",
    "scope_boundary",
    "partial_success_boundary",
    "concurrency_conflict",
    "migration_switch",
    "other",
}
H3_FALSE_GUARDS = {
    "one_branch_violates_constraints",
    "fixed_resolution_in_prompt",
    "implementation_only",
    "retry_or_error_message_only",
    "display_only",
    "single_result_invariant_only",
    "source_selection_only",
    "equivalent_user_outcome",
}
CONTEXT_ANCHOR_SUPPORTS = {"trigger", "option_a", "option_b", "invariant"}
OPEN_CHOICE_RE = re.compile(
    r"(?:"
    r"(?:由你|由设计者|请你|请自行|自行|自己)[^。；]{0,12}"
    r"(?:判断|决定|选择|权衡|取舍|确定|设计)"
    r"|(?:需要|请)[^。；]{0,8}(?:明确|确定)[^。；]{0,18}"
    r"(?:哪一版|哪一份|哪份|谁优先|优先级|采用)"
    r"|(?:优先级|采用哪份|采用哪一版|谁先采用|谁优先)[^。；]{0,12}"
    r"(?:由你|自行|自己|设计者)"
    r")"
)
CLOSED_RESOLUTION_RE = re.compile(
    r"(?:冲突|先后)[^。；]{0,40}(?:以|按)[^。；]{0,40}"
    r"(?:为准|为依据|作为[^。；]{0,12}依据|优先)"
)
FIXED_BRANCH_RE = re.compile(
    r"(?:只能|必须)[^。；]{0,24}(?:读取|采用|使用|保留|切换到|沿用)[^。；]{0,18}"
    r"(?:刚启用|当前版本|最新版本|旧版本|历史版本|原值|旧缓存|受保护|可恢复)"
    r"|不能(?:把|将|再用|继续用)?[^。；]{0,18}(?:旧|原值|历史)"
    r"[^。；]{0,16}(?:写回|覆盖|恢复|采用)"
    r"|(?:旧|历史|原值)[^。；]{0,12}(?:不得|不能|不允许)"
    r"[^。；]{0,12}(?:写回|覆盖|恢复|采用)"
    r"|(?:冲突|差异)[^。；]{0,32}(?:只能)[^。；]{0,50}"
    r"(?:不能|不得)[^。；]{0,18}(?:删除|清理|覆盖|恢复|写回)"
    r"|(?:只能|必须)[^。；]{0,80}(?:不能|不得)[^。；]{0,20}"
    r"(?:删除|清理|覆盖|写回)"
)
BUSINESS_TENSION_RE = re.compile(
    r"冲突|不同[^。；]{0,12}(?:口径|版本|批次|来源)|同一[^。；]{0,24}(?:多|两|重复)"
    r"|多个[^。；]{0,20}(?:来源|规则|口径)|迟到|更正|补报|部分[^。；]{0,12}失败"
    r"|(?:冻结|历史)[^。；]{0,30}(?:更新|变化|重算|修订)"
    r"|周期[^。；]{0,20}(?:不同|只在)"
)


def _has_tradeoff_evidence(excerpt: str, context: str) -> bool:
    # Keep old lexical calibration readable; prompt_style rejects delegation in
    # new outputs. A natural excerpt must also share the actual context object.
    if _fixed_branch_evidence(excerpt):
        return False
    if OPEN_CHOICE_RE.search(excerpt):
        return True
    return bool(
        len(excerpt) >= 12
        and BUSINESS_TENSION_RE.search(excerpt)
        and not CLOSED_RESOLUTION_RE.search(excerpt)
        and (_cjk_trigrams(excerpt) & _cjk_trigrams(context)) - CHOICE_GENERIC_GRAMS
    )


def _fixed_branch_evidence(text: str) -> list[str]:
    """Return clauses that already select one branch of the H3 decision.

    A shared invariant such as “不能同时释放路径和准备恢复” constrains all
    branches equally. “不能把旧配置写回” or “必须读取刚启用的配置” instead
    selects the winner and is not an open H3 choice.
    """

    hits: list[str] = []
    for sentence in _sentences(text):
        if "不能既" in sentence or "不得既" in sentence:
            continue
        if FIXED_BRANCH_RE.search(sentence):
            hits.append(sentence)
    return hits
CHOICE_GENERIC_GRAMS = {
    "冲突时",
    "优先级",
    "由你判",
    "由你决",
    "由你权",
    "由你选",
    "采用哪",
    "哪一份",
    "哪一版",
}
PRESCRIPTION_MARKERS = (
    "第一步",
    "第二步",
    "第三步",
    "先",
    "再",
    "然后",
    "接着",
    "逐项",
    "依次",
    "按顺序",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_text(raw) for raw in value) if item]


def _sentences(text: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"[。；！？!?\n]+", str(text or ""))
        if part.strip()
    ]


def _cjk_trigrams(text: str) -> set[str]:
    compact = re.sub(r"[^\u4e00-\u9fff]", "", text)
    return {compact[index:index + 3] for index in range(max(0, len(compact) - 2))}


def _longest_common_cjk(left: str, right: str) -> str:
    left_text = re.sub(r"[^\u4e00-\u9fff]", "", left or "")
    right_text = re.sub(r"[^\u4e00-\u9fff]", "", right or "")
    if not left_text or not right_text:
        return ""
    previous = [0] * (len(right_text) + 1)
    best_end = 0
    best_length = 0
    for left_index, left_char in enumerate(left_text, start=1):
        current = [0] * (len(right_text) + 1)
        for right_index, right_char in enumerate(right_text, start=1):
            if left_char != right_char:
                continue
            current[right_index] = previous[right_index - 1] + 1
            if current[right_index] > best_length:
                best_length = current[right_index]
                best_end = left_index
        previous = current
    return left_text[best_end - best_length:best_end]


def _trigram_similarity(left: str, right: str) -> float:
    left_grams = _cjk_trigrams(left)
    right_grams = _cjk_trigrams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def _equivalent_option_outcome(left: str, right: str) -> tuple[bool, str]:
    """Detect two H3 options that only rename the source or mechanism."""
    common = _longest_common_cjk(left, right)
    similarity = _trigram_similarity(left, right)
    return (
        len(common) >= 16 or similarity >= 0.72,
        common or f"相似度 {similarity:.2f}",
    )


def _tradeoff_conflict_errors(prompt: str, excerpt: str) -> list[str]:
    """Reject a fake open choice that another sentence has already decided.

    SOLO2 record 6463/cc10010-14 passed the lexical H1/H2 checks but was
    discarded because one sentence fixed checkpoint precedence while another
    asked the implementer to choose it. The shared business term (for example
    ``检查点``) identifies that contradiction. A fixed rule about a different
    object remains valid, so overlap must be on a meaningful three-character
    term rather than the generic words ``冲突`` or ``优先级``.
    """
    if not prompt or not excerpt:
        return []
    open_sentences = [sentence for sentence in _sentences(prompt) if excerpt in sentence]
    if not open_sentences:
        return []
    closed_sentences = [
        sentence for sentence in _sentences(prompt)
        if CLOSED_RESOLUTION_RE.search(sentence)
    ]
    for open_sentence in open_sentences:
        for closed_sentence in closed_sentences:
            if closed_sentence == open_sentence:
                continue
            overlap = (
                _cjk_trigrams(open_sentence)
                & _cjk_trigrams(closed_sentence)
            ) - CHOICE_GENERIC_GRAMS
            if overlap:
                term = sorted(overlap, key=lambda item: (-len(item), item))[0]
                return [
                    "design_tradeoff 的同一业务对象既要求设计者裁决，"
                    f"又在另一句被固定为一种结果（共同对象：{term}）；"
                    "平台会把这种题视为实现已写死，请删除固定裁决或更换取舍轴"
                ]
    return []


def _sequence_for_folder(folder: str) -> int | None:
    match = FOLDER_PLAN_RE.search(folder or "")
    return int(match.group(1)) if match else None


def _source_region(repo: Path | None, anchor: dict[str, Any], label: str) -> tuple[list[str], str]:
    errors: list[str] = []
    if repo is None:
        return errors, ""
    raw_path = _text(anchor.get("path"))
    path = Path(raw_path)
    if not raw_path or path.is_absolute() or ".." in path.parts:
        return [f"{label} 的 path 不安全"], ""
    target = Path(repo) / path
    if not target.is_file() or target.is_symlink():
        return [f"{label} 的源码文件不存在或为符号链接：{raw_path}"], ""
    try:
        start = int(anchor.get("start_line"))
        end = int(anchor.get("end_line"))
    except (TypeError, ValueError):
        return [f"{label} 的 start_line/end_line 必须是整数"], ""
    if start < 1 or end < start:
        return [f"{label} 的行号范围无效"], ""
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    if end > len(lines):
        return [f"{label} 的结束行超出文件长度"], ""
    excerpt = _text(anchor.get("excerpt"))
    if len(excerpt) < 8:
        errors.append(f"{label} 的 excerpt 至少要有 8 个字符")
    elif excerpt not in "\n".join(lines[start - 1:end]):
        errors.append(f"{label} 的 excerpt 不在声明源码行内")
    return errors, "\n".join(lines[start - 1:end])


def _hard_evidence_keys() -> set[str]:
    from check_prompt_g16 import HARD_EVIDENCE_KEYS

    return set(HARD_EVIDENCE_KEYS)


def _design_errors(
    design: Any,
    *,
    where: str,
    repo: Path | None,
    prompt: str = "",
) -> list[str]:
    errors: list[str] = []
    if not isinstance(design, dict):
        return [f"{where} 缺少 difficulty_design"]
    try:
        version = int(design.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version != CONTRACT_VERSION:
        errors.append(f"{where} 的 difficulty_design.version 必须是 {CONTRACT_VERSION}")

    reasoning = design.get("reasoning_challenge")
    if not isinstance(reasoning, dict):
        errors.append(f"{where} 缺少 reasoning_challenge")
        reasoning = {}
    constraints = reasoning.get("constraints")
    if not isinstance(constraints, list) or len(constraints) < 2:
        errors.append(f"{where} 的 reasoning_challenge.constraints 至少要有两条硬约束")
        constraints = []
    kinds: list[str] = []
    constraint_ids: list[str] = []
    for index, item in enumerate(constraints):
        label = f"{where} reasoning_challenge.constraints[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} 必须是对象")
            continue
        constraint_id = _text(item.get("id"))
        if not re.fullmatch(r"C[1-9][0-9]*", constraint_id):
            errors.append(f"{label}.id 必须是 C1、C2 这类稳定编号")
        elif constraint_id in constraint_ids:
            errors.append(f"{label}.id 重复：{constraint_id}")
        else:
            constraint_ids.append(constraint_id)
        kind = _text(item.get("kind"))
        if kind not in CONSTRAINT_KINDS:
            errors.append(f"{label}.kind 不合法：{kind or '空'}")
        else:
            kinds.append(kind)
        for key in ("statement", "failure_if_omitted"):
            if len(_text(item.get(key))) < 8:
                errors.append(f"{label}.{key} 至少要有 8 个字符")
    if len(set(kinds)) < 2:
        errors.append(f"{where} 的 reasoning_challenge 至少要有两种不同约束类型")
    if len(constraint_ids) < 2:
        errors.append(f"{where} 的 reasoning_challenge 至少要有两个唯一约束编号")
    if len(_text(reasoning.get("why_interact"))) < 12:
        errors.append(f"{where} 的 reasoning_challenge.why_interact 必须说明约束如何相互作用")
    if len(_text(reasoning.get("prompt_excerpt"))) < 8:
        errors.append(f"{where} 的 reasoning_challenge.prompt_excerpt 缺失")

    integration = design.get("module_integration")
    if not isinstance(integration, dict):
        errors.append(f"{where} 缺少 module_integration")
        integration = {}
    primary = _text(integration.get("primary_module"))
    if len(primary) < 2:
        errors.append(f"{where} 的 module_integration.primary_module 缺失")
    secondary = integration.get("secondary_modules")
    if not isinstance(secondary, list) or not secondary:
        errors.append(f"{where} 的 module_integration.secondary_modules 至少要有 1 个")
        secondary = []
    secondary_names: list[str] = []
    for index, item in enumerate(secondary):
        label = f"{where} module_integration.secondary_modules[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} 必须是对象")
            continue
        name = _text(item.get("name"))
        role = _text(item.get("role"))
        if len(name) < 2:
            errors.append(f"{label}.name 缺失")
        else:
            secondary_names.append(name)
        if role not in SECONDARY_ROLES:
            errors.append(
                f"{label}.role 必须是真实数据流角色：{'/'.join(sorted(SECONDARY_ROLES))}"
            )
        if len(_text(item.get("artifact"))) < 4:
            errors.append(f"{label}.artifact 至少要有 4 个字符")
        if len(_text(item.get("relationship"))) < 8:
            errors.append(f"{label}.relationship 至少要有 8 个字符")
    if len(_text(integration.get("data_artifact"))) < 4:
        errors.append(f"{where} 的 module_integration.data_artifact 缺失")
    if len(_text(integration.get("prompt_excerpt"))) < 8:
        errors.append(f"{where} 的 module_integration.prompt_excerpt 缺失")

    workload = design.get("workload_design")
    if not isinstance(workload, dict):
        errors.append(f"{where} 缺少 workload_design；P6 低调用量题不能进入正式交接")
        workload = {}
    if workload.get("version") != WORKLOAD_CONTRACT_VERSION:
        errors.append(
            f"{where} 的 workload_design.version 必须是 {WORKLOAD_CONTRACT_VERSION}"
        )
    expected_calls = workload.get("expected_effective_tool_calls")
    if (
        type(expected_calls) is not int
        or expected_calls < MIN_EXPECTED_EFFECTIVE_TOOL_CALLS
    ):
        errors.append(
            f"{where} 的 workload_design.expected_effective_tool_calls "
            f"必须为不小于 {MIN_EXPECTED_EFFECTIVE_TOOL_CALLS} 的整数"
        )
    if len(_text(workload.get("call_floor_rationale"))) < 12:
        errors.append(
            f"{where} 的 workload_design.call_floor_rationale "
            "必须说明为什么至少需要这些工具调用，不能只填数字"
        )
    slices = workload.get("work_slices")
    if not isinstance(slices, list) or len(slices) < MIN_WORKLOAD_SLICES:
        errors.append(
            f"{where} 的 workload_design.work_slices "
            f"至少要有 {MIN_WORKLOAD_SLICES} 个工作切片"
        )
        slices = []
    slice_modules: set[str] = set()
    slice_results: set[str] = set()
    slice_verifications: set[str] = set()
    for index, item in enumerate(slices):
        label = f"{where} workload_design.work_slices[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} 必须是对象")
            continue
        module = _text(item.get("module"))
        data_result = _text(item.get("data_result"))
        verification = _text(item.get("verification"))
        if len(module) < 2:
            errors.append(f"{label}.module 缺失")
        for key, value in (
            ("implementation", _text(item.get("implementation"))),
            ("data_result", data_result),
            ("verification", verification),
        ):
            if len(value) < 8:
                errors.append(f"{label}.{key} 至少要有 8 个字符")
        if module:
            slice_modules.add(module)
        if data_result:
            slice_results.add(data_result)
        if verification:
            slice_verifications.add(verification)
    if len(slice_modules) < 2:
        errors.append(f"{where} 的 workload_design.work_slices 必须覆盖至少两个模块")
    if len(slice_results) < len(slices):
        errors.append(f"{where} 的 workload_design.work_slices 必须产生不同的数据结果")
    if len(slice_verifications) < len(slices):
        errors.append(f"{where} 的 workload_design.work_slices 必须分别写独立验证")
    verification_paths = _text_list(workload.get("verification_paths"))
    if len(set(verification_paths)) < MIN_VERIFICATION_PATHS:
        errors.append(
            f"{where} 的 workload_design.verification_paths "
            f"至少要有 {MIN_VERIFICATION_PATHS} 条不同的真实验证路径"
        )
    for index, path in enumerate(verification_paths):
        if len(path) < 8:
            errors.append(
                f"{where} workload_design.verification_paths[{index}] "
                "至少要有 8 个字符"
            )

    tradeoff = design.get("design_tradeoff")
    if not isinstance(tradeoff, dict):
        errors.append(f"{where} 缺少 design_tradeoff")
        tradeoff = {}
    if len(_text(tradeoff.get("decision"))) < 8:
        errors.append(f"{where} 的 design_tradeoff.decision 至少要有 8 个字符")
    decision_axis = _text(tradeoff.get("decision_axis"))
    if decision_axis not in DECISION_AXES:
        errors.append(
            f"{where} 的 design_tradeoff.decision_axis 不合法："
            f"{decision_axis or '空'}"
        )
    trigger = _text(tradeoff.get("trigger"))
    if len(trigger) < 12:
        errors.append(f"{where} 的 design_tradeoff.trigger 必须写清冲突触发条件")
    options = _text_list(tradeoff.get("options"))
    if len(set(options)) < 2:
        errors.append(f"{where} 的 design_tradeoff.options 至少要有两个不同方案")
    if len(_text(tradeoff.get("outcome_invariant"))) < 8:
        errors.append(f"{where} 的 design_tradeoff.outcome_invariant 缺失")
    if len(_text(tradeoff.get("why_open"))) < 12:
        errors.append(f"{where} 的 design_tradeoff.why_open 必须说明为何没有写死实现")
    # Freedom to choose implementation is not evidence of a consequential tradeoff.
    context_excerpt = _text(tradeoff.get("context_excerpt"))
    if len(context_excerpt) < 12:
        errors.append(f"{where} 的 design_tradeoff.context_excerpt 必须摘录触发取舍的真实业务条件")
    elif prompt and context_excerpt not in prompt:
        errors.append(f"{where} 的 design_tradeoff.context_excerpt 不在最终题面中")
    if trigger and context_excerpt and trigger not in context_excerpt:
        errors.append(f"{where} 的 design_tradeoff.trigger 必须逐字进入 context_excerpt")
    context_anchors = tradeoff.get("context_anchors")
    if not isinstance(context_anchors, list) or len(context_anchors) < 2:
        errors.append(
            f"{where} 的 design_tradeoff.context_anchors 至少要有两条源码依据，"
            "分别支撑触发条件和至少一个方案边界"
        )
    else:
        anchor_supports: set[str] = set()
        for index, anchor in enumerate(context_anchors):
            label = f"{where} design_tradeoff.context_anchors[{index}]"
            if not isinstance(anchor, dict):
                errors.append(f"{label} 必须是源码锚点对象")
                continue
            supports = _text(anchor.get("supports"))
            if supports not in CONTEXT_ANCHOR_SUPPORTS:
                errors.append(
                    f"{label}.supports 必须是 "
                    f"{'/'.join(sorted(CONTEXT_ANCHOR_SUPPORTS))}"
                )
            else:
                anchor_supports.add(supports)
            if repo is None:
                errors.append(f"{label} 缺少仓库，无法核对取舍条件")
                continue
            anchor_errors, _ = _source_region(repo, anchor, label)
            errors.extend(anchor_errors)
        if "trigger" not in anchor_supports:
            errors.append(f"{where} 的 context_anchors 至少一条 supports=trigger")
        if not ({"option_a", "option_b"} & anchor_supports):
            errors.append(
                f"{where} 的 context_anchors 至少一条支撑 option_a 或 option_b"
            )
    analysis = tradeoff.get("option_analysis")
    if not isinstance(analysis, list):
        analysis = []
    analyzed: set[str] = set()
    correctness_effects: list[str] = []
    observable_results: list[str] = []
    for index, item in enumerate(analysis):
        label = f"{where} design_tradeoff.option_analysis[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} 必须是对象")
            continue
        option = _text(item.get("option"))
        if option not in options or option in analyzed:
            errors.append(f"{label}.option 必须唯一对应 options 中的真实方案")
        analyzed.add(option)
        for key in ("benefit", "cost", "viable_when", "correctness_effect", "observable_result"):
            if len(_text(item.get(key))) < 12:
                errors.append(
                    f"{label}.{key} 必须写清方案在该冲突下实际会发生什么，"
                    "不能只写收益、重试或结果不变量"
                )
        preserved = set(_text_list(item.get("preserves_constraint_ids")))
        missing_constraints = set(constraint_ids) - preserved
        unknown_constraints = preserved - set(constraint_ids)
        if missing_constraints:
            errors.append(
                f"{label}.preserves_constraint_ids 没有覆盖全部 H1 约束："
                f"{'、'.join(sorted(missing_constraints))}"
            )
        if unknown_constraints:
            errors.append(
                f"{label}.preserves_constraint_ids 引用了不存在的约束："
                f"{'、'.join(sorted(unknown_constraints))}"
            )
        correctness_effect = _text(item.get("correctness_effect"))
        observable_result = _text(item.get("observable_result"))
        if correctness_effect:
            correctness_effects.append(correctness_effect)
        if observable_result:
            observable_results.append(observable_result)
    if set(options) - analyzed or len(analyzed) < 2:
        errors.append(f"{where} 的 design_tradeoff.option_analysis 必须覆盖至少两个方案的实际代价")
    if len(set(correctness_effects)) < len(options):
        errors.append(f"{where} 的每个 H3 方案必须产生不同的正确性影响")
    if len(set(observable_results)) < len(options):
        errors.append(f"{where} 的每个 H3 方案必须有不同的可观察结果")
    for left, right in itertools.combinations(analysis, 2):
        if not isinstance(left, dict) or not isinstance(right, dict):
            continue
        left_option = _text(left.get("option"))
        right_option = _text(right.get("option"))
        left_effect = _text(left.get("correctness_effect"))
        right_effect = _text(right.get("correctness_effect"))
        left_result = _text(left.get("observable_result"))
        right_result = _text(right.get("observable_result"))
        effect_same, effect_evidence = _equivalent_option_outcome(
            left_effect, right_effect
        )
        result_same, result_evidence = _equivalent_option_outcome(
            left_result, right_result
        )
        if effect_same or result_same:
            evidence = effect_evidence if effect_same else result_evidence
            errors.append(
                f"{where} 的 H3 方案「{left_option}」与「{right_option}」"
                f"只是来源或机制不同，正确性影响/用户结果近似相同（{evidence}）；"
                "两方案必须改变不同的数据状态、版本边界或下游产物"
            )
    for key in (
        "both_options_valid",
        "choice_changes_correctness",
        "prompt_keeps_choice_open",
        "fixed_branch_excluded",
    ):
        if tradeoff.get(key) is not True:
            errors.append(f"{where} 的 design_tradeoff.{key} 必须为 true")
    false_guards = tradeoff.get("false_h3_guards")
    if not isinstance(false_guards, dict):
        errors.append(f"{where} 缺少 design_tradeoff.false_h3_guards")
        false_guards = {}
    for name in H3_FALSE_GUARDS:
        if false_guards.get(name) is not False:
            errors.append(
                f"{where} 的 design_tradeoff.false_h3_guards.{name} 必须为 false"
            )
    if len(_text(tradeoff.get("false_h3_counterexample"))) < 12:
        errors.append(
            f"{where} 的 false_h3_counterexample 必须写明一个看似取舍但已提前裁决的反例"
        )
    if len(_text(tradeoff.get("decision_impact"))) < 12:
        errors.append(f"{where} 的 design_tradeoff.decision_impact 必须说明选择如何影响数据语义或处理链路")
    if tradeoff.get("implementation_not_prescribed") is not True:
        errors.append(f"{where} 的 design_tradeoff.implementation_not_prescribed 必须为 true")
    tradeoff_excerpt = _text(tradeoff.get("prompt_excerpt"))
    if len(tradeoff_excerpt) < 8:
        errors.append(f"{where} 的 design_tradeoff.prompt_excerpt 缺失")
    elif fixed_hits := _fixed_branch_evidence(tradeoff_excerpt):
        errors.append(
            f"{where} 的 design_tradeoff.prompt_excerpt 已经预先选定分支："
            f"{fixed_hits[0]}；H3 只能固定共同不变量，不能固定由哪一方案获胜"
        )
    elif not _has_tradeoff_evidence(tradeoff_excerpt, context_excerpt):
        errors.append(
            f"{where} 的 design_tradeoff.prompt_excerpt 必须留下真实选择权："
            "通过真实业务条件体现未预定的冲突轴，并与 context_excerpt 对应；"
            "只写结果不变量、兼容要求或固定“以谁为准”不构成 H3，"
            "也不要追加由你决定之类的委托句"
        )

    counterexample = design.get("medium_counterexample")
    if not isinstance(counterexample, dict):
        errors.append(f"{where} 缺少 medium_counterexample")
    else:
        for key in ("generic_task", "why_medium"):
            if len(_text(counterexample.get(key))) < 12:
                errors.append(f"{where} medium_counterexample.{key} 至少要有 12 个字符")

    evidence = _text_list(design.get("hard_evidence"))
    allowed_evidence = _hard_evidence_keys()
    if not evidence:
        errors.append(f"{where} 的 hard_evidence 至少命中一项")
    for item in evidence:
        if item not in allowed_evidence:
            errors.append(f"{where} 的 hard_evidence 不在统一判据内：{item}")

    anti = design.get("anti_patterns")
    if not isinstance(anti, dict):
        errors.append(f"{where} 缺少 anti_patterns")
        anti = {}
    for name in ANTI_PATTERNS:
        if anti.get(name) is not False:
            errors.append(f"{where} 的 anti_patterns.{name} 必须为 false")

    anchors = design.get("module_anchors")
    if not isinstance(anchors, list) or len(anchors) < 2:
        errors.append(f"{where} 的 module_anchors 至少要有两条跨模块源码锚点")
        anchors = []
    anchor_modules: list[str] = []
    anchor_paths: list[str] = []
    for index, anchor in enumerate(anchors):
        label = f"{where} module_anchors[{index}]"
        if not isinstance(anchor, dict):
            errors.append(f"{label} 必须是对象")
            continue
        module = _text(anchor.get("module"))
        supports = _text(anchor.get("supports"))
        if len(module) < 2:
            errors.append(f"{label}.module 缺失")
        else:
            anchor_modules.append(module)
        if supports not in {"primary", "secondary", "data_flow"}:
            errors.append(f"{label}.supports 必须是 primary/secondary/data_flow")
        raw_path = _text(anchor.get("path"))
        if raw_path:
            anchor_paths.append(raw_path)
        errors.extend(_source_region(repo, anchor, label)[0])
    if len(set(anchor_modules)) < 2:
        errors.append(f"{where} 的 module_anchors 必须覆盖至少两个不同模块")
    if len(set(anchor_paths)) < 2:
        errors.append(f"{where} 的 module_anchors 必须引用至少两个不同源码文件")
    if primary and anchor_modules and primary not in anchor_modules:
        errors.append(f"{where} 的 module_anchors 没有覆盖 primary_module")
    if secondary_names and not (set(secondary_names) & set(anchor_modules)):
        errors.append(f"{where} 的 module_anchors 没有覆盖任何 secondary_module")

    if prompt:
        excerpts = {
            "reasoning_challenge": _text(reasoning.get("prompt_excerpt")),
            "module_integration": _text(integration.get("prompt_excerpt")),
            "design_tradeoff": _text(tradeoff.get("prompt_excerpt")),
        }
        for key, excerpt in excerpts.items():
            if excerpt and excerpt not in prompt:
                errors.append(f"{where}.{key}.prompt_excerpt 不在最终题面中")
        artifact = _text(integration.get("data_artifact"))
        module_excerpt = excerpts["module_integration"]
        if artifact and module_excerpt and artifact not in module_excerpt:
            errors.append(f"{where} 的第二模块题面摘录没有包含数据产物：{artifact}")
        decision_excerpt = excerpts["design_tradeoff"]
        if decision_excerpt:
            errors.extend(_tradeoff_conflict_errors(prompt, decision_excerpt))
        prescription_count = sum(prompt.count(marker) for marker in PRESCRIPTION_MARKERS)
        if prescription_count >= 4 and not _has_tradeoff_evidence(decision_excerpt, context_excerpt):
            errors.append(
                f"{where} 的题面步骤写得太死且没有留下实现取舍；"
                "减少先/再/逐项等过程指令，改为写结果不变量和可选裁决轴"
            )
    return errors


def map_errors(topic_map: dict[str, Any], repo: Path | None) -> list[str]:
    errors: list[str] = []
    try:
        version = int(topic_map.get("difficulty_contract_version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version != CONTRACT_VERSION:
        errors.append(f"topic map.difficulty_contract_version 必须是 {CONTRACT_VERSION}")
    plans = topic_map.get("task_plan") or []
    if not isinstance(plans, list):
        return errors + ["topic map.task_plan 必须是数组"]
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        sequence = plan.get("sequence")
        errors.extend(
            _design_errors(
                plan.get("difficulty_design"),
                where=f"task_plan sequence {sequence}",
                repo=repo,
            )
        )
    return errors


def prompt_errors(
    records: list[dict[str, Any]],
    topic_map: dict[str, Any],
    repo: Path | None,
) -> list[str]:
    errors: list[str] = []
    try:
        version = int(topic_map.get("difficulty_contract_version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version != CONTRACT_VERSION:
        errors.append(
            f"topic map.difficulty_contract_version 必须是 {CONTRACT_VERSION}"
        )
    plans = {
        int(plan.get("sequence")): plan
        for plan in topic_map.get("task_plan") or []
        if isinstance(plan, dict) and str(plan.get("sequence") or "").isdigit()
    }
    seen: set[int] = set()
    for record in records:
        prompt = _text(record.get("提示词"))
        if not prompt:
            continue
        folder = _text(record.get("子文件夹名称"))
        sequence = _sequence_for_folder(folder)
        if sequence is None or sequence not in plans:
            errors.append(f"{folder or '未命名'} 找不到对应的 task_plan sequence")
            continue
        seen.add(sequence)
        plan = plans[sequence]
        if _text(record.get("任务类型")) != _text(plan.get("task_type")):
            errors.append(f"{folder} 的任务类型与 task_plan sequence {sequence} 不一致")
        errors.extend(
            _design_errors(
                plan.get("difficulty_design"),
                where=f"{folder} (sequence {sequence})",
                repo=repo,
                prompt=prompt,
            )
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", help="批次目录，读取 repo-topic-map.json 和 tasks.xlsx")
    parser.add_argument("--map", help="显式 repo-topic-map.json")
    parser.add_argument("--repo", help="源码仓库")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    from repo_topic_policy import load_topic_map
    from batch_prompt_workbook import read_workbook

    parent = Path(args.parent).expanduser().resolve() if args.parent else None
    repo = Path(args.repo).expanduser().resolve() if args.repo else None
    if parent and repo is None:
        candidates = sorted(path for path in parent.iterdir() if (path / "origin").is_dir())
        repo = candidates[0] / "origin" if candidates else None
    topic_map = load_topic_map(
        parent=parent,
        repo=repo,
        explicit=args.map,
        required=True,
    )
    errors = map_errors(topic_map or {}, repo)
    if parent and (parent / "tasks.xlsx").is_file():
        errors += prompt_errors(read_workbook(parent / "tasks.xlsx"), topic_map or {}, repo)
    result = {
        "ok": not errors,
        "version": CONTRACT_VERSION,
        "task_count": len((topic_map or {}).get("task_plan") or []),
        "error_count": len(errors),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
