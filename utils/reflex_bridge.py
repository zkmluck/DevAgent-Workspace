"""反射弧闸门的接入层。

职责只有一个：在真正对外写之前，问一次"这个 PR 现在该不该开"，把三档判决交给调用方。

这一层是**可缺席**的：找不到 jev-reflex-gate 时返回 None，流水线回到接入前的行为，
并在日志里明确写出来。闸门是加分项，不该让工作台本身跑不起来。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.config import GITHUB_TOKEN, PROJECT_ROOT
from github_api.repo_fetch import parse_repo_url

# 源码就在工作台目录下时（开发期常见），直接从这里加载，不必先 pip install
_BUNDLED_PACKAGE = PROJECT_ROOT / "jev-reflex-gate"


def _load_gate() -> Optional[Tuple[Any, Any]]:
    """返回 (guard_github_pr, load_settings)；装不上则返回 None。"""

    try:
        import jev_reflex  # noqa: F401
    except ImportError:
        if not _BUNDLED_PACKAGE.is_dir():
            return None
        sys.path.insert(0, str(_BUNDLED_PACKAGE))
        try:
            import jev_reflex  # noqa: F401
        except ImportError:
            return None

    from jev_reflex import guard_github_pr, load_settings

    return guard_github_pr, load_settings


def probe_push_permission(repo_url: str) -> Optional[bool]:
    """只读探测我们对目标仓库有没有推送权限。探测不了就返回 None（未知）。

    这一步很重要：`unauthorized_push` 是闸门里的一票否决类，而它需要知道"我们到底
    能不能推"。探测器失败时传 None，闸门会按"权限未知"处理，稍微保守一点。
    """

    if not GITHUB_TOKEN:
        return None
    try:
        from github import Github

        owner, name = parse_repo_url(repo_url)
        repo = Github(GITHUB_TOKEN).get_repo(f"{owner}/{name}")
        permissions = repo.permissions
        if permissions is None:
            return None
        return bool(permissions.push)
    except Exception:
        return None


def review_pr(
    repo_url: str, artifacts_dir: str, state: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """给一次 PR 动作投票。返回 None 表示闸门不可用，调用方按原样执行。"""

    loaded = _load_gate()
    if loaded is None:
        return None
    guard_github_pr, load_settings = loaded

    try:
        settings = load_settings()
    except Exception:
        return None
    if not settings.enabled:
        return None

    can_push = probe_push_permission(repo_url)
    is_fork = None if can_push is None else (not can_push)

    decision = guard_github_pr(
        repo_url,
        artifacts_dir,
        proxy_state=state or {},
        can_push=can_push,
        is_fork=is_fork,
    )
    answers = decision.answers or {}
    return {
        "verdict": decision.verdict.value,
        "blocked": decision.blocked,
        "downgrade_to_draft": decision.downgrade_to_draft,
        "reasons": list(decision.reasons),
        "action_id": decision.action_id,
        "client": decision.client,
        "error": decision.error,
        "block": (answers.get("block") or {}).get("noul"),
        "noise": (answers.get("noise") or {}).get("noul"),
        "risk_class": (answers.get("risk_class") or {}).get("choice"),
        "can_push": can_push,
    }
