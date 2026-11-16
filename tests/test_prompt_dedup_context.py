from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_prompt_dedup import check, normalize, strip_scaffolding, text_overlap  # noqa: E402
from review_contract import CONTEXT_STATEMENT  # noqa: E402


def test_context_statement_is_excluded_from_batch_dedup() -> None:
    left = f"客户分群画像数据需要按行业聚合。{CONTEXT_STATEMENT}"
    right = f"财务预算差异数据需要按科目聚合。{CONTEXT_STATEMENT}"

    assert "历史对话缓存" not in normalize(left)
    assert "本轮题面" not in normalize(right)


def test_shared_business_text_still_counts() -> None:
    shared = "同一段业务说明必须保留在查重正文"
    left = f"客户分群画像数据。{shared}。{CONTEXT_STATEMENT}"
    right = f"财务预算差异数据。{shared}。{CONTEXT_STATEMENT}"

    assert text_overlap(left, right) >= 0.2


def test_different_task_types_are_not_compared() -> None:
    shared = "同一段业务说明必须保留在查重正文并继续影响后续结果"
    items = [
        (f"a[功能开发]", f"客户分群画像数据。{shared}。{CONTEXT_STATEMENT}"),
        (f"b[Bugfix]", f"客户分群画像数据。{shared}。{CONTEXT_STATEMENT}"),
    ]

    assert check(items, 0.2)["ok"] is True


def test_required_scaffolding_is_not_business_duplication() -> None:
    shared_scaffold = (
        "结果要关联另一个入口，同时保留后续取数所需的状态。"
        "处理中断后必须从失败记录继续，重复提交只生效一次。"
        "跨模块传递中，另一个业务面读取同一份产物。"
    )
    left = f"客户画像按行业聚合。{shared_scaffold}{CONTEXT_STATEMENT}"
    right = f"财务预算按科目聚合。{shared_scaffold}{CONTEXT_STATEMENT}"

    assert "跨模块" not in strip_scaffolding(left)
    assert text_overlap(
        strip_scaffolding(left),
        strip_scaffolding(right),
    ) < 0.2
