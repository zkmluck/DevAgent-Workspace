"""离线用的假 Jev。

响应结构和真实 API 完全一致，判决由可解释的启发式给出。它**不是随机数发生器**：
同一份 state 永远得到同一组概率，所以单测可以断言具体数值，不需要网络也不需要花钱。
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from .config import Settings

SECRET_MARKERS = (
    "[redacted]",
    "jv_live_",
    "sk-",
    "ghp_",
    "gho_",
    "github_pat_",
    "akia",
    "api key",
    "apikey",
    "password",
    "credential",
    "secret",
)
LOW_VALUE_MARKERS = ("empty", "placeholder", "no changes", "todo only", "lorem ipsum")
DUPLICATE_MARKERS = ("duplicate", "already exists", "same branch")
PERMISSION_MARKERS = (
    "push permission: no",
    "can not push",
    "cannot push",
    "no push permission",
    "permission denied",
)
UPSTREAM_MARKER = "where the commit goes: the upstream repository"


def _choice_answer(winner: str, confidence: float) -> Dict[str, Any]:
    others = [name for name in (
        "routine",
        "sensitive_content",
        "unauthorized_push",
        "low_value_spam",
        "duplicate",
    ) if name != winner]
    remainder = max(0.0, 1.0 - confidence)
    share = round(remainder / len(others), 4)
    probabilities = {name: share for name in others}
    probabilities[winner] = round(confidence, 4)
    return {
        "type": "choice",
        "choice": winner,
        "confidence": round(confidence, 4),
        "probabilities": probabilities,
    }


def assess(state: str) -> Tuple[float, float, str, float, Dict[str, Any]]:
    """把 state 映射成 (拦截概率, 噪音概率, 风险类别, 影响面, 选项答案)。

    规则按严重程度从高到低匹配，先命中的先说话。
    """

    text = state.lower()
    fork = "fork required: yes" in text

    if any(marker in text for marker in SECRET_MARKERS):
        return 0.93, 0.05, "sensitive_content", 3.0, _choice_answer("sensitive_content", 0.9)

    # 没有推送权限、却要直接推上游：这是越权，不是"fork 贡献"
    if any(marker in text for marker in PERMISSION_MARKERS) and UPSTREAM_MARKER in text:
        return 0.88, 0.05, "unauthorized_push", 3.0, _choice_answer("unauthorized_push", 0.88)

    if "artifacts: none" in text or any(
        marker in text for marker in LOW_VALUE_MARKERS
    ):
        return 0.15, 0.88, "low_value_spam", 2.0, _choice_answer("low_value_spam", 0.62)

    if any(marker in text for marker in DUPLICATE_MARKERS):
        return 0.15, 0.8, "duplicate", 2.0, _choice_answer("duplicate", 0.6)

    allow = 0.06 + (0.04 if fork else 0.0)
    return allow, 0.08, "routine", (2.0 if fork else 1.0), _choice_answer("routine", 0.93)


class MockJev:
    """与 JevClient 同形的假客户端。"""

    name = "mock"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    def ask(
        self, state: str, questions: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        allow, noise, risk_class, blast, choice_answer = assess(state)
        answers: Dict[str, Any] = {
            "block": {"type": "noul", "noul": allow},
            "noise": {"type": "noul", "noul": noise},
            "risk_class": choice_answer,
            "blast_radius": {
                "type": "score",
                "score": blast,
                "probabilities": {str(int(blast)): 1.0},
            },
        }
        usage = {
            "input_tokens": max(1, len(state) // 4),
            "cost_usd": 0.0,
            "credits_remaining_usd": None,
            "mock": True,
        }
        return answers, usage
