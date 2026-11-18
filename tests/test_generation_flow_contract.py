from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def test_semantic_topic_map_example_tracks_current_contract() -> None:
    from difficulty_contract import H3_FALSE_GUARDS

    example = (ROOT / "references" / "semantic-v2-topic-map.md").read_text(
        encoding="utf-8"
    )
    assert '"workload_design"' in example
    assert '"dedup_contract_version": 1' in example
    assert '"semantic_identity"' in example
    assert '"semantic_contrast"' in example
    for guard in H3_FALSE_GUARDS:
        assert f'"{guard}"' in example


def test_default_prompt_generation_has_no_user_confirmation_gate() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    rules = (ROOT / "references" / "generation-rules.md").read_text(encoding="utf-8")
    hardening = (ROOT / "references" / "difficulty-hardening.md").read_text(
        encoding="utf-8"
    )
    combined = "\n".join((skill, rules, hardening))

    assert "默认出题流程不设置用户确认节点" in skill
    assert "通过后直接生成\n与有效容量一致的正式题位" in skill
    assert "样稿自动准入" in skill
    assert "通过后直接进入正式生成，不等待用户确认" in rules
    assert "自动准入通过后直接进入正式写表，不等待用户确认" in hardening

    stale_gate_phrases = (
        "经用户确认表达，再生成正式",
        "才能作为候选提示词发用户确认",
        "未经确认不得改正式工作簿",
        "不得留到用户确认文风后再补",
        "确认后仍须执行全部正式闸门",
    )
    for phrase in stale_gate_phrases:
        assert phrase not in combined


def test_explicit_preview_request_is_the_only_preview_stop() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "只有用户明确要求“只先看预览”时" in skill
    assert "不需要再次询问是否可以继续" in skill
