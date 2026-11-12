#!/usr/bin/env python3
"""Check GSB prompt similarity across sibling project batches.

The normal solo-create gates only compare prompts inside one batch (plus same-project
history). GSB batches are often generated under one parent directory with the same
task-type and demand-mode sequence, so a second batch can reuse the first batch's
sentence skeleton while only replacing business nouns. This gate treats all sibling
batches as one comparison corpus and blocks that case before workbook write-back.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from solo_paths import solo_create_scripts

SOLO_CREATE_SCRIPTS = solo_create_scripts("batch_prompt_workbook.py")
if str(SOLO_CREATE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SOLO_CREATE_SCRIPTS))

import batch_prompt_workbook as workbook_lib  # noqa: E402
import check_prompt_dedup as dedup_lib  # noqa: E402
import check_repo_theme as theme_lib  # noqa: E402
import prompt_cross_project_registry as registry_lib  # noqa: E402


DEFAULT_WORKBOOK = "tasks.xlsx"
DEFAULT_REGISTRY = SCRIPT_DIR.parent / "data" / "cross-project-registry.json"
DEFAULT_PLATFORM_CORPUS = SCRIPT_DIR.parent / "data" / "platform-prompt-corpus.json"
DEFAULT_MAX_TEXT_OVERLAP = 0.20
DEFAULT_MAX_SEMANTIC_SIMILARITY = 0.35
DEFAULT_LONG_GRAM_SIZE = 8
DEFAULT_MAX_COMMON_LONG_GRAMS = 2
DEFAULT_MAX_COMMON_FRAGMENT_LENGTH = 20
DEFAULT_OPENING_LENGTH = 24
DEFAULT_MAX_OPENING_PREFIX = 12
DEFAULT_UNRELATED_TEXT_OVERLAP = 0.60
DEFAULT_UNRELATED_SEMANTIC_SIMILARITY = 0.70
DEFAULT_UNRELATED_FRAGMENT_LENGTH = 40

# 平台历史题面语料比本地注册表覆盖面大：2026-10-05/06 的 A-5 题面重复在平台侧只报
# 9.2%~13.4%，本地提交侧 18% 阈值会漏。出题阶段改用同源的 SequenceMatcher 口径，
# 并把长公共片段与字符比例都作为硬条件。
DEFAULT_PLATFORM_MAX_SIMILARITY = 0.13
DEFAULT_PLATFORM_MAX_COMMON_FRAGMENT = 30
DEFAULT_PLATFORM_MIN_COMMON_HAN = 12
DEFAULT_PLATFORM_CORPUS_MAX_AGE_HOURS = 168
FORBIDDEN_TOPIC_TERMS = (
    ("待办清单", "常见小应用", "平台雷同题库已多次按「常见小应用」拒收该题眼，改写措辞无效"),
)


def platform_corpus_path() -> Path | None:
    raw = os.environ.get("GSB_PLATFORM_PROMPT_CORPUS")
    if raw is not None:
        value = raw.strip()
        if value.lower() in {"", "0", "off", "none", "false", "no"}:
            return None
        return Path(value).expanduser().resolve()
    return DEFAULT_PLATFORM_CORPUS.resolve()

CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

SHAPE_LEXICON: dict[str, tuple[str, ...]] = {
    "独立概览页": ("独立进入", "概览视图", "总览页", "驾驶舱"),
    "内嵌聚合面板": ("列表旁", "面板", "侧栏", "摘要", "汇总条"),
    "日历排班": ("日历", "排班", "跨月"),
    "矩阵排行": ("矩阵", "排行", "对比", "比较"),
    "时间轴流水": ("时间轴", "流水", "历史", "阶段推进"),
    "批量编排": ("多选", "批量", "一次提交", "跨页选择"),
    "分阶段队列": ("队列", "分批", "待处理", "处理中"),
    "规则阈值": ("阈值", "超限", "合理范围", "判定规则"),
    "状态机": ("状态流转", "生命周期", "阶段", "归档"),
    "导入导出": ("导入", "导出", "上传", "下载"),
    "权限归属": ("权限", "角色", "只读", "越权", "归属"),
    "缺口异常": ("缺测", "缺失时段", "异常条", "资料不全"),
    "持久一致性": ("刷新", "返回", "重新进入", "持久"),
}


def normalize(text: str) -> str:
    return dedup_lib.normalize(text)


def cjk_text(text: str) -> str:
    return "".join(CJK_RE.findall(text or ""))


def cjk_ngrams(text: str, size: int) -> set[str]:
    run = cjk_text(text)
    if len(run) < size:
        return set()
    return {run[index:index + size] for index in range(len(run) - size + 1)}


def longest_common_fragment(left: str, right: str) -> str:
    """Return the longest contiguous CJK fragment shared by two prompts."""
    left_text = cjk_text(left)
    right_text = cjk_text(right)
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


def common_prefix_length(left: str, right: str, limit: int) -> int:
    left_text = normalize(left)
    right_text = normalize(right)
    length = 0
    for left_char, right_char in zip(left_text[:limit], right_text[:limit]):
        if left_char != right_char:
            break
        length += 1
    return length


def semantic_similarity(left: str, right: str) -> float:
    """Pairwise character-bigram cosine.

    The solo-create helper uses corpus TF-IDF. A two-document corpus would zero
    every shared term, so cross-project checking needs a pair-local fallback.
    """
    left_counter = Counter(dedup_lib.ngrams(left, 2))
    right_counter = Counter(dedup_lib.ngrams(right, 2))
    if not left_counter or not right_counter:
        return 0.0
    dot = sum(value * right_counter.get(gram, 0) for gram, value in left_counter.items())
    left_norm = math.sqrt(sum(value * value for value in left_counter.values()))
    right_norm = math.sqrt(sum(value * value for value in right_counter.values()))
    if not left_norm or not right_norm:
        return 0.0
    return float(dot / (left_norm * right_norm))


def detect_modes(text: str) -> set[str]:
    try:
        _, modes = theme_lib.detect_modes(text)
    except Exception:  # noqa: BLE001 - classifier drift must not silence the lexical gate.
        return set()
    return set(modes)


def detect_shapes(text: str) -> set[str]:
    found: set[str] = set()
    for name, keywords in SHAPE_LEXICON.items():
        if sum(1 for keyword in keywords if keyword in text) >= 2:
            found.add(name)
    return found


def text_overlap(left: str, right: str) -> float:
    return float(dedup_lib.text_overlap(left, right))


def compare_prompts(
    candidate: dict[str, str],
    peer: dict[str, str],
    *,
    max_text_overlap: float = DEFAULT_MAX_TEXT_OVERLAP,
    max_semantic_similarity: float = DEFAULT_MAX_SEMANTIC_SIMILARITY,
    max_common_long_grams: int = DEFAULT_MAX_COMMON_LONG_GRAMS,
    max_common_fragment_length: int = DEFAULT_MAX_COMMON_FRAGMENT_LENGTH,
    max_opening_prefix: int = DEFAULT_MAX_OPENING_PREFIX,
) -> dict[str, object]:
    left = str(candidate.get("prompt") or "")
    right = str(peer.get("prompt") or "")
    left_type = str(candidate.get("task_type") or "")
    right_type = str(peer.get("task_type") or "")
    aliases = {"代码生成": "功能开发", "0-1代码生成": "功能开发",
               "功能迭代": "功能优化", "feature迭代": "功能优化",
               "缺陷修复": "Bugfix", "Bug修复": "Bugfix", "Bug 修复": "Bugfix",
               "工程化": "依赖升级与迁移"}
    left_kind = aliases.get(left_type, left_type).lower()
    right_kind = aliases.get(right_type, right_type).lower()
    same_task_type = bool(left_kind and right_kind and left_kind == right_kind)

    overlap = text_overlap(left, right)
    semantic = semantic_similarity(left, right)
    common_grams = sorted(cjk_ngrams(left, DEFAULT_LONG_GRAM_SIZE)
                          & cjk_ngrams(right, DEFAULT_LONG_GRAM_SIZE))
    common_fragment = longest_common_fragment(left, right)
    opening_prefix = common_prefix_length(left, right, DEFAULT_OPENING_LENGTH)
    left_modes = detect_modes(left)
    right_modes = detect_modes(right)
    shared_modes = sorted(left_modes & right_modes)
    left_capabilities = dedup_lib.capabilities(left)
    right_capabilities = dedup_lib.capabilities(right)
    shared_capabilities = sorted(left_capabilities & right_capabilities)
    left_shapes = detect_shapes(left)
    right_shapes = detect_shapes(right)
    shared_shapes = sorted(left_shapes & right_shapes)

    relevant_pair = same_task_type and bool(shared_modes)
    violations: list[dict[str, object]] = []
    if relevant_pair and overlap > max_text_overlap:
        violations.append({
            "kind": "text_overlap",
            "score": round(overlap, 4),
            "limit": max_text_overlap,
            "message": "跨项目字符三元组重合率超限，典型的只换业务名词未换题面骨架",
        })
    if relevant_pair and semantic > max_semantic_similarity:
        violations.append({
            "kind": "semantic_similarity",
            "score": round(semantic, 4),
            "limit": max_semantic_similarity,
            "message": "跨项目字符二元组余弦近似度超限，需要同时换诉求组织方式",
        })
    if (relevant_pair and (len(common_grams) > max_common_long_grams
                           or len(common_fragment) >= max_common_fragment_length)):
        violations.append({
            "kind": "long_fragment",
            "score": max(len(common_grams), len(common_fragment)),
            "limit": f">{max_common_long_grams} 个 8 字片段或 {max_common_fragment_length} 字连续片段",
            "common_fragments": common_grams[:12],
            "longest_common_fragment": common_fragment,
            "message": "出现可复制的连续中文长片段，不能靠同义替换规避",
        })
    if same_task_type and shared_modes and opening_prefix >= max_opening_prefix:
        violations.append({
            "kind": "opening_fingerprint",
            "score": opening_prefix,
            "limit": max_opening_prefix,
            "message": "同一任务类型和需求模式下，开头骨架与其它项目重复",
        })
    if (same_task_type and shared_modes and shared_shapes
            and (len(shared_capabilities) >= 3
                 or overlap > max_text_overlap * 0.8)):
        violations.append({
            "kind": "same_solution_shape",
            "score": len(shared_shapes),
            "limit": 0,
            "shared_shapes": shared_shapes,
            "shared_capabilities": shared_capabilities,
            "message": "同一任务类型和需求模式沿用了相同解法形态，必须改入口、交互或动作顺序",
        })
    if (not relevant_pair and (
            overlap > DEFAULT_UNRELATED_TEXT_OVERLAP
            or semantic > DEFAULT_UNRELATED_SEMANTIC_SIMILARITY
            or len(common_fragment) >= DEFAULT_UNRELATED_FRAGMENT_LENGTH)):
        violations.append({
            "kind": "unrelated_pair_extreme_clone",
            "score": max(overlap, semantic),
            "limit": (
                f"text>{DEFAULT_UNRELATED_TEXT_OVERLAP}, "
                f"semantic>{DEFAULT_UNRELATED_SEMANTIC_SIMILARITY}, "
                f"fragment>={DEFAULT_UNRELATED_FRAGMENT_LENGTH}"
            ),
            "message": "任务类型或需求模式虽不相交，但题面仍达到极端克隆水平",
        })

    return {
        "ok": not violations,
        "same_task_type": same_task_type,
        "relevant_pair": relevant_pair,
        "text_overlap": round(overlap, 4),
        "semantic_similarity": round(semantic, 4),
        "common_long_grams": common_grams,
        "longest_common_fragment": common_fragment,
        "opening_prefix": opening_prefix,
        "shared_modes": shared_modes,
        "shared_capabilities": shared_capabilities,
        "shared_shapes": shared_shapes,
        "violations": violations,
    }


def load_workbook_items(path: Path) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for record in workbook_lib.read_workbook(path):
        prompt = str(record.get("提示词", "") or "").strip()
        if not prompt:
            continue
        items.append({
            "label": str(record.get("子文件夹名称", "") or path.stem),
            "task_type": str(record.get("任务类型", "") or ""),
            "prompt": prompt,
        })
    return items


def load_platform_corpus(path: Path) -> tuple[list[dict[str, str]], dict[str, object]]:
    """读取平台题面快照，转成与本地 peer 相同的比较形状。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("平台题面语料顶层必须是对象")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("平台题面语料缺少 items 数组")
    items: list[dict[str, str]] = []
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            continue
        prompt = str(raw.get("prompt") or raw.get("user_prompt") or "").strip()
        if not prompt:
            continue
        record_id = str(raw.get("id") or index)
        repo_id = str(raw.get("repo_id") or "")
        items.append({
            "label": str(raw.get("label") or repo_id or f"platform#{record_id}"),
            "task_type": str(raw.get("task_type") or raw.get("question_type") or ""),
            "prompt": prompt,
            "platform_id": record_id,
            "repo_id": repo_id,
            "status": str(raw.get("status") or ""),
            "submitted_at": str(raw.get("submitted_at") or ""),
            "source": "platform",
            "batch": "platform",
            "batch_id": "platform",
        })
    meta = {
        "profile": str(payload.get("profile") or ""),
        "account": str(payload.get("account") or ""),
        "fetched_at": str(payload.get("fetched_at") or ""),
        "record_count": len(items),
    }
    return items, meta


