"""审计留痕：每次判决追加一行 JSONL，事后可以回放"当时为什么放行"。

日志里永远不出现 API key、不出现产物正文；state 只留摘要。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Settings, load_settings
from .models import Decision

_CST = timezone(timedelta(hours=8))


def _entry(decision: Decision, settings: Settings) -> Dict[str, Any]:
    extracted: Dict[str, Any] = {}
    block = (decision.answers.get("block") or {}).get("noul")
    noise = (decision.answers.get("noise") or {}).get("noul")
    risk = (decision.answers.get("risk_class") or {}).get("choice")
    blast = (decision.answers.get("blast_radius") or {}).get("score")
    if block is not None:
        extracted["block"] = block
    if noise is not None:
        extracted["noise"] = noise
    if risk is not None:
        extracted["risk_class"] = risk
    if blast is not None:
        extracted["blast_radius"] = blast

    return {
        "ts": datetime.now(_CST).isoformat(timespec="seconds"),
        "action_id": decision.action_id,
        "action_type": decision.action_type,
        "repo": decision.repo,
        "state_digest": decision.state_digest,
        "answers": extracted,
        "verdict": decision.verdict.value,
        "reasons": list(decision.reasons),
        "thresholds": {
            "allow_below": settings.allow_below,
            "block_above": settings.block_above,
            "noise_above": settings.noise_above,
        },
        "latency_ms": decision.latency_ms,
        "cost_usd": decision.cost_usd,
        "client": decision.client,
        "error": decision.error,
        "human_override": None,
    }


def record(
    decision: Decision, *, settings: Optional[Settings] = None, path: Optional[str] = None
) -> str:
    """追加一条审计记录，返回写入的文件路径。"""

    settings = settings or load_settings()
    target = Path(path or settings.audit_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(_entry(decision, settings), ensure_ascii=False)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line + os.linesep)
    return str(target)


def read_all(path: Optional[str] = None) -> List[Dict[str, Any]]:
    target = Path(path or load_settings().audit_path)
    if not target.exists():
        return []
    entries: List[Dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def find(action_id: str, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    for entry in reversed(read_all(path)):
        if entry.get("action_id") == action_id:
            return entry
    return None


def replay(action_id: str, path: Optional[str] = None) -> str:
    """把"当时为什么这么判"还原成人能读的一段话。"""

    entry = find(action_id, path)
    if entry is None:
        return f"no audit entry for action_id={action_id}"

    answers = entry.get("answers") or {}
    thresholds = entry.get("thresholds") or {}
    lines = [
        f"action_id : {entry.get('action_id')}",
        f"when      : {entry.get('ts')}",
        f"action    : {entry.get('action_type')} -> {entry.get('repo')}",
        f"verdict   : {entry.get('verdict')}",
        f"answers   : block={answers.get('block')} noise={answers.get('noise')} "
        f"risk_class={answers.get('risk_class')} "
        f"blast_radius={answers.get('blast_radius')}",
        f"threshold : allow_below={thresholds.get('allow_below')} "
        f"block_above={thresholds.get('block_above')} "
        f"noise_above={thresholds.get('noise_above')}",
        f"client    : {entry.get('client')} "
        f"latency={entry.get('latency_ms')}ms cost=${entry.get('cost_usd')}",
        "reasons   :",
    ]
    lines.extend(f"  - {reason}" for reason in (entry.get("reasons") or []))
    return "\n".join(lines)
