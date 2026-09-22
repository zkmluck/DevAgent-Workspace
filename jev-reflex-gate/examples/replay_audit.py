"""按 action_id 回放历史判决，或者列出全部记录。

    python examples/replay_audit.py              # 列出全部
    python examples/replay_audit.py 8f2c1a4e     # 回放某一次
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jev_reflex.audit import read_all, replay

DEFAULT_AUDIT = "output/reflex/audit.jsonl"


def main() -> None:
    path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_AUDIT
    if len(sys.argv) > 1:
        print(replay(sys.argv[1], path))
        return

    entries = read_all(path)
    if not entries:
        print(f"没有审计记录：{path}")
        return
    print(f"{len(entries)} 条记录（{path}）")
    for entry in entries:
        answers = entry.get("answers") or {}
        print(
            f"  {entry.get('action_id')}  {entry.get('verdict'):<6} "
            f"block={answers.get('block')}  risk={answers.get('risk_class')}  "
            f"{entry.get('action_type')} -> {entry.get('repo')}"
        )


if __name__ == "__main__":
    main()