def corpus_age_hours(meta: dict[str, object]) -> float | None:
    raw = str(meta.get("fetched_at") or "").strip()
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - moment.astimezone(timezone.utc)).total_seconds() / 3600)


def _prompt_self_match(candidate: dict[str, str], peer: dict[str, str]) -> bool:
    """同一批次复检时，允许忽略候选对应的那条平台旧记录。"""
    label = str(candidate.get("label") or "").strip()
    candidate_repo = str(candidate.get("repo_id") or "").strip()
    peer_repo = str(peer.get("repo_id") or "").strip()
    if candidate_repo and peer_repo and candidate_repo == peer_repo:
        return True
    if label and peer_repo and peer_repo.rsplit("/", 1)[-1] == label:
        return True
    return False


def compare_platform_prompt(
    candidate: dict[str, str],
    peer: dict[str, str],
    *,
    max_similarity: float = DEFAULT_PLATFORM_MAX_SIMILARITY,
    max_common_fragment: int = DEFAULT_PLATFORM_MAX_COMMON_FRAGMENT,
    min_common_han: int = DEFAULT_PLATFORM_MIN_COMMON_HAN,
) -> dict[str, object]:
    left = str(candidate.get("prompt") or "")
    right = str(peer.get("prompt") or "")
    matcher = SequenceMatcher(None, left, right)
    match = matcher.find_longest_match()
    fragment = left[match.a:match.a + match.size]
    han_count = sum(1 for char in fragment if "\u4e00" <= char <= "\u9fff")
    ratio = float(matcher.ratio())
    violations: list[dict[str, object]] = []
    if ratio >= max_similarity:
        violations.append({
            "kind": "platform_text_similarity",
            "score": round(ratio, 4),
            "limit": max_similarity,
            "message": "与平台历史题面相似度达到 A-5 / A-4 风险线",
        })
    if len(fragment) >= max_common_fragment and han_count >= min_common_han:
        violations.append({
            "kind": "platform_common_fragment",
            "score": len(fragment),
            "limit": max_common_fragment,
            "common_fragment": fragment,
            "message": "与平台历史题面共有长片段，属于平台已实测会判 A-5 的模板复用",
        })
    return {
        "ok": not violations,
        "similarity": round(ratio, 4),
        "longest_common_fragment": fragment,
        "longest_common_fragment_length": len(fragment),
        "common_han": han_count,
        "violations": violations,
    }


