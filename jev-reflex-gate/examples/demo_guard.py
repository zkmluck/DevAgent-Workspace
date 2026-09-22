"""离线演示：三种动作走一遍闸门，然后回放其中一次的判决理由。

不需要网络，不需要 API key，也不花钱：

    python examples/demo_guard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jev_reflex.audit import replay
from jev_reflex.config import Settings
from jev_reflex.gate import guard
from jev_reflex.models import Action, Artifact

AUDIT = "output/reflex/demo_audit.jsonl"


def main() -> None:
    settings = Settings(audit_path=AUDIT)

    cases = [
        (
            "① 自己仓库里的正常报告",
            Action(
                repo="zkmluck/DevAgent-Workspace",
                can_push=True,
                artifacts=(
                    Artifact("analysis_report.md", 12400, "a1" * 32),
                    Artifact("test_skeleton.py", 2200, "b2" * 32),
                ),
                pr_title="docs(devagent): 自动分析报告",
                pr_body="由流水线生成的代码分析文本。",
            ),
        ),
        (
            "② 产物里混进了密钥",
            Action(
                repo="zkmluck/errAnalyst",
                can_push=False,
                is_fork=True,
                artifacts=(Artifact("pr_draft.md", 1800, "c3" * 32),),
                pr_title="docs(devagent): 自动分析报告",
                pr_body="示例片段里包含 sk-abcdefghijklmnop 这样的字符串。",
            ),
        ),
        (
            "③ 空产物，没有实质内容",
            Action(
                repo="torvalds/linux",
                can_push=False,
                is_fork=True,
                artifacts=(),
                pr_title="docs(devagent): 自动分析报告",
            ),
        ),
    ]

    last_review_id = ""
    for label, action in cases:
        decision = guard(action, settings=settings)
        print(f"{label}")
        print(f"    判决 : {decision.verdict.value}")
        print(f"    理由 : {'; '.join(decision.reasons)}")
        print(f"    降级 : {'是，转草稿 PR' if decision.downgrade_to_draft else '否'}")
        print(f"    耗时 : {decision.latency_ms}ms    记录 : {decision.action_id}")
        print()
        if decision.needs_review:
            last_review_id = decision.action_id

    print(f"审计日志：{AUDIT}")
    if last_review_id:
        print()
        print("回放最近一次待复核判决：")
        print(replay(last_review_id, AUDIT))


if __name__ == "__main__":
    main()
