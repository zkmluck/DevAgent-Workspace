"""打印给 Qwen 用的提示词，方便直接复制到百炼/通义千问的对话窗口里试。

    python examples/print_qwen_prompt.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jev_reflex.policy import build_questions
from jev_reflex.prompts import SYSTEM_PROMPT, build_user_prompt

DEMO_STATE = """Action: github_pr
Repository: zkmluck/errAnalyst
Base branch: main
New branch: devagent/auto/20260922-103102
Push permission: we can NOT push to this repository; a fork will be created
Fork required: yes
Pull request requested: yes
Already a draft: no
Artifacts (2 files, 14200 bytes total):
  - analysis_report.md (12400 bytes, sha256:a1a1a1a1)
  - test_skeleton.py (1800 bytes, sha256:b2b2b2b2)
PR title: docs(devagent): errAnalyst 自动分析报告
PR body (first 200 chars): 由 DevAgent-Workspace 自动生成的仓库分析结果，包含 3 处缺陷、2 个测试骨架建议。"""


def main() -> None:
    print("#" * 72)
    print("# SYSTEM PROMPT（放进 system 角色）")
    print("#" * 72)
    print(SYSTEM_PROMPT)
    print()
    print("#" * 72)
    print("# USER PROMPT 示例（state + questions）")
    print("#" * 72)
    print(build_user_prompt(DEMO_STATE, build_questions()))


if __name__ == "__main__":
    main()
