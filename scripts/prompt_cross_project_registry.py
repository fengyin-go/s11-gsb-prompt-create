#!/usr/bin/env python3
"""Shared cross-project prompt registry primitives.

The registry is intentionally small and file based: workbook paths are keys,
each entry pins the workbook hash/count, and all mutating readers share one
lock. The GSB checker owns prompt-similarity logic; this module only manages
the durable index that lets batches in different directories participate.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 1
ENV_REGISTRY = "GSB_CROSS_PROJECT_REGISTRY"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def default_registry_path(
    explicit: str | Path | None = None,
    *,
    fallback: str | Path | None = None,
) -> Path:
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().resolve()
    configured = os.environ.get(ENV_REGISTRY)
    if configured:
        return Path(configured).expanduser().resolve()
    if fallback is not None:
        return Path(fallback).expanduser().resolve()
    return (Path.home() / ".codex" / "gsb-cross-project-registry.json").resolve()


def empty_registry() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "registry_id": "default",
        "updated_at": "",
        "entries": {},
    }


def load_registry(path: str | Path, *, missing_ok: bool = True) -> dict[str, Any]:
    registry_path = Path(path).expanduser().resolve()
    if not registry_path.is_file():
        if missing_ok:
            return empty_registry()
        raise FileNotFoundError(f"跨项目注册表不存在：{registry_path}")
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"跨项目注册表无法读取：{registry_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"跨项目注册表顶层必须是对象：{registry_path}")
    if int(payload.get("schema_version") or 0) != SCHEMA_VERSION:
        raise ValueError(
            f"跨项目注册表 schema_version 不支持："
            f"{payload.get('schema_version')} != {SCHEMA_VERSION}"
        )
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        raise ValueError(f"跨项目注册表 entries 必须是对象：{registry_path}")
    payload.setdefault("registry_id", "default")
    payload.setdefault("updated_at", "")
    return payload


def save_registry(path: str | Path, payload: dict[str, Any]) -> Path:
    registry_path = Path(path).expanduser().resolve()
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(payload)
    data["schema_version"] = SCHEMA_VERSION
    data.setdefault("registry_id", "default")
    data["updated_at"] = now_iso()
    if not isinstance(data.get("entries"), dict):
        raise ValueError("跨项目注册表 entries 必须是对象")

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{registry_path.name}.",
        suffix=".tmp",
        dir=str(registry_path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, registry_path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)
    return registry_path


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workbook_stats(path: str | Path) -> dict[str, Any]:
    workbook = Path(path).expanduser().resolve()
    stat = workbook.stat()
    return {
        "workbook_sha256": sha256_file(workbook),
        "workbook_mtime_ns": stat.st_mtime_ns,
        "workbook_size": stat.st_size,
    }


@contextlib.contextmanager
def registry_lock(
    registry_path: str | Path,
    *,
    timeout_seconds: float = 120.0,
    poll_seconds: float = 0.1,
) -> Iterator[Path]:
    """Acquire the registry-wide lock used for check -> workbook -> registry."""
    try:
        import fcntl  # type: ignore
    except ImportError as exc:  # pragma: no cover - macOS/Linux in this workspace.
        raise RuntimeError("当前平台没有 fcntl，无法保证跨进程注册表锁") from exc

    lock_path = Path(f"{Path(registry_path).expanduser().resolve()}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"等待跨项目注册表锁超时：{lock_path}")
                time.sleep(max(0.01, float(poll_seconds)))
        yield lock_path
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def upsert_entry(
    registry: dict[str, Any],
    workbook: str | Path,
    *,
    batch_id: str = "",
    batch_dir: str | Path | None = None,
    status: str = "finalized",
    prompt_count: int = 0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workbook_path = Path(workbook).expanduser().resolve()
    key = canonical_path(workbook_path)
    entries = registry.setdefault("entries", {})
    if not isinstance(entries, dict):
        raise ValueError("跨项目注册表 entries 必须是对象")
    previous = entries.get(key) if isinstance(entries.get(key), dict) else {}
    timestamp = now_iso()
    entry: dict[str, Any] = {
        "batch_id": str(batch_id or workbook_path.parent.name),
        "batch_dir": canonical_path(batch_dir or workbook_path.parent),
        "workbook": key,
        "status": str(status or "finalized"),
        "prompt_count": int(prompt_count),
        "registered_at": str(previous.get("registered_at") or timestamp),
        "updated_at": timestamp,
    }
    entry.update(workbook_stats(workbook_path))
    if extra:
        entry.update(extra)
    entries[key] = entry
    return entry


def entry_issues(
    entry: dict[str, Any],
    *,
    expected_prompt_count: int | None = None,
    verify_hash: bool = True,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    raw_workbook = str(entry.get("workbook") or "").strip()
    if not raw_workbook:
        return [{"kind": "missing_workbook_path", "entry": entry}]
    workbook = Path(raw_workbook).expanduser()
    if not workbook.is_file():
        return [{
            "kind": "missing_workbook",
            "workbook": str(workbook),
            "batch_id": str(entry.get("batch_id") or ""),
        }]
    if verify_hash:
        expected_hash = str(entry.get("workbook_sha256") or "")
        actual_hash = sha256_file(workbook)
        if expected_hash and actual_hash != expected_hash:
            issues.append({
                "kind": "workbook_hash_mismatch",
                "workbook": str(workbook),
                "batch_id": str(entry.get("batch_id") or ""),
                "expected": expected_hash,
                "actual": actual_hash,
            })
    if expected_prompt_count is not None:
        registered = int(entry.get("prompt_count", -1))
        if registered != int(expected_prompt_count):
            issues.append({
                "kind": "prompt_count_mismatch",
                "workbook": str(workbook),
                "batch_id": str(entry.get("batch_id") or ""),
                "expected": registered,
                "actual": int(expected_prompt_count),
            })
    return issues
