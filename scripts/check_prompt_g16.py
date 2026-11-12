#!/usr/bin/env python3
"""GSB 提示词难度下限闸门（平台规则 G16 / G17）。

背景（2026-09-29 约 11:00 平台质检口径调整，2026-09-30 本地闸门硬化）：

- G16「题目难度达不到『困难』」：本期只收 `困难` / `地狱`。题面被判成 `中等`
  时**直接作废、不可返修**——难度是题目本身的属性，改写措辞或调高难度字段都无效。
  普通任务类型对 `困难` 的判据是三项特征**至少成立两项**；
  `Bugfix`（含 `Bug修复` / `bugfix` 别名）必须**三项全部成立**：
    1. 多模块整合；2. 关键设计取舍；3. 复杂技术关注点。
- 2026-09-30 起再加一层**硬证据**：三项特征光靠弱证据撑不算数。平台累计判废 15 条
  （cc10016 13 条 + cc10017 2 条）时，本地词面闸门把「本模块概览/看板/统计数字与列表
  一致」「以后端返回为准而前端不再重算」「单独一句历史按当时口径保留」「批量逐条回执」
  「把几处判断收拢成一份」都当成了困难特征 —— 这五种写法平台判词里全部点名否定过。
  所以至少要有一项落到硬证据组（状态机与单向推进 / 并发与幂等 / 权限与归属 /
  事务与落库一致性 / 断点续做与失败链路 / 口径版本化与重算 / 迁移与存量回填 /
  另一个业务面跟着变 / 冲突口径与优先级裁决）。
- G17「难度标『地狱』实为『困难』」：题面只到 `困难` 却标了 `地狱` 会被打回，
  改标 `困难` 后重提即可。`地狱` 要额外满足「需求高度开放或约束隐蔽 / 需要架构级
  判断 / 多步复杂的调试验证 / 正确性依赖大量边界场景」其中两项。

2026-09-30 平台一度放宽 `Bug修复`（本地列名 `Bugfix`）与 `feature迭代`（本地列名
`功能优化`）为「三档难度都收」，**2026-10-01 起本闸门撤回这条放宽，两类重新按 G16
困难下限硬判**（`困难` 下限对五类任务类型一视同仁）。撤回依据是平台回执：这两类题
一旦落到「中等」档，仍会被判废，且判词比放宽之前更严——

  - `GSB 规则 G19 · 迭代 / 修复题难度达不到「中等」`（2026-10-01 新规则）：迭代 / 修复
    题最低收 `中等`，而且「中等」要同时成立三项特征 —— 调用/数据流理解、实现决策、
    边界条件与约束（平台 `medium_threshold: 3`）。只成立一两项的照样作废、不可返修。
  - 同一账号 654 条平台回执里 32 条已废弃，其中难度类 24 条（G16 22 条 + G19 2 条），
    是最主要的一类；`功能开发` / `代码重构` / `依赖升级与迁移` / `feature迭代` / `Bug修复`
    五类都出现过。

所以本脚本同时管三件事：G16 困难下限（五类全查）、G19 中等下限（只对迭代 / 修复作
提示，见下）与 G17 地狱上限。G19 的「中等」是语义判断，词面只做兜底筛选；一般缺项
默认只提示、不拦，`--strict` 才把「单一功能模块」「G19 疑似不达中等」与「硬证据只出现
在末句」一起当不合格（GSB 批次建议带 `--strict` 自查）。但 2026-10-07 平台真实回执
确认的两种 UI-only 形状已经改为默认硬拦，见 `PLATFORM_G19_HIGH_CONFIDENCE_REASONS`。

另有两条与难度相关、但不在本脚本硬判范围内的判废原因，写在 SKILL.md 里当出题纪律：

- `GSB 规则 G18 · 当日两侧满分占比超限`：难度不够时 A / B 两侧都容易拿满分，当日两侧
  满分占比超过 10% 会被整批作废。把题面做成跨多个功能模块的困难题，是压低这条的手段。
- 单一功能模块的题面是 G16 / G19 判词的常客（「单一…功能」「局部到跨层」「无架构取舍」），
  所以每题都必须落在 ≥2 个功能模块上（见 `multi_module` 信号）。

判据只有一份，在 $cc-gsb-create 的 `scripts/gsb_difficulty.py`：跑题前
（`gsb_runner.py`）、写表前（`gsb_result.py`）与出题前（本脚本）必须用同一套，谁都不许
另抄一份正则。本脚本只负责从工作簿读题面、把判定按出题阶段的格式打印出来。

本脚本仍是词面兜底：`ok: true` 只代表题面里出现了够硬的难度信号，**不代表平台一定判
困难**；生成方仍要按 `SKILL.md` 的三项特征逐条自查。`ok: false` 则必须换切入点和
主诉求重出，不许只加形容词。

用法：

    python3 scripts/check_prompt_g16.py --parent "<父目录>"
    python3 scripts/check_prompt_g16.py --prompts-file "<每行一条提示词的文本文件>"
    python3 scripts/check_prompt_g16.py --parent "<父目录>" --task-types 功能开发,功能优化
    python3 scripts/check_prompt_g16.py --parent "<父目录>" --strict   # 末句硬证据也算问题
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from solo_paths import gsb_create_scripts, solo_create_scripts  # noqa: E402

SOLO_CREATE_SCRIPTS = solo_create_scripts("batch_prompt_workbook.py")
if str(SOLO_CREATE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SOLO_CREATE_SCRIPTS))

import batch_prompt_workbook as workbook_lib  # noqa: E402


def _load_judge():
    """加载 $cc-gsb-create 里那份唯一的 G16 / G17 判据。"""
    judge_path = gsb_create_scripts("gsb_difficulty.py") / "gsb_difficulty.py"
    spec = importlib.util.spec_from_file_location("gsb_difficulty_canonical", judge_path)
    if spec is None or spec.loader is None:  # pragma: no cover - 文件存在时不会走到
        raise SystemExit(f"加载不了 G16/G17 判据：{judge_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, judge_path


JUDGE, JUDGE_PATH = _load_judge()

DEFAULT_WORKBOOK = "tasks.xlsx"
ALL_TASK_TYPES = {"all", "全部", "*"}
MIN_FEATURES = JUDGE.MIN_FEATURES
BUGFIX_MIN_FEATURES = JUDGE.BUGFIX_MIN_FEATURES
FEATURE_NAMES = JUDGE.FEATURE_KEYS
HARD_EVIDENCE_KEYS = JUDGE.HARD_EVIDENCE_KEYS
HELL_MIN_FEATURES = JUDGE.MIN_HELL_FEATURES
# 备注里写清「难度复核: …」的条目视为逐条复核过：平台判难度是语义口径，脚本只能做兜底
# 筛选；确实判断这条题能到 `困难` 时，把要求成立的特征逐项写进备注留档。
JUSTIFICATION_PREFIX = "难度复核"

RULE_G16 = "GSB 规则 G16 · 题目难度达不到「困难」"
RULE_G19 = "GSB 规则 G19 · 迭代 / 修复题难度达不到「中等」"
RULE_G17 = "GSB 规则 G17 · 难度标「地狱」实为「困难」"

# 2026-10-01：撤回 2026-09-30 对 `Bug修复` / `feature迭代` 的放宽，两类重新按 G16 困难
# 下限硬判（与其余三类同一口径，Bugfix按判据仍要求三项全备）。键的规范化口径与
# `gsb_difficulty._normalize_task_type` 一致（去空白 + 转小写），本地列名与平台枚举都列全。
ITERATION_REPAIR_TASK_TYPES = frozenset({
    "Bugfix", "bug修复", "bugfix",
    "功能优化", "feature迭代", "feature",
})

POLICY_HARD_FLOOR = "hard-floor"

# ---------------------------------------------------------------------------
# G19「迭代 / 修复题难度达不到『中等』」（2026-10-01 平台新规则）
#
# 平台对迭代 / 修复题的难度判据是**两层**：先看「中等」是否成立（G19，三项全备），
# 再看「困难」是否成立（G16）。实测判废样本（cc10019 #19 / #20806）：
#
#   #21834 Bug修复 —— 中等三项只成立 1 项（调用/数据流理解），缺实现决策与边界条件，
#                     平台判「题面把空态、未命中提示、导出重试逐条列明，做法基本给定」；
#   #20806 feature迭代 —— 中等三项成立 2 项（调用/数据流理解 + 边界条件），缺实现决策，
#                     平台判「排序、收起、回第一页、空态等行为题面已逐条规定，做法被写死」。
#
# 「实现决策」这一项是纯语义判断（题目有没有留出影响结果对错的实现选择），词面无法可靠
# 复现：按最宽的词面口径去量，平台判通过的迭代 / 修复题里绝大多数也会被量成「缺实现
# 决策」。所以这里只做两件事：给出一份**近似**的中等特征读数，用于出题时自查与人工
# 复核；默认只提示、不拦，但下面另行维护平台真实回执已经确认的 UI-only 硬拦形状。
MEDIUM_FLOOR_FEATURES = ("调用与数据流理解", "实现决策", "边界条件与约束")
MEDIUM_FLOOR_REQUIRED = len(MEDIUM_FLOOR_FEATURES)
# 「难度复核」备注里能数出来的特征名：困难三项 + G19 的三项中等特征（迭代 / 修复题）。
NOTE_FEATURE_NAMES = tuple(FEATURE_NAMES) + tuple(MEDIUM_FLOOR_FEATURES)
MEDIUM_FLOOR_PATTERNS = {
    "调用与数据流理解": [
        r"(两处|三处|多处|几个入口|各入口|两条路径|两份)[^。；]{0,24}"
        r"(一致|同一份|同一套|对得上|同步|共用|一起取数)",
        r"(只留一份|共用|同一份|统一成一份|收成一份|抽成一份)"
        r"[^。；]{0,26}(取数|规则|口径|判定|写法|实现)",
        r"(台账|列表|详情|面板|清单|提醒|概览|看板|报表|统计|导出|导入|待办|审批|工作台|汇总)"
        r"[^。；]{0,20}(与|和|跟|同)[^。；]{0,20}"
        r"(台账|列表|详情|面板|清单|提醒|概览|看板|报表|统计|导出|导入|待办|审批|工作台|汇总)"
        r"[^。；]{0,16}(一致|对得上|同步|同时更新|一起变|联动)",
        r"(顺着|沿着|跟着|按)[^。；]{0,10}(调用链|调用|链路|数据流|取数|既有实现)"
        r"[^。；]{0,16}(定位|改|修|找|梳理|理解)",
        r"(既有|现有|原有|旧的)[^。；]{0,12}(取数逻辑|调用链|数据流|实现)"
        r"[^。；]{0,16}(改|修|梳理|定位|理解|并对齐)",
    ],
    "实现决策": [
        r"(自行决定|自行设计|自行取舍|由你决定|由你设计|自行选择|自己决定|自行权衡)",
        r"(方案|实现方式|做法|口径)[^。；]{0,10}(自定|自拟|由你|自行|不限|开放)",
        r"(权衡|取舍|孰轻孰重|两种做法)",
        r"(优先级|谁优先|谁说了算|冲突时以谁为准)",
        r"(口径)[^。；]{0,12}(统一|收成|只留一份|定成一份)",
        r"(兼容|沿用)[^。；]{0,10}(既有|已有|历史|老数据|存量)",
        r"(迁移|回填|补数)[^。；]{0,10}(存量|历史|既有)",
    ],
    "边界条件与约束": [
        r"(并发|同时提交|同时操作|竞态|重复提交|重复登记|重复导入|重复归档)",
        r"(越权|权限|归属)[^。；]{0,12}(拦截|拒绝|只读|不得|不允许)",
        r"(事务|落库|持久化|缓存)[^。；]{0,16}(一致|失败|退回|回滚)",
        r"(状态|环节)[^。；]{0,12}(只能|按顺序|单向)[^。；]{0,24}(流转|推进)",
        r"(超时|中断|断线|断电)[^。；]{0,16}(续|接着|重试|恢复|兜底)",
        r"(极值|越界|超出范围|非法值|无效值|精度|四舍五入)",
        r"(时区|夏令时|跨年|跨月|闰|月初|月末|跨天)",
        r"(分页|每页|页码)[^。；]{0,12}(边界|越界|回到第一页|非法)",
    ],
}

# 每题必须落到 ≥2 个功能模块：判据里已经算出的「多模块整合」特征，加上这些更直白的
# 「同一个动作要牵动两个业务面」句式。单一模块是 G16 / G19 判词里出现最多的短板。
MULTI_MODULE_EXTRA_PATTERNS = [
    r"(另一个|其它|其他|别的|其余)[^。；]{0,6}(模块|业务面|入口|页面|链路|台账)",
    r"(跨|打通|串联|贯通|联动)[^。；]{0,6}(模块|链路|入口|页面|业务面)",
    r"(同一个动作|同一个操作|这一步)[^。；]{0,20}(两个|两处|另一处|另一个)[^。；]{0,16}"
    r"(页面|模块|入口|台账|清单|业务面)",
]
# 「多模块整合」只由本模块概览 / 看板 / 统计这类表面词撑起来时，按平台判废 #14 的口径
# 只是弱证据，要提示换真跨模块后果。
WEAK_SURFACE_RE = re.compile(
    r"(概览|看板|首页|总览|统计|报表|汇总|卡片|运营页|工作台|大屏|仪表盘)"
)


def normalize_task_type(task_type: str) -> str:
    return re.sub(r"\s+", "", str(task_type or "")).lower()


def difficulty_policy(task_type: str) -> str:
    """2026-10-01 起五类任务类型一律走 G16 困难下限（`hard-floor`）。

    保留这个函数与返回键，是为了让上下游按老名字读也不出错；2026-09-30 那版
    `medium-allowed` 的放宽已撤回（依据见模块 docstring）。
    """
    return POLICY_HARD_FLOOR


def g16_enforced(task_type: str) -> bool:
    return True


def required_feature_count(task_type: str) -> int:
    """透传 `$cc-gsb-create` 判据里的要求项数（普通两项、Bugfix三项全备）。

    2026-10-01 撤回放宽之后，`Bugfix` 重新按判据要求三项，`功能优化` 与其他类型一样
    两项；两者都要再落一项硬证据（见 `evaluate()`）。
    """
    return JUDGE.required_feature_count(task_type)


def is_iteration_repair(task_type: str) -> bool:
    """迭代 / 修复类（平台 `feature迭代` / `Bug修复`）：额外受 G19 中等下限约束。"""
    return normalize_task_type(task_type) in ITERATION_REPAIR_TASK_TYPES


def multi_module_signals(text: str, strong_features: dict) -> list[str]:
    """题面是不是落到了 ≥2 个功能模块上（判据侧 + 更直白的双业务面句式）。"""
    signals: list[str] = []
    if strong_features.get("多模块整合"):
        signals += list(strong_features["多模块整合"])[:2]
    for pattern in MULTI_MODULE_EXTRA_PATTERNS:
        for match in re.finditer(pattern, str(text or "")):
            token = match.group(0).strip()
            if token and token not in signals:
                signals.append(token)
    return signals


def medium_floor_verdict(text: str) -> dict:
    """G19 词面兜底：迭代 / 修复题的三项中等特征读出来了哪几项。

    只做近似筛选（`实现决策` 本质是语义判断），命中不全时给提示，`--strict` 才拦。
    """
    text = str(text or "")
    hits: dict[str, list[str]] = {}
    for name, patterns in MEDIUM_FLOOR_PATTERNS.items():
        evidence: list[str] = []
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                token = match.group(0).strip()
                if token and token not in evidence:
                    evidence.append(token)
        if evidence:
            hits[name] = evidence
    missing = [name for name in MEDIUM_FLOOR_FEATURES if name not in hits]
    return {
        "hit_features": list(hits),
        "missing_features": missing,
        "evidence": hits,
        "ok": not missing,
    }


PLATFORM_G16_NEGATIVE_REASONS = {
    "cross_call_unification": (
        "平台已实测把「两条/几条路径、几处判定收拢成一份共用实现」判为中等："
        "跨调用点收拢本身不算关键设计取舍或困难；必须换成一个需要状态、权限、"
        "事务、迁移或冲突优先级裁决的切入点。"
    ),
    "single_domain_new_ledger": (
        "平台已实测把「单业务域新建台账 + 重算/回填/去重」判为中等："
        "这类题需要真正的第二个业务面（审批、导出、权限、跨单位归属、工作台等）"
        "或复杂状态链，不能只靠同一台账的列表、详情、概览一致撑难度。"
    ),
    "seed_demo_restore_only": (
        "平台已实测把「示例数据铺底 / 初始化 / 备份重试 / 多视图一致」判为中等："
        "这类依赖升级与迁移边界不能单独证明困难，除非同时存在真实迁移、数据版本、权限归属、"
        "事务一致性或不可跳级的状态链。"
    ),
}

PLATFORM_G19_HIGH_CONFIDENCE_REASONS = {
    "sort_page_ui_only": (
        "平台已实测把「排序 + 分页 + 回到第一页/空态说明」判为 G19 中等："
        "这仍是页面行为组合，缺少影响结果对错的实现决策和非平凡边界约束。"
    ),
    "empty_export_ui_only": (
        "平台已实测把「空态提示 + 重新登记入口 + 导出失败重试」判为 G19 中等："
        "这些补救步骤不能替代对调用链、实现决策和数据边界的真实建模。"
    ),
}


def platform_learned_verdict(text: str, task_type: str) -> dict:
    """平台真实回执提炼出的负向闸门。

    这里只做「降级」：命中已确认的平台中等模板时，把原本可能被正向词面误判为
    困难的题拦下；不新增任何正向特征，也不替代 `gsb_difficulty.py` 的唯一判据。
    """
    text = str(text or "")
    normalized_type = normalize_task_type(task_type)
    problems: list[str] = []
    reasons: list[str] = []

    cross_call_source = re.search(
        r"(?:两套|两条|几处|三处|多处|多个来源|两条路径|多处判定|三个来源)",
        text,
    )
    cross_call_merge = re.search(
        r"(收拢|合并|抽成|归并|统一|共用|同一份|一份实现|一份口径|一份规则)",
        text,
    )
    if cross_call_source and cross_call_merge:
        problems.append("cross_call_unification")
        reasons.append(PLATFORM_G16_NEGATIVE_REASONS["cross_call_unification"])

    if normalized_type in {"0-1功能开发", "功能开发"} and re.search(
        r"(新建|新增|做一个|加一个).{0,12}台账", text
    ):
        stronger_scope = re.search(
            r"(审批|权限|越权|并发|事务|回滚|导出|工作台|班组|负责人|部门|"
            r"指派|交接|回写|落进|落到|运营概览|首页)",
            text,
        )
        if not stronger_scope:
            problems.append("single_domain_new_ledger")
            reasons.append(PLATFORM_G16_NEGATIVE_REASONS["single_domain_new_ledger"])

    if re.search(r"(示例数据|样板数据|铺底|种子数据)", text) and re.search(
        r"(备份|抄底|重试|重复|复位|初始化|覆盖)", text
    ):
        stronger_scope = re.search(
            r"(依赖|锁文件|容器|镜像|构建|部署|权限|越权|并发|事务|状态机|"
            r"迁移|冲突|优先|驳回|拒绝)",
            text,
        )
        if not stronger_scope:
            problems.append("seed_demo_restore_only")
            reasons.append(PLATFORM_G16_NEGATIVE_REASONS["seed_demo_restore_only"])

    g19_problems: list[str] = []
    if is_iteration_repair(task_type):
        sort_page_ui = (
            re.search(r"(排序|次序)", text)
            and re.search(r"(分页|翻页|每页)", text)
            and re.search(r"(回到第一页|页码|空态|找不到|没有命中)", text)
            and not re.search(
                r"(权限|越权|并发|同时提交|事务|落库|回滚|迁移|回填|"
                r"冲突.{0,12}为准|状态.{0,12}只能|重复.{0,12}只)",
                text,
            )
        )
        if sort_page_ui:
            g19_problems.append("sort_page_ui_only")
            reasons.append(PLATFORM_G19_HIGH_CONFIDENCE_REASONS["sort_page_ui_only"])
        empty_export_ui = (
            re.search(r"(空态|空数据|没有命中)", text)
            and re.search(r"(重新登记|新增入口|入口)", text)
            and re.search(r"导出", text)
            and re.search(r"(失败|报错)", text)
            and not re.search(
                r"(权限|越权|并发|同时提交|事务|落库|回滚|迁移|回填|"
                r"冲突.{0,12}为准|状态.{0,12}只能)",
                text,
            )
        )
        if empty_export_ui:
            g19_problems.append("empty_export_ui_only")
            reasons.append(PLATFORM_G19_HIGH_CONFIDENCE_REASONS["empty_export_ui_only"])

    return {
        "g16_blocks": [name for name in problems if name in PLATFORM_G16_NEGATIVE_REASONS],
        "g19_blocks": g19_problems,
        "problems": problems + g19_problems,
        "reasons": reasons,
    }


def evaluate(text: str, task_type: str = "") -> dict:
    """把 `gsb_difficulty` 的判定整理成出题阶段要看的形状。"""
    judge_type = {"功能开发": "代码生成", "功能优化": "功能迭代", "依赖升级与迁移": "工程化"}.get(task_type, task_type)
    verdict = JUDGE.evaluate_prompt(text, judge_type)
    required = int(verdict["要求特征数"])
    features = dict(verdict["特征"])
    hard_features = list(verdict["硬证据组"])
    meets_hard_floor = len(features) >= required and bool(hard_features)
    policy = difficulty_policy(task_type)
    enforced = True
    shape = list(JUDGE.medium_shape_reasons(text, judge_type))
    learned = platform_learned_verdict(text, task_type)
    learned_g16_blocks = list(learned["g16_blocks"])
    learned_g19_blocks = list(learned["g19_blocks"])
    g16_ok = meets_hard_floor and not learned_g16_blocks
    reasons = list(shape)
    if not g16_ok:
        if learned_g16_blocks:
            reasons.extend(learned["reasons"])
        elif len(features) < required:
            found = "、".join(features) or "无"
            if required == BUGFIX_MIN_FEATURES:
                reasons.append(
                    "Bugfix必须同时成立多模块整合、关键设计取舍、复杂技术关注点三项，"
                    f"当前只读出：{found}"
                )
            elif not reasons:
                reasons.append("没读出足够的困难特征")
        else:
            weak = "；".join(f"{name}（{'、'.join(evidence[:2])}）"
                            for name, evidence in dict(verdict["弱证据"]).items()) or "无"
            reasons.append(
                "三项特征只由弱证据撑着，一项硬证据都没有"
                f"（弱证据：{weak}）——这正是被平台判废的那一档。要补一项硬证据："
                + " / ".join(HARD_EVIDENCE_KEYS)
            )
    hell = JUDGE.hell_signals(text)
    suggested = "地狱" if len(hell) >= HELL_MIN_FEATURES else "困难"
    if suggested == "地狱" and len(hell) < HELL_MIN_FEATURES:
        reasons.append(f"标地狱需要至少两项地狱特征，当前只有：{'、'.join(hell) or '无'}")
    # 2026-10-01 新增的多模块与 G19 纪律（一般缺项默认提示，`--strict` 才拦）：
    hints = list(verdict["提示"])
    module_signals = multi_module_signals(text, features)
    integration_evidence = list(features.get("多模块整合") or [])
    if not module_signals:
        hints.append(
            "只读到一个功能模块：单一功能是平台 G16 / G19 判词里出现最多的短板"
            "（「单一…功能」「局部到跨层」「无架构取舍」），每题都要落在 ≥2 个功能模块上，"
            "并把跨模块后果写进主诉求而不是末尾补一句"
        )
    elif integration_evidence and all(
        WEAK_SURFACE_RE.search(token) for token in integration_evidence
    ):
        # 平台判废 #14 的原话就是「保持概览一致，属常规局部到跨层实现」：只靠本模块
        # 概览/看板/统计与列表对齐撑起来的「多模块整合」是弱证据，要提示换成真跨模块后果。
        hints.append(
            "多模块整合只由「本模块概览 / 看板 / 统计与列表一致」这类弱证据撑着"
            "（平台判废 #14 原话：「保持概览一致，属常规局部到跨层实现」），"
            "要换成本模块之外真的多出一份东西（导出 / 审批 / 待办 / 台账 / 工作台跟着变）"
        )
    iteration_repair = is_iteration_repair(task_type)
    medium_floor = medium_floor_verdict(text) if iteration_repair else {
        "hit_features": [], "missing_features": [], "evidence": {}, "ok": True,
    }
    g19_ok = medium_floor["ok"]
    if iteration_repair and not g19_ok:
        hints.append(
            "疑似不达 G19 中等下限：迭代 / 修复题要同时成立「调用与数据流理解 / 实现决策 / "
            "边界条件与约束」三项，词面只读到 "
            + ("、".join(medium_floor["hit_features"]) or "无")
            + "；缺 "
            + ("、".join(medium_floor["missing_features"]) or "无")
            + "。平台对这类题的判词原话是「做法基本给定」「做法被写死，缺实现决策」，"
            "要顺着既有调用链或数据流定位改动点、留出影响结果对错的实现决策、"
            "并同时满足若干非平凡边界约束"
        )
    if learned_g19_blocks:
        reasons.extend([
            reason for reason in learned["reasons"]
            if reason.startswith("平台已实测把")
        ])
    return {
        "g16_ok": g16_ok,
        "g16_enforced": enforced,
        "difficulty_policy": policy,
        "policy_note": (
            "平台 G16 困难下限（五类任务类型一视同仁，2026-10-01 起 Bug修复 / feature迭代 "
            "重新纳入）：至少两项困难特征，Bugfix三项全备，且至少一项落在硬证据组"
        ),
        "meets_hard_floor": meets_hard_floor,
        "required_features": required,
        "hard_floor_required_features": required,
        "strong_features": features,
        "strong_feature_count": len(features),
        "strong_evidence_count": sum(len(items) for items in features.values()),
        "hard_features": hard_features,
        "hard_feature_names": list(verdict["硬特征"]),
        "weak_evidence": dict(verdict["弱证据"]),
        "multi_module": bool(module_signals),
        "multi_module_signals": module_signals,
        "g19_applies": iteration_repair,
        "g19_ok": g19_ok,
        "g19_hard_block": bool(learned_g19_blocks),
        "platform_learned_blocks": list(learned["problems"]),
        "platform_learned_reasons": list(learned["reasons"]),
        "g19_required_features": MEDIUM_FLOOR_REQUIRED if iteration_repair else 0,
        "g19_hit_features": list(medium_floor["hit_features"]),
        "g19_missing_features": list(medium_floor["missing_features"]),
        "g19_evidence": dict(medium_floor["evidence"]),
        "hints": hints,
        "medium_shape": shape,
        "medium_template": bool(verdict["中等模板"]),
        "medium_evidence": dict(verdict["中等证据"]),
        "hell_signals": hell,
        "hell_signal_count": len(hell),
        "suggested_difficulty": suggested,
        "reasons": reasons,
    }


def _justification_feature_count(note: str) -> int:
    """备注里能数出几项特征：困难三项 + G19 的三项中等特征都算。"""
    note = str(note or "")
    return sum(1 for name in NOTE_FEATURE_NAMES if name in note)


def check(
    items: list[tuple[str, str, str]],
    justifications: dict[str, str] | None = None,
    *,
    strict: bool = False,
) -> dict:
    justifications = justifications or {}
    rows: list[dict] = []
    violations: list[dict] = []
    justified: list[dict] = []
    for label, task_type, text in items:
        verdict = evaluate(text, task_type)
        row = {"label": label, "task_type": task_type, "prompt": text, **verdict}
        # 硬闸：G16 困难下限（五类任务类型都查）+ 平台已确认的 G19 UI-only 形状。
        # `--strict` 另外把 hint 当不合格，hint 里包含「单一功能模块」、一般
        # 「G19 疑似不达中等」与「硬证据只在末句」三类。
        kinds: list[str] = []
        if not row["g16_ok"]:
            kinds.append("G16")
        if row.get("g19_hard_block"):
            kinds.append("G19")
        if strict and row["hints"]:
            kinds.append("strict")
        row["violation_kinds"] = kinds
        row["gate_ok"] = not kinds
        if "strict" in kinds:
            row["reasons"] = list(row["reasons"]) + list(row["hints"])
        rows.append(row)
        if kinds:
            # G16 违规按类型要求项数（普通 2、Bugfix 3）；纯 strict 违规写两项即可。
            required = int(row["required_features"]) if "G16" in kinds else 2
            note = str(justifications.get(label, "") or "").strip()
            justification_count = _justification_feature_count(note)
            row["justification_feature_count"] = justification_count
            row["note_required_features"] = required
            platform_hard = bool(row.get("platform_learned_blocks"))
            if note and justification_count >= required and not platform_hard:
                justified.append({**row, "note": note})
            else:
                if note and platform_hard:
                    row["justification_issue"] = (
                        "命中平台真实回执提炼出的负向硬闸，不能用「难度复核」备注放行"
                    )
                elif note:
                    row["justification_issue"] = (
                        f"难度复核只写到 {justification_count} 项特征，"
                        f"{task_type or '普通类型'}至少要求 {required} 项"
                    )
                violations.append(row)
    hell = sum(1 for row in rows if row["suggested_difficulty"] == "地狱")
    iteration_repair_rows = [row for row in rows if row["g19_applies"]]
    return {
        "ok": not violations,
        "rule": RULE_G16,
        "companion_rules": [RULE_G19, RULE_G17],
        "judge": str(JUDGE_PATH),
        "strict": strict,
        "checked_count": len(rows),
        "violation_count": len(violations),
        "g16_violation_count": sum(1 for row in rows if "G16" in row["violation_kinds"]),
        "g19_hard_violation_count": sum(
            1 for row in rows if "G19" in row["violation_kinds"]
        ),
        "platform_learned_violation_count": sum(
            1 for row in rows if row.get("platform_learned_blocks")
        ),
        "strict_only_violation_count": sum(
            1 for row in rows if row["violation_kinds"] == ["strict"]
        ),
        "g16_enforced_count": len(rows),
        "g16_exempt_count": 0,
        "g16_exempt_types": [],
        "multi_module_missing_count": sum(1 for row in rows if not row["multi_module"]),
        "g19_applicable_count": len(iteration_repair_rows),
        "g19_violation_count": sum(1 for row in iteration_repair_rows if not row["g19_ok"]),
        "medium_template_count": sum(1 for row in rows if row["medium_template"]),
        "hint_count": sum(1 for row in rows if row["hints"]),
        "suggested_difficulty": {
            "困难": len(rows) - hell,
            "地狱": hell,
        },
        "violations": violations,
        "justified": justified,
        "items": rows,
        "notes": [
            "2026-10-01：撤回 2026-09-30 对 `Bug修复`（本地 `Bugfix`）与 `feature迭代`"
            "（本地 `功能优化`）的难度放宽，两类重新按 G16 困难下限硬判"
            "（`g16_enforced: true`，`g16_exempt_count: 0`）。",
            "G16：五类任务类型都要成立「多模块整合 / 关键设计取舍 / 复杂技术关注点」中的"
            "两项以上（`Bugfix` / `Bug修复` 按判据三项全备），而且至少一项要落到硬证据上"
            "（" + " / ".join(HARD_EVIDENCE_KEYS) + "）。",
            "G19（2026-10-01 平台新规则）：迭代 / 修复题最低收 `中等`，且「中等」要同时"
            "成立调用/数据流理解、实现决策、边界条件与约束三项；只成立一两项照样判废、"
            "不可返修。「实现决策」是语义判断，脚本只给近似读数：默认进 `hints` 提示；"
            "但 2026-10-07 真实回执确认的「排序分页 UI-only」「空态+导出重试 UI-only」"
            "两种形状已改为默认硬拦。",
            "平台负向硬闸（2026-10-07 新增）：跨调用点收拢成一份、单业务域新建台账、"
            "示例数据铺底/备份重试这三类，即使词面命中硬证据，也必须重做；不能用"
            "「难度复核」备注放行。",
            "多模块纪律：单一功能模块是 G16 / G19 判词里最常被点名的短板"
            "（「单一…功能」「局部到跨层」「无架构取舍」），每题都要落在 ≥2 个功能模块上；"
            "`multi_module` 字段给读数，缺了进 `hints`，`--strict` 才当不合格。",
            "平台判词点名否定过、只能当弱证据的写法：本模块概览/看板/统计数字与列表一致、"
            "以后端返回为准而前端不再重算、单独一句历史按当时口径保留、批量逐条回执、"
            "把几处判断收拢成一份。",
            "G17：题面只到困难时不要标地狱；地狱要另有两项地狱特征。",
            "G18（不在本脚本硬判范围）：当日两侧满分占比超过 10% 会整批判废；难度不够时"
            "A / B 两侧都容易拿满分，把题面做成跨模块的困难题是压低这条的主要手段。",
            "本条闸门命中即作废、不可返修，必须在出题阶段就换切入点重出，不许事后改措辞。",
            f"确实判断这条题能到「困难」时，把要求成立的特征逐项写进工作簿备注并以"
            f"「{JUSTIFICATION_PREFIX}: 」开头留档（普通类型写两项、Bugfix写三项，"
            f"G19 的三项中等特征名也算数）。脚本命中但备注复核过的条目只留痕、不拦。",
        ],
    }


def load_prompt_file(path: Path) -> list[tuple[str, str, str]]:
    """每行一条题面；兼容纯文本与 label<TAB>type<TAB>prompt。"""
    items: list[tuple[str, str, str]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        fields = text.split("\t")
        if len(fields) >= 3:
            label = fields[0].strip() or f"{path.name}#{index}"
            task_type = fields[1].strip()
            prompt = "\t".join(fields[2:]).strip()
        elif len(fields) == 2:
            label = fields[0].strip() or f"{path.name}#{index}"
            task_type = ""
            prompt = fields[1].strip()
        else:
            label = f"{path.name}#{index}"
            task_type = ""
            prompt = text
        if prompt:
            items.append((label, task_type, prompt))
    return items


def load_workbook_prompts(
    parent: Path, workbook_name: str, task_types: str
) -> tuple[list[tuple[str, str, str]], dict[str, str]]:
    records = workbook_lib.read_workbook(parent / workbook_name)
    wanted = {part.strip() for part in task_types.split(",") if part.strip()}
    apply_filter = bool(wanted) and not (wanted & ALL_TASK_TYPES)
    items: list[tuple[str, str, str]] = []
    justifications: dict[str, str] = {}
    for record in records:
        prompt = str(record.get("提示词", "") or "").strip()
        if not prompt:
            continue
        task_type = str(record.get("任务类型", "") or "").strip()
        if apply_filter and task_type not in wanted:
            continue
        label = str(record.get("子文件夹名称", "") or "未命名")
        items.append((label, task_type, prompt))
        note = str(record.get("备注", "") or "").strip()
        if note.startswith(JUSTIFICATION_PREFIX):
            justifications[label] = note
    return items, justifications


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GSB 提示词难度闸门（平台规则 G16 困难下限 / G19 中等下限 / G17 地狱上限）"
    )
    parser.add_argument("--parent", help="父目录，默认读取其中的 tasks.xlsx")
    parser.add_argument("--workbook", default=DEFAULT_WORKBOOK)
    parser.add_argument("--prompts-file", help="每行一条提示词的文本文件，与 --parent 二选一")
    parser.add_argument("--task-types", default="all", help="只检查这些任务类型，逗号分隔；all 表示不过滤")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="把 hints 也算作不合格：单一功能模块、G19 疑似不达中等、硬证据只出现在末句",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="只输出汇总与被拦条目的摘要，不打印全部逐条明细",
    )
    args = parser.parse_args()

    if args.prompts_file:
        items = load_prompt_file(Path(args.prompts_file).expanduser().resolve())
        justifications: dict[str, str] = {}
    elif args.parent:
        parent = Path(args.parent).expanduser().resolve()
        if not parent.is_dir():
            raise SystemExit(f"Parent directory does not exist: {parent}")
        items, justifications = load_workbook_prompts(parent, args.workbook, args.task_types)
    else:
        raise SystemExit("必须提供 --parent 或 --prompts-file 之一")

    result = check(items, justifications, strict=args.strict)
    if args.quiet:
        def _compact(row: dict) -> dict:
            return {
                "label": row["label"],
                "task_type": row["task_type"],
                "g16_ok": row["g16_ok"],
                "gate_ok": row["gate_ok"],
                "g16_enforced": row["g16_enforced"],
                "difficulty_policy": row["difficulty_policy"],
                "violation_kinds": row["violation_kinds"],
                "required_features": row["required_features"],
                "strong_features": [k for k, v in row["strong_features"].items() if v],
                "hard_features": row["hard_features"],
                "weak_evidence": [k for k, v in row["weak_evidence"].items() if v],
                "multi_module": row["multi_module"],
                "g19_applies": row["g19_applies"],
                "g19_ok": row["g19_ok"],
                "g19_hard_block": row.get("g19_hard_block"),
                "platform_learned_blocks": row.get("platform_learned_blocks"),
                "g19_missing_features": row["g19_missing_features"],
                "hints": row["hints"],
                "medium_template": row["medium_template"],
                "suggested_difficulty": row["suggested_difficulty"],
                "reasons": row["reasons"],
            }

        all_rows = result["items"]
        # 兼容旧字段名：2026-10-01 起不在 G16 下限范围外的类型，exempt 恒为空。
        result["exempt"] = []
        result["hint_only"] = [
            _compact(row) for row in all_rows if row["violation_kinds"] == ["strict"]
        ]
        result["items"] = [_compact(row) for row in result["violations"]]
        result["violations"] = result["items"]
        result["justified"] = [
            {**_compact(row), "note": row.get("note", "")} for row in result["justified"]
        ]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
