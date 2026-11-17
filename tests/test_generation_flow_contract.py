from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_default_prompt_generation_has_no_user_confirmation_gate() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    rules = (ROOT / "references" / "generation-rules.md").read_text(encoding="utf-8")
    hardening = (ROOT / "references" / "difficulty-hardening.md").read_text(
        encoding="utf-8"
    )
    combined = "\n".join((skill, rules, hardening))

    assert "默认出题流程不设置用户确认节点" in skill
    assert "不等待用户确认；通过后直接生成正式 36 条" in skill
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
