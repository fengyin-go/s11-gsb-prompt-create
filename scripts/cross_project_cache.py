#!/usr/bin/env python3
"""Content-addressed cache for cross-project prompt pair comparisons."""

from __future__ import annotations

import copy
import contextlib
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


CACHE_SCHEMA_VERSION = 1
CACHE_ENGINE_VERSION = "s11-cross-project-cache-v1"
ENV_CACHE_DIR = "S11_CROSS_PROJECT_CACHE_DIR"


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def corpus_fingerprint(paths: list[str | Path], *, extra: dict[str, Any] | None = None) -> str:
    """Hash every corpus input that can change a cached pair result."""
    materials: list[dict[str, str]] = []
    for raw_path in sorted({str(Path(path).expanduser().resolve()) for path in paths}):
        path = Path(raw_path)
        materials.append({
            "path": raw_path,
            "sha256": file_sha256(path) if path.is_file() else "missing",
        })
    payload = {
        "engine": CACHE_ENGINE_VERSION,
        "materials": materials,
        "extra": extra or {},
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def default_cache_path(batch_dir: str | Path) -> Path | None:
    raw = os.environ.get(ENV_CACHE_DIR)
    if raw is not None and raw.strip().lower() in {"", "0", "off", "none", "false", "no"}:
        return None
    base = (
        Path(raw).expanduser()
        if raw and raw.strip()
        else Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        / "s11-gsb-prompt-create"
    )
    batch_key = hashlib.sha256(
        str(Path(batch_dir).expanduser().resolve()).encode("utf-8")
    ).hexdigest()[:20]
    return (base / "cross-project" / batch_key / "cache-v1.json").resolve()


def _item_signature(item: dict[str, Any]) -> dict[str, str]:
    return {
        "task_type": str(item.get("task_type") or ""),
        "prompt": str(item.get("prompt") or ""),
    }


class CrossProjectCache:
    """Small JSON cache scoped to one batch and one frozen corpus fingerprint."""

    def __init__(
        self,
        path: str | Path | None,
        *,
        corpus_digest: str,
    ) -> None:
        self.path = Path(path).expanduser().resolve() if path else None
        self.corpus_digest = str(corpus_digest)
        self.entries: dict[str, Any] = {}
        self.stats: dict[str, Any] = {
            "enabled": self.path is not None,
            "path": str(self.path) if self.path else "",
            "corpus_fingerprint": self.corpus_digest,
            "loaded_entries": 0,
            "hits": 0,
            "misses": 0,
            "writes": 0,
            "invalidated": False,
            "load_error": "",
        }
        self._dirty = False
        if self.path is not None:
            self._load()

    def _load(self) -> None:
        assert self.path is not None
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or int(payload.get("schema_version") or 0) != CACHE_SCHEMA_VERSION
                or payload.get("engine_version") != CACHE_ENGINE_VERSION
                or payload.get("corpus_fingerprint") != self.corpus_digest
                or not isinstance(payload.get("entries"), dict)
            ):
                self.stats["invalidated"] = True
                return
            self.entries = payload["entries"]
            self.stats["loaded_entries"] = len(self.entries)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.entries = {}
            self.stats["invalidated"] = True
            self.stats["load_error"] = str(exc)

    def _key(
        self,
        namespace: str,
        candidate: dict[str, Any],
        peer: dict[str, Any] | None,
        settings: dict[str, Any],
    ) -> str:
        payload = {
            "namespace": namespace,
            "candidate": _item_signature(candidate),
            "peer": _item_signature(peer) if peer is not None else None,
            "settings": settings,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def get(
        self,
        namespace: str,
        candidate: dict[str, Any],
        peer: dict[str, Any] | None,
        settings: dict[str, Any],
    ) -> Any | None:
        key = self._key(namespace, candidate, peer, settings)
        if key not in self.entries:
            self.stats["misses"] += 1
            return None
        self.stats["hits"] += 1
        return copy.deepcopy(self.entries[key])

    def put(
        self,
        namespace: str,
        candidate: dict[str, Any],
        peer: dict[str, Any] | None,
        settings: dict[str, Any],
        value: Any,
    ) -> None:
        key = self._key(namespace, candidate, peer, settings)
        self.entries[key] = copy.deepcopy(value)
        self.stats["writes"] += 1
        self._dirty = True

    def save(self) -> None:
        if self.path is None or not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "engine_version": CACHE_ENGINE_VERSION,
            "corpus_fingerprint": self.corpus_digest,
            "entries": self.entries,
        }
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_name)
