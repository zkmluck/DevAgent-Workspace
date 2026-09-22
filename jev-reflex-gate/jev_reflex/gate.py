"""对外唯一入口：guard(action) 给出判决，并把这一判决留痕。

一条硬性约定：**闸门自己出问题时，判决一定是"待复核"**。超时、断网、
余额不足、返回格式异常，全都不会变成放行。
"""

from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Union

from .audit import record
from .config import Settings, load_settings
from .decide import JevClient, JevError
from .mock import MockJev
from .models import Action, Artifact, Decision, Verdict
from .policy import build_questions, build_state, evaluate
from .qwen import QwenJev


def _pick_client(settings: Settings, client: Any) -> Any:
    if client is not None:
        return client
    if settings.client == "jev":
        return JevClient(settings)
    if settings.client == "qwen":
        return QwenJev(settings)
    return MockJev(settings)


def _digest(state: str) -> str:
    return "sha256:" + hashlib.sha256(state.encode("utf-8")).hexdigest()[:32]


def guard(
    action: Action,
    *,
    settings: Optional[Settings] = None,
    client: Any = None,
    audit: bool = True,
) -> Decision:
    """判断一次对外写操作，返回三档判决之一。"""

    settings = settings or load_settings()
    action_id = uuid.uuid4().hex[:8]
    state = build_state(action)
    digest = _digest(state)

    def finish(
        decision: Decision, usage: Dict[str, Any], started: float
    ) -> Decision:
        decision.action_id = action_id
        decision.action_type = action.action_type
        decision.repo = action.repo
        decision.state_digest = digest
        decision.latency_ms = int((time.perf_counter() - started) * 1000)
        decision.cost_usd = float(usage.get("cost_usd") or 0.0)
        if audit:
            record(decision, settings=settings)
        return decision

    if not settings.enabled:
        started = time.perf_counter()
        return finish(
            Decision(Verdict.ALLOW, ["gating disabled by configuration"]), {}, started
        )

    started = time.perf_counter()
    active_client = _pick_client(settings, client)
    try:
        answers, usage = active_client.ask(state, build_questions())
    except JevError as exc:
        decision = Decision(
            Verdict.REVIEW,
            [f"{exc.code}: {exc.message}", "uncertainty cannot be resolved; do not auto-approve"],
            {},
            client=getattr(active_client, "name", "unknown"),
            error=exc.code,
        )
        return finish(decision, {}, started)
    except Exception as exc:  # pragma: no cover - 兜底，绝不静默放行
        decision = Decision(
            Verdict.REVIEW,
            [f"unexpected client failure: {type(exc).__name__}", "do not auto-approve"],
            {},
            client=getattr(active_client, "name", "unknown"),
            error="unexpected",
        )
        return finish(decision, {}, started)

    verdict, reasons, extracted = evaluate(answers, settings)
    decision = Decision(
        verdict,
        reasons,
        answers,
        client=getattr(active_client, "name", "unknown"),
    )
    return finish(decision, usage, started)


def _scan_artifacts(artifacts_dir: Union[str, Path]) -> Sequence[Artifact]:
    root = Path(artifacts_dir)
    if not root.exists():
        return ()
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        found.append(
            Artifact(
                name=path.relative_to(root).as_posix(),
                bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )
    return tuple(found)


def guard_github_pr(
    repo_url: str,
    artifacts_dir: Union[str, Path],
    *,
    proxy_state: Optional[Dict[str, Any]] = None,
    can_push: Optional[bool] = None,
    is_fork: Optional[bool] = None,
    base_branch: str = "",
    pr_title: str = "",
    pr_body: str = "",
    draft: bool = False,
    settings: Optional[Settings] = None,
    client: Any = None,
    audit: bool = True,
) -> Decision:
    """工作台接入用的便捷入口：从一个产物目录直接组装动作。

    ``proxy_state`` 可以传工作台流水线的 state（例如 PR 草稿里的标题和正文），
    这里只会取白名单里的字段，其余内容不会离开本机。
    """

    proxy_state = proxy_state or {}
    pr_data = proxy_state.get("pr") or {}
    repo = str(proxy_state.get("repo") or proxy_state.get("repo_url") or repo_url or "")
    if repo.startswith("http"):
        repo = repo.rstrip("/").split("github.com/")[-1]

    resolved_can_push = can_push
    if resolved_can_push is None and "can_push" in proxy_state:
        resolved_can_push = bool(proxy_state.get("can_push"))
    resolved_fork = is_fork
    if resolved_fork is None:
        resolved_fork = bool(proxy_state.get("is_fork", False))

    action = Action(
        action_type="github_pr",
        repo=repo,
        base_branch=base_branch or str(proxy_state.get("branch") or ""),
        head_branch=str(pr_data.get("branch") or ""),
        is_fork=bool(resolved_fork),
        can_push=resolved_can_push,
        artifacts=tuple(_scan_artifacts(artifacts_dir)),
        pr_title=pr_title or str(pr_data.get("title") or ""),
        pr_body=pr_body or str(pr_data.get("llm_body") or pr_data.get("body") or ""),
        draft=draft,
        create_pr=True,
    )
    return guard(action, settings=settings, client=client, audit=audit)
