from __future__ import annotations

import json
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import batch_prompt_workbook as workbook  # noqa: E402
import check_prompt_dedup as dedup  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "platform-a5-cc10011-20261116.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def unique_cjk(seed: int, length: int) -> str:
    return "".join(chr(seed + index) for index in range(length))


def test_cc10011_ten_structural_equivalents_are_all_blocked() -> None:
    fixture = load_fixture()
    records = fixture["records"]
    assert [record["platform_id"] for record in records] == [
        "8291",
        "8299",
        "8307",
        "8341",
        "8357",
        "8359",
        "8361",
        "8371",
        "8393",
        "8398",
    ]
    for index, record in enumerate(records):
        peer = records[(index + 1) % len(records)]
        result = dedup.check_raw_a5(
            record["synthetic_prompt"],
            peer["synthetic_prompt"],
        )
        assert result["ok"] is False, record["platform_id"]
        assert result["longest_common_fragment_length"] >= 40
        assert result["window_hit_count"] >= 1
        assert "raw_a5_platform_window" in {
            violation["kind"] for violation in result["violations"]
        }


def test_seven_to_eighteen_percent_similarity_still_fails_on_shared_chain() -> None:
    fixture = load_fixture()
    shared = fixture["synthetic_shared_domain_chain"]
    left = shared + unique_cjk(0x4E00, 300)
    right = shared + unique_cjk(0x6E00, 300)
    similarity = SequenceMatcher(None, left, right).ratio()

    result = dedup.check_raw_a5(left, right)

    assert 0.07 <= similarity <= 0.18, similarity
    assert result["ok"] is False
    assert result["longest_common_fragment_length"] >= 40
    assert result["window_hit_count"] >= 1


def test_internal_domain_chain_repeat_fails() -> None:
    chain = load_fixture()["synthetic_shared_domain_chain"]
    prompt = f"客户分群画像项目。{chain}。后文再次要求：{chain}。"

    result = dedup.check_raw_a5(prompt, "完全不同的库存盘点需求。")

    assert result["ok"] is False
    assert result["max_internal_repeat_length"] >= 20
    assert "raw_a5_internal_repeat" in {
        violation["kind"] for violation in result["violations"]
    }


@pytest.mark.parametrize(
    ("length", "expected_ok"),
    [
        (19, True),
        (20, False),
    ],
)
def test_common_fragment_threshold_is_twenty(length: int, expected_ok: bool) -> None:
    source = "天地玄黄宇宙洪荒日月盈昃辰宿列张寒来暑往秋收冬藏"
    shared = source[:length]
    left = shared + unique_cjk(0x4F00, 30)
    right = shared + unique_cjk(0x5F00, 30)

    result = dedup.check_raw_a5(left, right)

    assert result["longest_common_fragment_length"] == length
    assert result["window_hit_count"] == 0
    assert result["ok"] is expected_ok


def test_confirmed_a5_fragment_blocks_even_without_pair_fragment() -> None:
    chain = load_fixture()["synthetic_shared_domain_chain"]
    candidate = f"光伏档案导出。{chain}。"

    result = dedup.check_raw_a5(
        candidate,
        "检测样品接收与复核。",
        confirmed_a5_fragments=[chain],
    )

    assert result["ok"] is False
    assert result["confirmed_fragment_hits"]
    assert "raw_a5_confirmed_fragment" in {
        violation["kind"] for violation in result["violations"]
    }


def test_scaffolding_words_are_not_whitelisted() -> None:
    shared = (
        "跨模块同步兼容已有数据格式并在中断后恢复，重复提交只保留一次，"
        "另一个业务入口同步消费同一份数据快照。"
    )
    left = f"客户画像按行业聚合。{shared}"
    right = f"预算差异按科目汇总。{shared}"

    assert shared not in dedup.strip_scaffolding(left)
    result = dedup.check_raw_a5(left, right)
    assert result["ok"] is False
    assert result["longest_common_fragment_length"] >= 20


def test_b1115_optimization_tail_is_blocked() -> None:
    shared = (
        "按现有处理链路修正受影响字段，沿用既有字段，"
        "竞态请求只允许一项生效。验收样例："
    )
    left = "优化现有流数据源批量暂停。失败分区保留可续位置。" + shared + "STRE-0001。"
    right = "优化现有指标采集器批量暂停。失败采集器保留续做位置。" + shared + "METR-0001。"

    result = dedup.check_raw_a5(left, right)

    assert result["ok"] is False
    assert result["longest_common_fragment_length"] >= 20
    assert "raw_a5_common_fragment" in {
        violation["kind"] for violation in result["violations"]
    }


def test_b1115_bugfix_tail_is_blocked() -> None:
    shared = (
        "顺着当前取数链路改正异常版本，沿用历史快照，"
        "并发处理只接受一个终态。验收样例："
    )
    left = "输出配置关闭后状态回退，写入参数丢失。" + shared + "SINK-0002。"
    right = "日志归档游标迁移后重复读取，断点错位。" + shared + "LOG-0002。"

    result = dedup.check_raw_a5(left, right)

    assert result["ok"] is False
    assert result["longest_common_fragment_length"] >= 20


