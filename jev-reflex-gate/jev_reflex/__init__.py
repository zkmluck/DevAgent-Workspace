"""反射弧：给 agent 的对外写操作加一道"动作前"的概率闸门。

用法::

    from jev_reflex import guard_github_pr

    decision = guard_github_pr("zkmluck/errAnalyst", artifacts_dir, can_push=False)
    if decision.blocked:
        print(decision.reasons)
    else:
        create_github_pr(..., draft=decision.downgrade_to_draft)
"""

from .config import Settings, load_settings
from .gate import guard, guard_github_pr
from .models import Action, Artifact, Decision, Verdict

__all__ = [
    "Action",
    "Artifact",
    "Decision",
    "Verdict",
    "Settings",
    "load_settings",
    "guard",
    "guard_github_pr",
]
__version__ = "0.1.0"
