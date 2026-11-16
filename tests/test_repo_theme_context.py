from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_repo_theme import classify  # noqa: E402
from review_contract import CONTEXT_STATEMENT  # noqa: E402


def test_context_statement_is_excluded_but_business_clause_is_fingerprinted() -> None:
    shared_business = "共同业务从句必须保留在指纹统计中"
    rows = [
        classify("a", f"客户分群数据需要校验。{shared_business}。{CONTEXT_STATEMENT}"),
        classify("b", f"财务预算数据需要聚合。{shared_business}。{CONTEXT_STATEMENT}"),
        classify("c", f"预测结果数据需要回填。{shared_business}。{CONTEXT_STATEMENT}"),
    ]

    context_grams = {
        "只依据当前仓",
        "不读取其他会",
        "历史对话缓存",
    }
    assert all(not (context_grams & set(row["ngrams"])) for row in rows)

    shared_grams = {
        shared_business[index:index + 6]
        for index in range(len(shared_business) - 5)
    }
    assert all(shared_grams & set(row["ngrams"]) for row in rows)


def test_required_open_choice_clause_is_excluded_from_fingerprint() -> None:
    row = classify(
        "choice",
        f"客户分群结果需要按画像编号追溯。新旧标签冲突时由你决定采用哪份；"
        f"但缓存只能看到完整画像。{CONTEXT_STATEMENT}",
    )

    assert "由你决定采用" not in "".join(row["ngrams"])