def check_platform_corpus(
    candidates: list[dict[str, str]],
    peers: list[dict[str, str]],
    *,
    max_similarity: float = DEFAULT_PLATFORM_MAX_SIMILARITY,
    max_common_fragment: int = DEFAULT_PLATFORM_MAX_COMMON_FRAGMENT,
) -> dict[str, object]:
    comparisons: list[dict[str, object]] = []
    violations: list[dict[str, object]] = []
    max_ratio = 0.0
    max_fragment = 0
    for candidate in candidates:
        for peer in peers:
            if _prompt_self_match(candidate, peer):
                continue
            result = compare_platform_prompt(
                candidate,
                peer,
                max_similarity=max_similarity,
                max_common_fragment=max_common_fragment,
            )
            max_ratio = max(max_ratio, float(result["similarity"]))
            max_fragment = max(max_fragment, int(result["longest_common_fragment_length"]))
            item = {"candidate": candidate, "peer": peer, **result}
            comparisons.append(item)
            if not result["ok"]:
                violations.append(item)
    violations.sort(
        key=lambda item: (
            -float(item["similarity"]),
            -int(item["longest_common_fragment_length"]),
            str(item["candidate"]["label"]),
        )
    )
    nearest = sorted(
        comparisons,
        key=lambda item: (
            -max(float(item["similarity"]), int(item["longest_common_fragment_length"]) / 100.0),
            str(item["candidate"]["label"]),
            str(item["peer"]["label"]),
        ),
    )[:5]
    return {
        "ok": not violations,
        "comparisons": len(comparisons),
        "violation_count": len(violations),
        "max_similarity": round(max_ratio, 4),
        "max_common_fragment_length": max_fragment,
        "nearest": nearest,
        "violations": violations,
    }


