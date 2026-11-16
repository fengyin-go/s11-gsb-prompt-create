#!/usr/bin/env python3
"""Freeze an arbitrary local source directory into a deterministic Git snapshot."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = 1
SNAPSHOT_NAME = "source-snapshot"
MANIFEST_NAME = "source-snapshot.json"
DEFAULT_GIT_USER_NAME = "S11 Source Snapshot"
DEFAULT_GIT_USER_EMAIL = "s11-source-snapshot@local"
FIXED_COMMIT_DATE = "2000-01-01T00:00:00Z"
MANAGED_IGNORE_BEGIN = "# BEGIN S11 LOCAL SNAPSHOT EXCLUSIONS"
MANAGED_IGNORE_END = "# END S11 LOCAL SNAPSHOT EXCLUSIONS"
DEFAULT_EXCLUDES = (
    "**/.git/**",
    "**/.hg/**",
    "**/.svn/**",
    "**/.DS_Store",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/.pytest_cache/**",
    "**/.mypy_cache/**",
    "**/.ruff_cache/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/.tox/**",
    "**/.nox/**",
    "**/.gradle/**",
    "**/.env",
    "**/.env.*",
)
ALWAYS_EXCLUDES = ("**/.env", "**/.env.*")
IGNORE_EXCEPTIONS = ("**/.env.example", ".env.example")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,90}$")


def _json_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def read_manifest(path: str | Path) -> dict:
    path = Path(path).expanduser().resolve()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取源码快照清单 {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"源码快照清单版本无效: {path}")
    return value


def source_name_from_path(path: Path, requested: str | None = None) -> str:
    raw = str(requested or Path(path).resolve().name).strip()
    if NAME_RE.fullmatch(raw):
        return raw
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip(".-_")
    if not normalized or not NAME_RE.fullmatch(normalized):
        raise ValueError(
            "source-name 必须由字母、数字、点、下划线或连字符组成，且长度不超过 91"
        )
    return normalized


def _relative_text(path: Path, root: Path) -> str:
    try:
        return Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(root))).as_posix()
    except ValueError as exc:
        raise ValueError(f"路径逃逸源码目录: {path}") from exc


def _safe_relative(raw: str) -> str:
    value = str(raw or "").strip().replace("\\", "/").strip("/")
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"exclude 必须是源码目录内的相对路径或 glob: {raw}")
    return path.as_posix()


def _matches(rel: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        variants = [pattern]
        if pattern.startswith("**/"):
            variants.append(pattern[3:])
        if any(
            fnmatch.fnmatch(rel, variant) or fnmatch.fnmatch("/" + rel, variant)
            for variant in variants
        ):
            return True
    return False


def _git(source: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=source,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def _git_output(source: Path, *args: str) -> str:
    result = _git(source, *args)
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"git {' '.join(args)} 失败")
    return result.stdout.strip()


def _git_file_list(source: Path) -> tuple[list[Path], set[str]]:
    """Return candidate files and tracked paths; Git sources respect ignore rules."""
    probe = _git(source, "rev-parse", "--is-inside-work-tree")
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        files: list[Path] = []
        for root, dirs, names in os.walk(source, followlinks=False):
            dirs.sort()
            names.sort()
            for name in [*dirs, *names]:
                path = Path(root) / name
                if path.is_file() or path.is_symlink():
                    files.append(path)
        files.sort(key=lambda path: path.as_posix())
        return files, set()
    listed = _git(
        source, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
    )
    if listed.returncode:
        raise ValueError(listed.stderr.strip() or "无法读取本地源码文件列表")
    files = [source / raw for raw in listed.stdout.split("\0") if raw]
    tracked_out = _git(source, "ls-files", "-z", "--cached")
    if tracked_out.returncode:
        raise ValueError(tracked_out.stderr.strip() or "无法读取 Git 跟踪文件")
    tracked = {raw for raw in tracked_out.stdout.split("\0") if raw}
    return files, tracked


def _safe_symlink(path: Path, source: Path) -> None:
    if not path.is_symlink():
        return
    target = path.resolve()
    if not target.exists():
        raise ValueError(f"源码快照拒绝失效的符号链接: {path}")
    try:
        target.relative_to(source.resolve())
    except ValueError as exc:
        raise ValueError(f"源码快照拒绝指向目录外部的符号链接: {path} -> {target}") from exc


def _append_managed_ignores(snapshot: Path, patterns: Iterable[str]) -> bool:
    values = list(dict.fromkeys(pattern for pattern in patterns if pattern))
    if not values:
        return False
    target = snapshot / ".gitignore"
    original = target.read_text(encoding="utf-8") if target.is_file() else ""
    managed = "\n".join((MANAGED_IGNORE_BEGIN, *values, MANAGED_IGNORE_END))
    marker = re.compile(
        rf"\n?{re.escape(MANAGED_IGNORE_BEGIN)}.*?{re.escape(MANAGED_IGNORE_END)}\n?",
        re.DOTALL,
    )
    if marker.search(original):
        updated = marker.sub(f"\n{managed}\n", original).strip() + "\n"
    else:
        prefix = original.rstrip()
        updated = (prefix + "\n\n" if prefix else "") + managed + "\n"
    if updated != original:
        target.write_text(updated, encoding="utf-8")
        return True
    return False


def _ignore_patterns(excludes: Iterable[str]) -> list[str]:
    patterns = list(DEFAULT_EXCLUDES)
    for raw in excludes:
        rel = _safe_relative(raw)
        patterns.append(f"/{rel}/" if "." not in Path(rel).name else f"/{rel}")
    patterns.extend("!" + pattern for pattern in IGNORE_EXCEPTIONS)
    return patterns


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _commit_snapshot(snapshot: Path) -> tuple[str, str]:
    init = _git(snapshot, "init", "-q", "-b", "main")
    if init.returncode:
        raise ValueError(init.stderr.strip() or "初始化源码快照 Git 仓库失败")
    added = _git(snapshot, "add", "-A")
    if added.returncode:
        raise ValueError(added.stderr.strip() or "暂存源码快照失败")
    if not _git_output(snapshot, "diff", "--cached", "--name-only"):
        raise ValueError("源码快照没有可提交文件")
    env = {
        "GIT_AUTHOR_NAME": DEFAULT_GIT_USER_NAME,
        "GIT_AUTHOR_EMAIL": DEFAULT_GIT_USER_EMAIL,
        "GIT_AUTHOR_DATE": FIXED_COMMIT_DATE,
        "GIT_COMMITTER_NAME": DEFAULT_GIT_USER_NAME,
        "GIT_COMMITTER_EMAIL": DEFAULT_GIT_USER_EMAIL,
        "GIT_COMMITTER_DATE": FIXED_COMMIT_DATE,
    }
    committed = _git(snapshot, "commit", "-q", "-m", "S11 source snapshot", env=env)
    if committed.returncode:
        raise ValueError(committed.stderr.strip() or "提交源码快照失败")
    commit = _git_output(snapshot, "rev-parse", "HEAD")
    tree = _git_output(snapshot, "rev-parse", "HEAD^{tree}")
    return commit, tree


def _source_files_for_manifest(snapshot: Path) -> list[dict[str, object]]:
    listing = _git(snapshot, "ls-files", "-z")
    if listing.returncode:
        raise ValueError(listing.stderr.strip() or "无法读取快照文件清单")
    files = []
    for raw in sorted(item for item in listing.stdout.split("\0") if item):
        path = snapshot / raw
        if not path.is_file() and not path.is_symlink():
            continue
        is_link = path.is_symlink()
        mode = "120000" if is_link else (
            "100755" if stat.S_IMODE(path.lstat().st_mode) & stat.S_IXUSR else "100644")
        files.append({
            "path": raw,
            "mode": mode,
            "sha256": hashlib.sha256(os.readlink(path).encode("utf-8")).hexdigest()
            if is_link else _sha256(path),
        })
    return files


def _snapshot_id(files: list[dict[str, object]]) -> str:
    payload = "\n".join(
        f"{item['path']}\0{item['mode']}\0{item['sha256']}" for item in files
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def prepare_source_snapshot(
    source_dir: str | Path,
    batch_dir: str | Path,
    *,
    source_name: str | None = None,
    excludes: Iterable[str] = (),
) -> dict:
    source = Path(source_dir).expanduser().resolve()
    batch = Path(batch_dir).expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"源码目录不存在: {source}")
    snapshot = batch / SNAPSHOT_NAME
    manifest_path = batch / MANIFEST_NAME
    if snapshot.exists():
        raise ValueError(f"源码快照目录已存在，不覆盖: {snapshot}")
    if manifest_path.exists():
        raise ValueError(f"源码快照清单已存在，不覆盖: {manifest_path}")
    try:
        snapshot.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("源码快照目录不能位于源码目录内部；请把 batch 放到源码目录之外")

    name = source_name_from_path(source, source_name)
    explicit = [_safe_relative(raw) for raw in excludes]
    patterns = _ignore_patterns(explicit)
    candidates, tracked = _git_file_list(source)
    selected: list[Path] = []
    excluded: set[str] = set()
    for path in candidates:
        if not path.exists() and not path.is_symlink():
            continue
        rel = _relative_text(path, source)
        _safe_symlink(path, source)
        if any(fnmatch.fnmatch(rel, item) for item in explicit):
            excluded.add(rel)
            continue
        if _matches(rel, DEFAULT_EXCLUDES):
            allowed_exception = _matches(rel, IGNORE_EXCEPTIONS)
            if not allowed_exception and (rel not in tracked or _matches(rel, ALWAYS_EXCLUDES)):
                excluded.add(rel)
                continue
        selected.append(path)

    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.mkdir()
    try:
        for path in selected:
            rel = path.relative_to(source)
            target = snapshot / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                target.symlink_to(os.readlink(path))
            else:
                shutil.copy2(path, target, follow_symlinks=False)
        _append_managed_ignores(snapshot, patterns)
        commit, tree = _commit_snapshot(snapshot)
        files = _source_files_for_manifest(snapshot)
        snapshot_id = _snapshot_id(files)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "source_mode": "directory_snapshot",
            "source_name": name,
            "source_dir": str(source),
            "snapshot_dir": str(snapshot),
            "snapshot_commit": commit,
            "snapshot_tree": tree,
            "snapshot_id": snapshot_id,
            "file_count": len(files),
            "files": files,
            "excluded_patterns": patterns,
            "excluded_path_count": len(excluded),
        }
        _json_write(manifest_path, manifest)
    except Exception:
        shutil.rmtree(snapshot, ignore_errors=True)
        manifest_path.unlink(missing_ok=True)
        raise
    return {**manifest, "snapshot_manifest": str(manifest_path)}


def validate_snapshot(source: str | Path, manifest_path: str | Path) -> dict:
    source = Path(source).expanduser().resolve()
    manifest = read_manifest(manifest_path)
    if manifest.get("source_mode") != "directory_snapshot":
        raise ValueError("源码快照清单 source_mode 必须为 directory_snapshot")
    if Path(str(manifest.get("snapshot_dir") or "")).expanduser().resolve() != source:
        raise ValueError("--source 必须指向 source-snapshot.json 记录的 snapshot_dir")
    if not (source / ".git").is_dir():
        raise ValueError("源码快照缺少独立 .git")
    commit = _git_output(source, "rev-parse", "HEAD")
    if commit != manifest.get("snapshot_commit"):
        raise ValueError("源码快照 HEAD 与 snapshot_commit 不一致")
    if _git_output(source, "rev-parse", "HEAD^{tree}") != manifest.get("snapshot_tree"):
        raise ValueError("源码快照 tree 与 snapshot_tree 不一致")
    if _git_output(source, "status", "--porcelain"):
        raise ValueError("源码快照有未提交改动；冻结后只能新增被忽略的依赖目录")
    if _git_output(source, "symbolic-ref", "--short", "HEAD") != "main":
        raise ValueError("源码快照必须位于 main")
    current_files = _source_files_for_manifest(source)
    if int(manifest.get("file_count") or -1) != len(current_files):
        raise ValueError("源码快照文件数量与清单不一致")
    if manifest.get("files") != current_files:
        raise ValueError("源码快照文件摘要与清单不一致")
    if manifest.get("snapshot_id") != _snapshot_id(current_files):
        raise ValueError("源码快照 snapshot_id 与文件摘要不一致")
    if not NAME_RE.fullmatch(str(manifest.get("source_name") or "")):
        raise ValueError("源码快照 source_name 无效")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="普通本地源码目录，不要求是 Git 仓库")
    parser.add_argument("--batch", required=True, help="批次目录，快照写入 <batch>/source-snapshot")
    parser.add_argument("--name", help="GitHub 任务仓库名前缀；默认取源码目录名")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="额外排除的源码内相对路径或 glob，可重复传入",
    )
    args = parser.parse_args()
    try:
        result = prepare_source_snapshot(
            args.source,
            args.batch,
            source_name=args.name,
            excludes=args.exclude,
        )
    except Exception as exc:  # noqa: BLE001 - CLI returns structured failure.
        print(json.dumps({"ok": False, "blocking_reason": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1) from exc
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