def test_ordinary_domain_terms_without_long_fragment_pass() -> None:
    left = "采购单审核页面需要显示供应商名称、采购单编号和入库仓库。"
    right = "供应商名称用于收货核对，仓库人员负责采购单审核。"

    result = dedup.check_raw_a5(left, right)

    assert result["ok"] is True
    assert result["longest_common_fragment_length"] < 20
    assert result["window_hit_count"] == 0


def test_existing_legal_clean_control_still_passes_batch_check() -> None:
    control = load_fixture()["clean_controls"][0]
    result = dedup.check([(control["label"], control["prompt"])], 0.2)

    assert result["ok"] is True, result["violations"]
    assert result["raw_a5_ok"] is True
    assert result["raw_a5_max_common_fragment_length"] == 0
    assert result["raw_a5_max_internal_repeat_length"] <= 19


def test_existing_qc_passed_corpus_has_no_raw_a5_false_positives() -> None:
    corpus_path = ROOT / "tests" / "fixtures" / "g16-corpus.json"
    payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    passed = [
        case for case in payload["cases"]
        if case.get("平台结论") == "质检通过"
    ]
    items = [
        (
            f"{case.get('子文件夹名称')}[{case.get('任务类型')}]",
            case.get("提示词", ""),
        )
        for case in passed
    ]

    result = dedup.check(items, 0.2)

    assert len(passed) == 33
    assert result["raw_a5_ok"] is True, result["raw_a5_violations"]
    assert result["raw_a5_violation_count"] == 0
    assert result["raw_a5_window_hits"] == 0
    assert result["raw_a5_max_common_fragment_length"] <= 19
    assert result["raw_a5_max_internal_repeat_length"] <= 19


def test_write_gate_surfaces_raw_a5_without_writing_workbook() -> None:
    fixture = load_fixture()
    records = [
        {
            "子文件夹名称": "repo-01-development",
            "任务类型": "功能开发",
            "提示词": fixture["records"][0]["synthetic_prompt"],
        },
        {
            "子文件夹名称": "repo-02-bugfix",
            "任务类型": "Bugfix",
            "提示词": fixture["records"][1]["synthetic_prompt"],
        },
    ]
    with tempfile.TemporaryDirectory(prefix="s11-raw-a5-gate-") as temp:
        parent = Path(temp)
        with (
            patch("check_repo_theme.check", return_value={"ok": True, "violations": []}),
            patch("check_prompt_difficulty.check", return_value={"ok": True, "violations": []}),
            patch("check_prompt_naturalness.check", return_value={"ok": True, "errors": []}),
            patch("prompt_style.prompt_errors", return_value=[]),
            patch("domain_fit.map_errors", return_value=[]),
            patch("domain_fit.prompt_errors", return_value=[]),
            patch("check_prompt_type_intent.prompt_errors", return_value=[]),
            patch("difficulty_contract.prompt_errors", return_value=[]),
        ):
            result = workbook.run_write_gates(
                parent,
                records,
                repo=None,
                topic_map={},
            )

    assert result["ok"] is False
    assert result["dedup"]["raw_a5_ok"] is False
    assert result["dedup"]["raw_a5_violation_count"] > 0
    assert any("raw_a5" in problem for problem in result["problems"])
    assert not (parent / workbook.DEFAULT_WORKBOOK).exists()


def test_generation_manifest_records_raw_a5_fields() -> None:
    with tempfile.TemporaryDirectory(prefix="s11-raw-a5-manifest-") as temp:
        parent = Path(temp)
        workbook_path = parent / "tasks.xlsx"
        workbook_path.write_bytes(b"temporary manifest fixture")
        gate = {
            "gate_version": "fixture",
            "ok": True,
            "dedup": {
                "ok": True,
                "raw_a5_ok": True,
                "raw_a5_violation_count": 0,
                "raw_a5_max_common_fragment_length": 19,
                "raw_a5_max_internal_repeat_length": 19,
                "raw_a5_window_hits": 0,
                "raw_a5_nearest_pairs": [],
            },
        }

        path = workbook.write_generation_manifest(
            parent,
            workbook=workbook_path,
            repo=None,
            gate=gate,
            skill={},
        )
        manifest = json.loads(path.read_text(encoding="utf-8"))

    assert manifest["raw_a5_ok"] is True
    assert manifest["raw_a5_violation_count"] == 0
    assert manifest["raw_a5_max_common_fragment_length"] == 19
    assert manifest["raw_a5_max_internal_repeat_length"] == 19
    assert manifest["raw_a5_window_hits"] == 0
    assert manifest["raw_a5_nearest_pairs"] == []


def test_raw_normalization_keeps_headings_and_context_statements() -> None:
    text = "# H1 标题\n兼容已有数据。中断后恢复。同步消费。"
    normalized = dedup.normalize_raw(text)

    assert "h1标题" in normalized
    assert "兼容已有数据" in normalized
    assert "中断后恢复" in normalized
    assert "同步消费" in normalized