def check_forbidden_topics(candidates: list[dict[str, str]]) -> list[dict[str, object]]:
    violations: list[dict[str, object]] = []
    for candidate in candidates:
        prompt = str(candidate.get("prompt") or "")
        for term, category, reason in FORBIDDEN_TOPIC_TERMS:
            if term in prompt:
                violations.append({
                    "candidate": candidate,
                    "term": term,
                    "category": category,
                    "message": f"命中平台雷同题库「{category}」：{term}；{reason}",
                })
    return violations


def load_candidate_file(path: Path, task_type: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        if text.startswith('{'):
            row=json.loads(text)
            if not isinstance(row,dict) or not isinstance(row.get('prompt'),str):raise ValueError('候选 JSONL 缺少 prompt')
            items.append({'label':row.get('folder') or row.get('label') or f'{path.name}#{index}',
                          'task_type':row.get('task_type',task_type),'prompt':row['prompt']})
            continue
        fields = text.split("\t")
        if len(fields) >= 3:
            items.append({
                "label": fields[0].strip() or f"{path.name}#{index}",
                "task_type": fields[1].strip(),
                "prompt": fields[2].strip(),
            })
        elif len(fields) == 2:
            items.append({
                "label": fields[0].strip() or f"{path.name}#{index}",
                "task_type": task_type,
                "prompt": fields[1].strip(),
            })
        else:
            items.append({
                "label": f"{path.name}#{index}",
                "task_type": task_type,
                "prompt": text,
            })
    return [item for item in items if item["prompt"]]


def discover_peer_batches(
    root: Path,
    current_batch: Path,
    selected_names: set[str] | None = None,
    workbook_name: str = DEFAULT_WORKBOOK,
) -> list[Path]:
    current = current_batch.resolve()
    peers: list[Path] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or child.resolve() == current:
            continue
        if selected_names and child.name not in selected_names:
            continue
        if (child / workbook_name).is_file():
            peers.append(child)
    return peers


def selected_peer(name: str, workbook: str | Path, selected_names: set[str]) -> bool:
    if not selected_names:
        return True
    workbook_path = Path(workbook).expanduser()
    return name in selected_names or workbook_path.parent.name in selected_names


def load_registry_peers(
    registry: dict[str, object],
    *,
    current_batch: Path,
    selected_names: set[str],
    workbook_name: str,
) -> tuple[list[dict[str, str]], list[dict[str, object]], set[str], int]:
    peers: list[dict[str, str]] = []
    issues: list[dict[str, object]] = []
    seen_paths: set[str] = set()
    batch_count = 0
    entries = registry.get("entries")
    if not isinstance(entries, dict):
        return peers, [{"kind": "invalid_registry_entries"}], seen_paths, batch_count

    current = registry_lib.canonical_path(current_batch)
    for key, entry in sorted(entries.items(), key=lambda item: str(item[0])):
        if not isinstance(entry, dict):
            issues.append({"kind": "invalid_registry_entry", "key": str(key)})
            continue
        if str(entry.get("status") or "").strip().lower() == "excluded":
            continue
        raw_workbook = str(entry.get("workbook") or key)
        workbook = Path(raw_workbook).expanduser()
        workbook_key = registry_lib.canonical_path(workbook)
        raw_batch_dir = str(entry.get("batch_dir") or workbook.parent)
        batch_dir = registry_lib.canonical_path(raw_batch_dir)
        batch_id = str(entry.get("batch_id") or workbook.parent.name)

        if batch_dir == current or workbook_key == registry_lib.canonical_path(
            current_batch / workbook_name
        ):
            continue
        if not selected_peer(batch_id, workbook, selected_names):
            continue

        if not workbook.is_file():
            issues.append({
                "kind": "missing_workbook",
                "batch_id": batch_id,
                "workbook": str(workbook),
            })
            continue
        items = load_workbook_items(workbook)
        entry_problems = registry_lib.entry_issues(
            entry,
            expected_prompt_count=len(items),
        )
        if entry_problems:
            issues.extend(entry_problems)
            continue

        seen_paths.add(workbook_key)
        if items:
            batch_count += 1
        for item in items:
            peers.append({
                **item,
                "batch": batch_id,
                "batch_id": batch_id,
                "workbook": str(workbook),
            })
    return peers, issues, seen_paths, batch_count


def check(
    candidates: list[dict[str, str]],
    peers: list[dict[str, str]],
    *,
    max_text_overlap: float = DEFAULT_MAX_TEXT_OVERLAP,
    max_semantic_similarity: float = DEFAULT_MAX_SEMANTIC_SIMILARITY,
) -> dict[str, object]:
    comparisons: list[dict[str, object]] = []
    violations: list[dict[str, object]] = []
    max_text = 0.0
    max_semantic = 0.0
    max_fragment = 0
    for candidate in candidates:
        for peer in peers:
            result = compare_prompts(
                candidate,
                peer,
                max_text_overlap=max_text_overlap,
                max_semantic_similarity=max_semantic_similarity,
            )
            max_text = max(max_text, float(result["text_overlap"]))
            max_semantic = max(max_semantic, float(result["semantic_similarity"]))
            max_fragment = max(max_fragment, len(str(result["longest_common_fragment"])))
            comparisons.append({
                "candidate": candidate,
                "peer": peer,
                **result,
            })
            if not result["ok"]:
                violations.append({
                    "candidate": candidate,
                    "peer": peer,
                    **result,
                })

    violations.sort(
        key=lambda item: (
            -len(item["violations"]),
            -float(item["text_overlap"]),
            -float(item["semantic_similarity"]),
            str(item["candidate"]["label"]),
        )
    )
    nearest = sorted(
        comparisons,
        key=lambda item: (
            -max(float(item["text_overlap"]), float(item["semantic_similarity"])),
            str(item["candidate"]["label"]),
            str(item["peer"]["label"]),
        ),
    )[:5]
    return {
        "ok": not violations,
        "candidate_count": len(candidates),
        "peer_prompt_count": len(peers),
        "comparisons": len(comparisons),
        "max_text_overlap": round(max_text, 4),
        "max_semantic_similarity": round(max_semantic, 4),
        "max_common_fragment_length": max_fragment,
        "violation_count": len(violations),
        "nearest": [
            {
                "candidate": item["candidate"],
                "peer": item["peer"],
                "text_overlap": item["text_overlap"],
                "semantic_similarity": item["semantic_similarity"],
                "longest_common_fragment": item["longest_common_fragment"],
                "shared_modes": item["shared_modes"],
                "shared_shapes": item["shared_shapes"],
            }
            for item in nearest
        ],
        "violations": violations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GSB sibling-batch prompt de-duplication gate."
    )
    parser.add_argument("--batch-dir", required=True, help="当前 GSB 批次目录")
    parser.add_argument(
        "--cross-project-root",
        help="同级批次所在目录；默认取 batch-dir 的上一级",
    )
    parser.add_argument("--workbook", default=DEFAULT_WORKBOOK, help="批次工作簿文件名")
    parser.add_argument("--candidate-workbook", help="显式指定候选工作簿，便于复检历史版本")
    parser.add_argument("--prompt", help="只检查一条候选提示词")
    parser.add_argument("--prompts-file", help="候选提示词文件；每行纯文本或 label<TAB>type<TAB>prompt")
    parser.add_argument("--task-type", default="", help="--prompt/--prompts-file 的任务类型")
    parser.add_argument("--source-folder", default="", help="--prompt 对应的子文件夹名称")
    parser.add_argument("--exclude-folder", action="append", default=[], help="扫描工作簿时跳过该子文件夹")
    parser.add_argument(
        "--peer-batch",
        action="append",
        default=[],
        help="只与指定同级批次比较，可重复传入；默认比较 cross-project-root 下全部同级批次",
    )
    parser.add_argument(
        "--registry",
        help=f"跨目录历史注册表 JSON；默认 {DEFAULT_REGISTRY}",
    )
    parser.add_argument(
        "--require-registry",
        action="store_true",
        help="注册表不存在或不可读时直接失败",
    )
    parser.add_argument(
        "--require-coverage",
        action="store_true",
        help="注册表覆盖不完整、工作簿缺失/变更或 peer 太少时直接失败",
    )
    parser.add_argument(
        "--bootstrap-no-history",
        action="store_true",
        help="首次批次显式声明没有历史 peer；只在 bootstrap 流程中使用",
    )
    parser.add_argument(
        "--min-peer-batches",
        type=int,
        default=None,
        help="至少需要多少条有题面的历史批次；require-coverage 时默认 1",
    )
    parser.add_argument(
        "--root-fallback",
        action="store_true",
        help="注册表模式之外，同时加载 cross-project-root 下未登记的同级工作簿",
    )
    parser.add_argument(
        "--platform-corpus",
        help=f"平台历史题面语料 JSON；默认 {DEFAULT_PLATFORM_CORPUS}",
    )
    parser.add_argument(
        "--no-platform-corpus",
        action="store_true",
        help="关闭平台历史题面语料比较，只做本地跨项目查重",
    )
    parser.add_argument(
        "--require-platform-corpus",
        action="store_true",
        help="平台语料缺失或过期时直接失败",
    )
    parser.add_argument(
        "--platform-max-similarity",
        type=float,
        default=DEFAULT_PLATFORM_MAX_SIMILARITY,
        help="平台题面相似度硬阈值，默认 0.13（低于提交侧 0.18）",
    )
    parser.add_argument(
        "--platform-max-common-fragment",
        type=int,
        default=DEFAULT_PLATFORM_MAX_COMMON_FRAGMENT,
        help="平台题面最长公共汉字片段硬阈值，默认 30",
    )
    parser.add_argument("--max-text-overlap", type=float, default=DEFAULT_MAX_TEXT_OVERLAP)
    parser.add_argument(
        "--max-semantic-similarity",
        type=float,
        default=DEFAULT_MAX_SEMANTIC_SIMILARITY,
    )
    args = parser.parse_args()

    batch_dir = Path(args.batch_dir).expanduser().resolve()
    if not batch_dir.is_dir():
        raise SystemExit(f"批次目录不存在：{batch_dir}")
    root = (
        Path(args.cross_project_root).expanduser().resolve()
        if args.cross_project_root
        else batch_dir.parent
    )
    if not root.is_dir():
        raise SystemExit(f"跨项目根目录不存在：{root}")
    registry_path = (
        Path(args.registry).expanduser().resolve()
        if args.registry
        else registry_lib.default_registry_path(fallback=DEFAULT_REGISTRY)
    )

    if args.prompt:
        candidates = [{
            "label": args.source_folder or "<candidate>",
            "task_type": args.task_type,
            "prompt": args.prompt.strip(),
        }]
    elif args.prompts_file:
        candidates = load_candidate_file(
            Path(args.prompts_file).expanduser().resolve(),
            args.task_type,
        )
    else:
        workbook = (
            Path(args.candidate_workbook).expanduser().resolve()
            if args.candidate_workbook
            else batch_dir / args.workbook
        )
        if not workbook.is_file():
            raise SystemExit(f"候选工作簿不存在：{workbook}")
        candidates = [
            item for item in load_workbook_items(workbook)
            if item["label"] not in set(args.exclude_folder)
        ]

    selected_names = set(args.peer_batch)
    peer_batch_dirs = discover_peer_batches(
        root,
        batch_dir,
        selected_names,
        args.workbook,
    )
    root_peer_paths = {
        registry_lib.canonical_path(path / args.workbook)
        for path in peer_batch_dirs
    }

    registry_requested = bool(
        args.registry or args.require_registry or args.require_coverage
    ) or registry_path.is_file()
    registry_issues: list[dict[str, object]] = []
    registry_used = False
    registry_peers: list[dict[str, str]] = []
    registered_paths: set[str] = set()
    registry_batch_count = 0
    if registry_requested:
        if not registry_path.is_file():
            registry_issues.append({
                "kind": "missing_registry",
                "registry": str(registry_path),
            })
        else:
            try:
                registry = registry_lib.load_registry(registry_path)
                registry_used = True
                (
                    registry_peers,
                    registry_issues,
                    registered_paths,
                    registry_batch_count,
                ) = load_registry_peers(
                    registry,
                    current_batch=batch_dir,
                    selected_names=selected_names,
                    workbook_name=args.workbook,
                )
            except (OSError, ValueError) as exc:
                registry_issues.append({
                    "kind": "invalid_registry",
                    "registry": str(registry_path),
                    "message": str(exc),
                })

    peers: list[dict[str, str]] = []
    peers.extend(registry_peers)
    seen_peer_paths = set(registered_paths)
    coverage_issues: list[dict[str, object]] = list(registry_issues)
    root_fallback_enabled = not registry_used or args.root_fallback
    if root_fallback_enabled:
        for peer_batch in peer_batch_dirs:
            workbook = peer_batch / args.workbook
            workbook_key = registry_lib.canonical_path(workbook)
            if workbook_key in seen_peer_paths:
                continue
            seen_peer_paths.add(workbook_key)
            for item in load_workbook_items(workbook):
                peers.append({
                    **item,
                    "batch": peer_batch.name,
                    "batch_id": peer_batch.name,
                    "workbook": str(workbook),
                })
    elif args.require_coverage:
        for peer_batch in peer_batch_dirs:
            workbook = peer_batch / args.workbook
            workbook_key = registry_lib.canonical_path(workbook)
            if workbook_key in registered_paths:
                continue
            if load_workbook_items(workbook):
                coverage_issues.append({
                    "kind": "unregistered_sibling_workbook",
                    "batch_id": peer_batch.name,
                    "workbook": str(workbook),
                })

    peer_batch_names = sorted({
        str(item.get("batch_id") or item.get("batch") or "")
        for item in peers
        if str(item.get("batch_id") or item.get("batch") or "")
    })
    found_selected = set(peer_batch_names)
    missing_selected = selected_names - found_selected
    if missing_selected:
        coverage_issues.append({
            "kind": "requested_peer_not_found",
            "batches": sorted(missing_selected),
        })

    min_peer_batches = (
        int(args.min_peer_batches)
        if args.min_peer_batches is not None
        else (1 if args.require_coverage else 0)
    )
    if args.bootstrap_no_history:
        min_peer_batches = 0
    if len(peer_batch_names) < min_peer_batches:
        coverage_issues.append({
            "kind": "insufficient_peer_batches",
            "expected": min_peer_batches,
            "actual": len(peer_batch_names),
        })

    platform_path = (
        Path(args.platform_corpus).expanduser().resolve()
        if args.platform_corpus
        else platform_corpus_path()
    )
    platform_enabled = bool(platform_path) and not args.no_platform_corpus
    platform_peers: list[dict[str, str]] = []
    platform_meta: dict[str, object] = {}
    platform_issues: list[dict[str, object]] = []
    platform_corpus_used = False
    platform_max_similarity = args.platform_max_similarity
    platform_max_common_fragment = args.platform_max_common_fragment
    if platform_enabled:
        assert platform_path is not None
        if not platform_path.is_file():
            platform_issues.append({
                "kind": "missing_platform_corpus",
                "path": str(platform_path),
                "message": "平台历史题面语料不存在，先运行 sync_platform_prompt_corpus.py",
            })
        else:
            try:
                platform_peers, platform_meta = load_platform_corpus(platform_path)
                platform_corpus_used = True
                age_hours = corpus_age_hours(platform_meta)
                if (
                    age_hours is not None
                    and age_hours > DEFAULT_PLATFORM_CORPUS_MAX_AGE_HOURS
                ):
                    issue = {
                        "kind": "stale_platform_corpus",
                        "path": str(platform_path),
                        "age_hours": round(age_hours, 2),
                        "message": (
                            f"平台题面语料已 {age_hours:.1f} 小时未刷新，建议先运行 "
                            "sync_platform_prompt_corpus.py --refresh"
                        ),
                    }
                    if args.require_platform_corpus:
                        platform_issues.append(issue)
                    else:
                        platform_meta["warning"] = issue["message"]
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                platform_issues.append({
                    "kind": "invalid_platform_corpus",
                    "path": str(platform_path),
                    "message": str(exc),
                })
    platform_result = check_platform_corpus(
        candidates,
        platform_peers,
        max_similarity=platform_max_similarity,
        max_common_fragment=platform_max_common_fragment,
    )
    forbidden_violations = check_forbidden_topics(candidates)

    result = check(
        candidates,
        peers,
        max_text_overlap=args.max_text_overlap,
        max_semantic_similarity=args.max_semantic_similarity,
    )
    coverage_ok = not coverage_issues
    platform_ok = platform_result["ok"] and not platform_issues
    forbidden_ok = not forbidden_violations
    final_ok = (
        bool(result["ok"])
        and platform_ok
        and forbidden_ok
        and (coverage_ok or not args.require_coverage)
    )
    result.update({
        "ok": final_ok,
        "similarity_ok": result["ok"],
        "cross_project_ok": final_ok,
        "cross_project_violation_count": result["violation_count"],
        "cross_project_max_text_overlap": result["max_text_overlap"],
        "cross_project_max_semantic_similarity": result["max_semantic_similarity"],
        "cross_project_max_common_fragment_length": result["max_common_fragment_length"],
        "cross_project_peer_batches": peer_batch_names,
        "platform_guard_ok": platform_ok and forbidden_ok,
        "platform_corpus_used": platform_corpus_used,
        "platform_corpus_path": str(platform_path) if platform_path else "",
        "platform_corpus": platform_meta,
        "platform_corpus_issues": platform_issues,
        "platform_dedup_ok": bool(platform_result["ok"]),
        "platform_dedup_violation_count": int(platform_result["violation_count"]),
        "platform_dedup_max_similarity": platform_result["max_similarity"],
        "platform_dedup_max_common_fragment_length": platform_result["max_common_fragment_length"],
        "platform_dedup_nearest": platform_result["nearest"],
        "platform_dedup_violations": platform_result["violations"],
        "platform_forbidden_topic_ok": forbidden_ok,
        "platform_forbidden_topic_violations": forbidden_violations,
        "batch_dir": str(batch_dir),
        "cross_project_root": str(root),
        "peer_batches": peer_batch_names,
        "registry_requested": registry_requested,
        "registry_used": registry_used,
        "registry_path": str(registry_path),
        "registry_exists": registry_path.is_file(),
        "registry_batch_count": registry_batch_count,
        "registered_workbook_count": len(registered_paths),
        "coverage_ok": coverage_ok,
        "coverage_issues": coverage_issues,
        "min_peer_batches": min_peer_batches,
        "bootstrap_no_history": bool(args.bootstrap_no_history),
        "root_fallback": root_fallback_enabled,
        "thresholds": {
            "max_text_overlap": args.max_text_overlap,
            "max_semantic_similarity": args.max_semantic_similarity,
            "max_common_long_grams": DEFAULT_MAX_COMMON_LONG_GRAMS,
            "max_common_fragment_length": DEFAULT_MAX_COMMON_FRAGMENT_LENGTH,
            "max_opening_prefix": DEFAULT_MAX_OPENING_PREFIX,
            "unrelated_max_text_overlap": DEFAULT_UNRELATED_TEXT_OVERLAP,
            "unrelated_max_semantic_similarity": DEFAULT_UNRELATED_SEMANTIC_SIMILARITY,
            "unrelated_max_common_fragment_length": DEFAULT_UNRELATED_FRAGMENT_LENGTH,
            "platform_max_similarity": platform_max_similarity,
            "platform_max_common_fragment": platform_max_common_fragment,
            "platform_max_age_hours": DEFAULT_PLATFORM_CORPUS_MAX_AGE_HOURS,
        },
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
