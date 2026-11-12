#!/usr/bin/env python3
"""Discover and register GSB prompt workbooks across directory trees."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from solo_paths import solo_create_scripts  # noqa: E402

SOLO_CREATE_SCRIPTS = solo_create_scripts("batch_prompt_workbook.py")
if str(SOLO_CREATE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SOLO_CREATE_SCRIPTS))

import batch_prompt_workbook as workbook_lib  # noqa: E402
import prompt_cross_project_registry as registry_lib  # noqa: E402


DEFAULT_WORKBOOK = "tasks.xlsx"
DEFAULT_REGISTRY = SCRIPT_DIR.parent / "data" / "cross-project-registry.json"
DEFAULT_CONFIG_NAME = "gsb-cross-project.json"
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".tox",
    ".nox",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    ".gsb-migration-backup",
    ".gsb-runtime",
}


def prompt_count(path: Path) -> tuple[int, int]:
    records = workbook_lib.read_workbook(path)
    prompts = sum(1 for record in records if str(record.get("提示词") or "").strip())
    return len(records), prompts


def discover_workbooks(
    roots: list[Path],
    *,
    workbook_name: str,
    max_depth: int,
) -> tuple[list[Path], list[dict[str, str]]]:
    found: dict[str, Path] = {}
    problems: list[dict[str, str]] = []
    for raw_root in roots:
        root = raw_root.expanduser().resolve()
        if not root.is_dir():
            problems.append({"kind": "missing_scan_root", "path": str(root)})
            continue
        for current, dirs, files in os.walk(root):
            current_path = Path(current)
            try:
                depth = len(current_path.relative_to(root).parts)
            except ValueError:
                depth = 0
            dirs[:] = sorted(
                name
                for name in dirs
                if name not in SKIP_DIRS and not name.startswith(".gsb-")
            )
            if workbook_name in files:
                workbook = (current_path / workbook_name).resolve()
                found.setdefault(registry_lib.canonical_path(workbook), workbook)
            if depth >= max_depth:
                dirs[:] = []
    return sorted(found.values(), key=lambda item: str(item)), problems


def build_entries(
    workbooks: list[Path],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    entries: list[dict[str, object]] = []
    batch_ids: Counter[str] = Counter()
    for workbook in workbooks:
        row_count, prompts = prompt_count(workbook)
        batch_id = workbook.parent.name
        batch_ids[batch_id] += 1
        stats = registry_lib.workbook_stats(workbook)
        entries.append({
            "batch_id": batch_id,
            "batch_dir": str(workbook.parent.resolve()),
            "workbook": str(workbook.resolve()),
            "status": "finalized" if prompts else "empty",
            "row_count": row_count,
            "prompt_count": prompts,
            **stats,
        })
    duplicate_ids = [
        {"kind": "duplicate_batch_id", "batch_id": batch_id, "count": count}
        for batch_id, count in sorted(batch_ids.items())
        if count > 1
    ]
    return entries, duplicate_ids


def register_entries(
    registry_path: Path,
    entries: list[dict[str, object]],
) -> dict[str, object]:
    with registry_lib.registry_lock(registry_path):
        registry = registry_lib.load_registry(registry_path)
        for item in entries:
            entry = registry_lib.upsert_entry(
                registry,
                str(item["workbook"]),
                batch_id=str(item["batch_id"]),
                batch_dir=str(item["batch_dir"]),
                status=str(item["status"]),
                prompt_count=int(item["prompt_count"]),
                extra={"row_count": int(item.get("row_count") or 0)},
            )
            item.update({
                "registered": True,
                "workbook_sha256": entry.get("workbook_sha256"),
                "workbook_mtime_ns": entry.get("workbook_mtime_ns"),
            })
        saved = registry_lib.save_registry(registry_path, registry)
    return {
        "registry_path": str(saved),
        "registered_count": len(entries),
        "entry_count": len(registry.get("entries") or {}),
    }


def write_batch_config(
    batch_dir: Path,
    *,
    registry_path: Path,
    checker_path: Path,
) -> bool:
    path = batch_dir / DEFAULT_CONFIG_NAME
    if path.exists():
        return False
    payload = {
        "registry": str(registry_path),
        "checker": str(checker_path),
        "require_registry": True,
        "require_coverage": True,
        "min_peer_batches": 1,
        "bootstrap_no_history": False,
        "root_fallback": False,
        "lock_timeout_seconds": 120,
        "timeout_seconds": 300,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover and register GSB prompt workbooks across directory trees."
    )
    parser.add_argument(
        "--scan-root",
        action="append",
        required=True,
        help="要扫描的根目录，可重复传入",
    )
    parser.add_argument("--workbook", default=DEFAULT_WORKBOOK, help="批次工作簿文件名")
    parser.add_argument(
        "--registry",
        help=f"跨项目注册表 JSON 路径；默认 {DEFAULT_REGISTRY}",
    )
    parser.add_argument("--max-depth", type=int, default=8, help="相对扫描根的最大递归深度")
    parser.add_argument(
        "--register",
        action="store_true",
        help="真实写入注册表；默认只做 dry-run",
    )
    parser.add_argument(
        "--write-batch-config",
        action="store_true",
        help="登记时给每个批次补 gsb-cross-project.json（只补缺失，不覆盖）",
    )
    args = parser.parse_args()

    roots = [Path(item) for item in args.scan_root]
    registry_path = registry_lib.default_registry_path(
        args.registry,
        fallback=DEFAULT_REGISTRY,
    )
    workbooks, scan_problems = discover_workbooks(
        roots,
        workbook_name=args.workbook,
        max_depth=max(0, args.max_depth),
    )
    entries, duplicate_ids = build_entries(workbooks)
    registration: dict[str, object] = {}
    configs_written = 0
    configs_existing = 0
    if args.register:
        registration = register_entries(registry_path, entries)
        if args.write_batch_config:
            checker_path = SCRIPT_DIR / "check_cross_project_prompts.py"
            for item in entries:
                written = write_batch_config(
                    Path(str(item["batch_dir"])),
                    registry_path=registry_path,
                    checker_path=checker_path,
                )
                if written:
                    configs_written += 1
                else:
                    configs_existing += 1
    result = {
        "ok": not scan_problems,
        "dry_run": not args.register,
        "registry": str(registry_path),
        "scan_roots": [str(path.expanduser().resolve()) for path in roots],
        "workbook_name": args.workbook,
        "discovered_count": len(workbooks),
        "registered_count": int(registration.get("registered_count") or 0),
        "registry_entry_count": int(registration.get("entry_count") or 0),
        "batch_configs_written": configs_written,
        "batch_configs_existing": configs_existing,
        "scan_problems": scan_problems,
        "duplicate_batch_ids": duplicate_ids,
        "entries": entries,
        "registration": registration,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
