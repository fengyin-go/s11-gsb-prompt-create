"""Resolve this isolated S11 helper set and the shared G16 judge."""
from __future__ import annotations
import os
from pathlib import Path

CORE_MODULES = (
    "batch_prompt_workbook.py", "check_repo_theme.py", "check_prompt_dedup.py",
    "check_prompt_difficulty.py", "repo_topic_policy.py", "prompt_cross_project_registry.py",
    "s11_policy.py",
)


def solo_create_scripts(required="batch_prompt_workbook.py") -> Path:
    # Never silently fall back to old solo-create task labels or allocation policy.
    local = Path(__file__).resolve().parent
    missing = [name for name in (*CORE_MODULES, required) if not (local / name).is_file()]
    if missing:
        raise SystemExit(f"S11 helper 副本不完整: {missing}")
    return local


def _gsb_create_candidates():
    codex_dir = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    candidates = []
    if os.environ.get("GSB_CC_GSB_CREATE_SCRIPTS"):
        candidates.append(Path(os.environ["GSB_CC_GSB_CREATE_SCRIPTS"]).expanduser())
    candidates.extend((codex_dir / "skills/cc-gsb-create/scripts",
                       Path(__file__).resolve().parents[2] / "cc-gsb-create/scripts"))
    return tuple(dict.fromkeys(candidates))


def gsb_create_scripts(required="gsb_difficulty.py") -> Path:
    for path in _gsb_create_candidates():
        if (path / required).is_file():
            return path
    raise SystemExit(f"缺少共享 G16/G17 判据 {required}，请安装同级 cc-gsb-create")
