"""GitHub helpers copied from gsb-prompt-create; single main branch policy."""
from __future__ import annotations
import base64
import re
import shutil
import subprocess
import sys
import time
import os
from pathlib import Path
BASE_BRANCH_NAME = "main"
REQUIRED_REMOTE_BRANCHES = {"main"}
COMMON_GH_PATHS = ("/opt/homebrew/bin/gh", "/usr/local/bin/gh")

def run_cmd(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=True, text=True)


def run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return run_cmd(["git", *args], cwd=cwd)


def gh_binary() -> str:
    found = shutil.which("gh")
    if found:
        return found
    for candidate in COMMON_GH_PATHS:
        if Path(candidate).is_file():
            return candidate
    raise SystemExit("Missing required command: gh（需要 gh 建任务仓库）")


def run_gh(args: list[str]) -> subprocess.CompletedProcess[str]:
    # Only retry explicit throttling, never permission/name/conflict failures.
    deadline = time.monotonic() + int(os.environ.get("S11_GITHUB_RETRY_BUDGET_SECONDS", "7200"))
    attempt = 0
    while True:
        result = run_cmd([gh_binary(), *args])
        message = (result.stderr + "\n" + result.stdout).lower()
        limited = any(word in message for word in (
            "rate limit", "secondary rate", "abuse detection", "http 429", "too many requests",
            "created too many repositories", "too quickly. please try again later"))
        if result.returncode == 0 or not limited:
            return result
        delay = min(60 * 2 ** min(attempt, 4), 900)
        hint = re.search(r"retry[- ]after[: =]+(\d+)", message)
        if hint:
            delay = max(delay, int(hint.group(1)))
        if time.monotonic() + delay > deadline:
            raise RuntimeError("GitHub 限流等待预算已用完；保留已完成清单，稍后从同批次恢复")
        print(f"GitHub 限流：等待 {delay} 秒后重试（第 {attempt + 1} 次）", file=sys.stderr, flush=True)
        remaining = delay
        while remaining:
            step = min(remaining, 30)
            time.sleep(step)
            remaining -= step
        attempt += 1


def github_token_args(remote_url: str) -> list[str]:
    """远端是 github.com 且本地凭据不可用时，退回用 gh 的 token 做一次性认证。

    这些 `-c` 只作用在当次 git 调用上，不会写进克隆出来的 `.git/config`，
    所以生成的 origin 里不会留下 token。
    """
    if "github.com" not in remote_url:
        return []
    result = run_cmd([gh_binary(), "auth", "token"])
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        return []
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return [
        "-c",
        "credential.helper=",
        "-c",
        "http.proxy=",
        "-c",
        "https.proxy=",
        "-c",
        "http.version=HTTP/1.1",
        "-c",
        f"http.https://github.com/.extraHeader=Authorization: Basic {encoded}",
    ]


def github_ssh_url(repo_url: str) -> str:
    match = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?$", repo_url.strip())
    if not match:
        return ""
    owner, name = match.groups()
    return f"git@github.com:{owner}/{name}.git"


def run_git_authorized(
    args: list[str], *, cwd: Path | None = None, remote_url: str = "",
) -> subprocess.CompletedProcess[str]:
    result = run_git(args, cwd=cwd)
    if result.returncode == 0:
        return result
    extra = github_token_args(remote_url)
    if not extra:
        return result
    return run_git([*extra, *args], cwd=cwd)


def source_remote_url(source: Path) -> str:
    result = run_git(["config", "--get", "remote.origin.url"], cwd=source)
    url = result.stdout.strip()
    if result.returncode != 0 or not url:
        raise SystemExit(
            "SOURCE_NO_REMOTE: 原始代码目录没有配置 origin 远端，无法推送基准提交。"
            f" source={source}。先给原始仓库加好远端再重新运行。"
        )
    return url


def normalize_repo_url(url: str) -> str:
    """把 https / ssh / scp 形式的 GitHub 地址统一成 https 页面地址（不带 .git）。"""
    cleaned = url.strip()
    cleaned = re.sub(r"^git@github\.com:", "https://github.com/", cleaned)
    cleaned = re.sub(r"^ssh://git@github\.com/", "https://github.com/", cleaned)
    cleaned = re.sub(r"\.git$", "", cleaned)
    return cleaned


def owner_of(url: str, fallback: str | None = None) -> str:
    match = re.search(r"github\.com[:/]+([^/]+)/", url)
    if match:
        return match.group(1)
    if fallback:
        return fallback
    raise SystemExit(f"无法从远端地址解析 owner：{url}，请用 --owner 指定")


def current_branch(source: Path) -> str:
    result = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=source)
    branch = result.stdout.strip()
    return branch if branch and branch != "HEAD" else "HEAD"


