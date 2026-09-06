"""PyGithub 自动创建 PR 流程。

流程：
1. 用 GITHUB_TOKEN 登录；
2. 新建一个独立分支（默认直接推到原仓库；无 push 权限时自动 fork）；
3. 把 output/ 下的分析产物提交到该分支的 .devagent/ 目录；
4. 调用 PyGithub create_pull 创建 PR。

这是一个“对外写操作”，调用方必须显式传入 create_pr=True 才会执行。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from github import Github, GithubException

from app.config import GITHUB_TOKEN
from github_api.repo_fetch import parse_repo_url

# (本地产物文件名, 仓库内目标路径)
ARTIFACT_TARGETS = [
    ("analysis_report.md", ".devagent/analysis_report.md"),
    ("docs_draft.md", ".devagent/docs_draft.md"),
    ("test_skeleton.py", ".devagent/test_skeleton.py"),
    ("pr_draft.md", ".devagent/pr_draft.md"),
]


class PRCreationError(Exception):
    """PR 创建过程中的可读错误。"""


def _can_push(repo) -> bool:
    try:
        return bool(repo.permissions.push)
    except Exception:
        return False


def _wait_fork_ready(github: Github, fork, base_branch: str, max_wait: int = 60) -> None:
    """GitHub fork 创建是异步的，轮询直到目标分支可读。"""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            fork = github.get_repo(fork.full_name)
            fork.get_branch(base_branch)
            return
        except GithubException:
            time.sleep(3)
    raise PRCreationError(f"fork 等待超时（{max_wait}s）: {fork.full_name}")


def _resolve_head_repo(github: Github, upstream, base_branch: str):
    """返回 (用于提交代码的仓库, PR head 所属用户)。

    有 push 权限直接在原仓库建分支；否则自动 fork，PR 从 fork 发起。
    """
    auth_user = github.get_user()
    if _can_push(upstream):
        return upstream, upstream.owner.login

    fork = auth_user.create_fork(upstream, default_branch_only=True)
    _wait_fork_ready(github, fork, base_branch)
    fork = github.get_repo(fork.full_name)
    return fork, auth_user.login


def _commit_artifacts(head_repo, branch: str, artifacts_dir: Path, message: str) -> int:
    """把存在的产物文件逐个提交到分支；文件已存在则更新。"""
    committed = 0
    for local_name, remote_path in ARTIFACT_TARGETS:
        source = artifacts_dir / local_name
        if not source.exists():
            continue
        try:
            existing = head_repo.get_contents(remote_path, ref=branch)
            sha = existing.sha if not isinstance(existing, list) else None
        except GithubException as exc:
            if exc.status != 404:
                raise
            sha = None

        content = source.read_text(encoding="utf-8")
        if sha:
            head_repo.update_file(
                remote_path, message, content, sha=sha, branch=branch
            )
        else:
            head_repo.create_file(remote_path, message, content, branch=branch)
        committed += 1
    return committed


def _find_open_pr(upstream, head_ref: str, base: str):
    try:
        for pr in upstream.get_pulls(state="open", head=head_ref, base=base):
            return pr
    except GithubException:
        pass
    return None


def create_github_pr(
    repo_url: str,
    artifacts_dir,
    title: str = "",
    body: str = "",
    base_branch: str = "",
    draft: bool = False,
    max_wait: int = 60,
) -> dict:
    """创建 PR 并返回 {pr_number, html_url, branch, base}。"""
    if not GITHUB_TOKEN:
        raise PRCreationError(
            "未配置 GITHUB_TOKEN。请在 .env 中填写一个具备 repo 权限的 token，再勾选创建 PR。"
        )

    owner, repo_name = parse_repo_url(repo_url)
    artifacts_dir = Path(artifacts_dir)
    if not artifacts_dir.exists():
        raise PRCreationError(f"产物目录不存在: {artifacts_dir}")

    github = Github(GITHUB_TOKEN)
    upstream = github.get_repo(f"{owner}/{repo_name}")
    auth_user = github.get_user()
    base = base_branch.strip() or upstream.default_branch

    branch = (
        f"devagent/auto/{time.strftime('%Y%m%d-%H%M%S')}"
        f"-{''.join(ch for ch in f'{repo_name}-{auth_user.login}' if ch.isalnum() or ch in '-_')[:40]}"
    )
    branch = branch.strip("-")

    head_repo, head_owner = _resolve_head_repo(github, upstream, base)

    # 分支起点使用 base 分支最新 commit
    base_sha = upstream.get_branch(base).commit.sha
    head_repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base_sha)

    commit_message = f"docs(devagent): 添加 DevAgent-Workspace 自动分析产物"
    committed = _commit_artifacts(head_repo, branch, artifacts_dir, commit_message)
    if committed == 0:
        raise PRCreationError("没有可提交的产物文件，跳过 PR 创建")

    pr_title = title.strip() or f"docs(devagent): {repo_name} 自动分析报告"
    pr_body = body.strip() or "由 DevAgent-Workspace 自动生成。"
    head_ref = branch if head_repo.full_name == upstream.full_name else f"{head_owner}:{branch}"

    existing = _find_open_pr(upstream, head_ref, base)
    if existing is not None:
        return {
            "pr_number": existing.number,
            "html_url": existing.html_url,
            "branch": branch,
            "base": base,
            "head": head_ref,
            "reused": True,
        }

    pr = upstream.create_pull(
        base=base,
        head=head_ref,
        title=pr_title,
        body=pr_body,
        draft=draft,
        maintainer_can_modify=True,
    )
    return {
        "pr_number": pr.number,
        "html_url": pr.html_url,
        "branch": branch,
        "base": base,
        "head": head_ref,
        "reused": False,
    }
