"""数据模型：动作、产物、判决。

这里定义的是**白名单**：只有 Action 上出现的字段才可能被送去判断，
产物只带文件名、体积和摘要，永远不带正文。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class Verdict(str, Enum):
    """三档判决，对应用户可见的三种去向。"""

    ALLOW = "allow"
    BLOCK = "block"
    REVIEW = "review"


@dataclass(frozen=True)
class Artifact:
    """一个待提交的产物文件。只描述，不携带内容。"""

    name: str
    bytes: int = 0
    sha256: str = ""

    @property
    def digest(self) -> str:
        return self.sha256[:8]


@dataclass(frozen=True)
class Action:
    """一次待执行的对外写操作。"""

    action_type: str = "github_pr"
    repo: str = ""
    base_branch: str = ""
    head_branch: str = ""
    is_fork: bool = False
    can_push: Optional[bool] = None
    artifacts: Tuple[Artifact, ...] = ()
    pr_title: str = ""
    pr_body: str = ""
    draft: bool = False
    create_pr: bool = True
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass
class Decision:
    """闸门的输出。调用方只需要看 verdict 和 reasons。"""

    verdict: Verdict
    reasons: List[str] = field(default_factory=list)
    answers: Dict[str, Any] = field(default_factory=dict)
    action_id: str = ""
    action_type: str = ""
    repo: str = ""
    state_digest: str = ""
    latency_ms: int = 0
    cost_usd: float = 0.0
    client: str = "mock"
    error: Optional[str] = None

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW

    @property
    def blocked(self) -> bool:
        return self.verdict is Verdict.BLOCK

    @property
    def needs_review(self) -> bool:
        return self.verdict is Verdict.REVIEW

    @property
    def downgrade_to_draft(self) -> bool:
        """落在中间地带时，默认降级成草稿 PR：不阻塞流程，也不惊动维护者。"""

        return self.verdict is Verdict.REVIEW

    def summary(self) -> str:
        head = f"{self.verdict.value.upper()} ({self.client})"
        return head + (" — " + "; ".join(self.reasons) if self.reasons else "")