def repo_exists(owner: str, name: str) -> bool:
    result = run_gh(["api", f"repos/{owner}/{name}", "--silent"])
    if result.returncode == 0:
        return True
    if "404" in result.stderr or "404" in result.stdout:
        return False
    raise RuntimeError("无法确认候选仓库是否存在：" + (result.stderr or result.stdout).strip())


def repo_branches(owner: str, name: str) -> set[str]:
    result = run_gh(["api", f"repos/{owner}/{name}/branches", "--paginate", "--jq", ".[].name"])
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def remote_heads(repo_url: str, *, cwd: Path | None = None) -> dict[str, str]:
    """远端 `refs/heads/*` 的分支名 -> 提交 SHA。

    实测 GitHub 上 `ls-remote` 会偶发 `RPC failed; curl 16 Error in the HTTP2 framing layer`，
    所以普通调用失败后退回 HTTP/1.1 + 一次性 token 再试，避免把网络抖动当成任务失败。
    """
    match = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?$", repo_url.strip())
    if match:
        owner, name = match.groups()
        result = run_gh([
            "api", f"repos/{owner}/{name}/branches", "--paginate",
            "--jq", '.[] | "\\(.name)\\t\\(.commit.sha)"',
        ])
        if result.returncode == 0:
            heads: dict[str, str] = {}
            for line in result.stdout.splitlines():
                branch, _, sha = line.partition("\t")
                if branch and sha:
                    heads[branch] = sha.strip()
            return heads
    result = run_git(["ls-remote", "--heads", repo_url], cwd=cwd)
    if result.returncode != 0:
        result = run_git_authorized(
            ["-c", "http.version=HTTP/1.1", "ls-remote", "--heads", repo_url],
            cwd=cwd, remote_url=repo_url,
        )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "git ls-remote failed"
        raise RuntimeError(f"无法读取 {repo_url} 的分支列表：{message}")
    heads: dict[str, str] = {}
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/"):
            heads[ref[len("refs/heads/"):]] = sha.strip()
    return heads


def remote_branches(repo_url: str) -> set[str]:
    return set(remote_heads(repo_url))


def create_repo(owner: str, name: str, visibility: str) -> None:
    result = run_gh(["repo", "create", f"{owner}/{name}", f"--{visibility}"])
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "gh repo create failed"
        raise RuntimeError(f"创建仓库 {owner}/{name} 失败：{message}")


def push_commit_to_branch(repo_url: str, commit: str, branch: str, source: Path) -> str:
    """把基准提交推成任务仓库的分支；分支已存在就不覆盖。"""
    existing = remote_branches(repo_url)
    if branch in existing:
        return "reused"
    result = run_git_authorized(
        ["push", repo_url, f"{commit}:refs/heads/{branch}"],
        cwd=source, remote_url=repo_url,
    )
    if result.returncode != 0:
        ssh_url = github_ssh_url(repo_url)
        if ssh_url:
            result = run_git(["push", ssh_url, f"{commit}:refs/heads/{branch}"], cwd=source)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "git push failed"
        raise RuntimeError(f"推送分支 {branch} 失败：{message}")
    return "created"


def clone_branch(
    repo_url: str, branch: str, dest: Path, *, full_clone: bool,
    git_user_name: str | None, git_user_email: str | None,
    local_source: Path | None = None,
) -> None:
    if local_source is not None:
        source_branch = current_branch(local_source)
        args = ["clone", "--no-hardlinks", "--branch", source_branch]
        if not full_clone:
            args.append("--single-branch")
        args += [str(local_source), str(dest)]
        result = run_git(args)
        if result.returncode == 0:
            run_git(["remote", "set-url", "origin", repo_url], cwd=dest)
            run_git(["checkout", "-B", branch], cwd=dest)
            run_git([
                "config", "remote.origin.fetch",
                f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
            ], cwd=dest)
            if source_branch != branch:
                run_git(["update-ref", "-d", f"refs/remotes/origin/{source_branch}"], cwd=dest)
    else:
        args = ["clone", "--branch", branch]
        if not full_clone:
            args.append("--single-branch")
        args += [repo_url, str(dest)]
        result = run_git_authorized(args, remote_url=repo_url)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "git clone failed"
        raise RuntimeError(f"克隆 {repo_url} 的 {branch} 分支失败：{message}")
    if git_user_name:
        run_git(["config", "user.name", git_user_name], cwd=dest)
    if git_user_email:
        run_git(["config", "user.email", git_user_email], cwd=dest)
    tracking = run_git(["config", "--get", f"branch.{branch}.remote"], cwd=dest)
    if tracking.returncode != 0 or tracking.stdout.strip() != "origin":
        run_git(["config", f"branch.{branch}.remote", "origin"], cwd=dest)
    merge = run_git(["config", "--get", f"branch.{branch}.merge"], cwd=dest)
    if merge.returncode != 0 or not merge.stdout.strip():
        run_git(["config", f"branch.{branch}.merge", f"refs/heads/{branch}"], cwd=dest)
