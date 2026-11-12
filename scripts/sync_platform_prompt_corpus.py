#!/usr/bin/env python3
"""同步当前 SOLO-QA 账号的 GSB 题面，生成出题阶段使用的平台语料快照。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from solo_paths import gsb_create_scripts  # noqa: E402


DEFAULT_OUTPUT = SCRIPT_DIR.parent / "data" / "platform-prompt-corpus.json"

def load_submit_module():
    submit_path = gsb_create_scripts("gsb_submit.py") / "gsb_submit.py"
    spec = importlib.util.spec_from_file_location("gsb_submit_for_prompt_corpus", submit_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"加载不了 gsb_submit.py：{submit_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, submit_path


def compact_item(item: dict, index: int) -> dict | None:
    prompt = str(item.get("user_prompt") or item.get("prompt") or "").strip()
    if not prompt:
        return None
    record_id = str(item.get("id") or index)
    repo_id = str(item.get("repo_id") or "")
    return {
        "id": record_id,
        "label": repo_id or f"platform#{record_id}",
        "repo_id": repo_id,
        "task_type": str(item.get("question_type") or item.get("task_type") or ""),
        "status": str(item.get("status") or ""),
        "current_version": item.get("current_version") or 1,
        "submitted_at": str(item.get("submitted_at") or ""),
        "prompt": prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    }


def build_corpus(items: list[dict], *, account: str, base_url: str = "", fetched_at: str = "") -> dict:
    compact: list[dict] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        entry = compact_item(item, index)
        if entry is not None:
            compact.append(entry)
    compact.sort(key=lambda item: str(item.get("id") or ""))
    return {
        "version": 1,
        "profile": "gsb-platform-prompt-corpus-v1",
        "account": account,
        "base_url": base_url,
        "fetched_at": fetched_at,
        "record_count": len(compact),
        "items": compact,
    }


def read_index(path: Path) -> tuple[list[dict], str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError(f"平台索引缺少 items：{path}")
    fetched_at = str(payload.get("fetched_at") or "")
    if isinstance(payload.get("fetched_at"), (int, float)):
        from datetime import datetime, timezone

        fetched_at = datetime.fromtimestamp(float(payload["fetched_at"]), tz=timezone.utc).isoformat()
    return items, str(payload.get("username") or ""), fetched_at


def main() -> None:
    parser = argparse.ArgumentParser(description="同步 GSB 平台题面语料快照")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"输出 JSON；默认 {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--index",
        help="直接读取 gsb_submit 的平台索引缓存，不访问网络；用于离线重建语料",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="强制从平台重拉列表与详情（慢；会访问 SOLO-QA）",
    )
    args = parser.parse_args()

    output = Path(args.output).expanduser().resolve()
    if args.index:
        index_path = Path(args.index).expanduser().resolve()
        items, account, fetched_at = read_index(index_path)
        base_url = ""
    else:
        submit, _ = load_submit_module()
        api, me = submit.connect()
        account = str(me.get("username") or "")
        base_url = api.base_url
        if args.refresh:
            items, meta = submit.refresh_platform_text_index(api, account, force=True, ttl=0)
            fetched_at = submit.C.now_iso()
        else:
            index_path = submit.platform_index_path(account)
            if index_path.is_file():
                items, cached_account, cached_fetched_at = read_index(index_path)
                if cached_account:
                    account = cached_account
                fetched_at = cached_fetched_at or submit.C.now_iso()
            else:
                items, meta = submit.refresh_platform_text_index(
                    api, account, force=True, ttl=0
                )
                fetched_at = submit.C.now_iso()

    corpus = build_corpus(items, account=account, base_url=base_url, fetched_at=str(fetched_at))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(corpus, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "output": str(output),
        "account": account,
        "record_count": corpus["record_count"],
        "fetched_at": corpus["fetched_at"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
