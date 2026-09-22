"""策略层：把一次动作翻译成三个问题，再把答案翻译成一个判决。

这一层是整个项目的心脏，也是唯一需要"调参"的地方。两条硬规则写死在这里：

* 敏感内容和越权推送是**一票否决**，不看概率；
* 任何信息缺失、答案不合预期，都退化成待复核，绝不猜成放行。

关于那个 noul 问题的方向，务必记住：它问的是**"该不该拦下"**，不是"安不安全"。
概率越高越该拦。这个方向是刻意的失败安全设计——一个退化的模型如果把所有问题都
答成 0.99，结果是处处拦下（人会立刻发现并去修），而不是处处放行。

**风险和价值是两个轴**，所以要问两个 noul：`block` 问危险，`noise` 问值不值得打扰人。
评测集上的实测结论是：空产物、占位文件、重复 PR 这类动作，模型给的风险值很低也
完全正确——它们不危险，只是没意义。把两者塞进一个问题，任何阈值都分不开。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .config import Settings
from .models import Action, Artifact, Verdict

# 一票否决的风险类别
VETO_CLASSES = frozenset({"unauthorized_push", "sensitive_content"})

# 决策时需要识别的密钥形态（发送前、写日志前都要抹掉）
_SECRET_PATTERNS = (
    re.compile(r"jv_live_[A-Za-z0-9_\-]{6,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
)
_REDACTED = "[redacted]"


def redact(text: str) -> str:
    """抹掉看起来像密钥的片段。发送前和留痕前都必须过这一关。"""

    if not text:
        return ""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


def _artifact_line(artifact: Artifact) -> str:
    digest = f", sha256:{artifact.digest}" if artifact.digest else ""
    return f"  - {artifact.name} ({artifact.bytes} bytes{digest})"


def build_state(action: Action) -> str:
    """把动作压成一段精简、脱敏的 state。

    这是费用与隐私的唯一来源，所以只放决策真正需要的字段：不发正文、
    不发 token、不发本机绝对路径。
    """

    lines: List[str] = [f"Action: {action.action_type}"]
    lines.append(f"Repository: {action.repo or 'unknown'}")
    lines.append(f"Base branch: {action.base_branch or 'default branch'}")
    if action.head_branch:
        lines.append(f"New branch: {action.head_branch}")

    if action.can_push is True:
        lines.append("Push permission: yes, we can push to this repository")
    elif action.can_push is False:
        lines.append("Push permission: no, we cannot push to this repository")
    else:
        lines.append("Push permission: unknown")

    lines.append(f"Fork required: {'yes' if action.is_fork else 'no'}")
    lines.append(
        "Where the commit goes: "
        + ("our own fork" if action.is_fork else "the upstream repository")
    )
    lines.append(f"Pull request requested: {'yes' if action.create_pr else 'no'}")
    lines.append(f"Already a draft: {'yes' if action.draft else 'no'}")

    if action.artifacts:
        total = sum(item.bytes for item in action.artifacts)
        lines.append(f"Artifacts ({len(action.artifacts)} files, {total} bytes total):")
        lines.extend(_artifact_line(item) for item in action.artifacts)
    else:
        lines.append(
            "Artifacts: none — nothing would be committed, the pull request would be empty"
        )

    if action.pr_title:
        lines.append(f"PR title: {redact(action.pr_title)}")
    if action.pr_body:
        excerpt = redact(action.pr_body[:200])
        lines.append(f"PR body (first 200 chars): {excerpt}")
    for key, value in action.extra.items():
        lines.append(f"{key}: {redact(str(value))}")

    return redact("\n".join(lines))


def build_questions() -> Dict[str, Any]:
    """一次往返问三个问题，共享同一份 state 成本。"""

    return {
        "block": {
            "type": "noul",
            "instructions": (
                "Should this external write action be blocked right now? "
                "Answer with the probability that it should be blocked, "
                "not the probability that the underlying content is good."
            ),
        },
        "noise": {
            "type": "noul",
            "instructions": (
                "Would this pull request be noise for the maintainers — empty, "
                "placeholder, duplicate or otherwise adding no real value? "
                "Answer with the probability that it adds no value, "
                "not the probability that it is dangerous."
            ),
        },
        "risk_class": {
            "type": "choice",
            "instructions": "What kind of risk does this action carry?",
            "criteria": {
                "routine": "normal, expected automated contribution",
                "sensitive_content": "contains secrets, credentials or private data",
                "unauthorized_push": "writes to a repository where we lack permission",
                "low_value_spam": "no meaningful content, would be noise for maintainers",
                "duplicate": "an equivalent pull request already exists",
            },
        },
        "blast_radius": {
            "type": "score",
            "instructions": "How large is the blast radius if this goes wrong?",
            "criteria": [
                "trivial, only our own scratch space",
                "affects our own fork",
                "notifies a maintainer",
                "writes to a repository belonging to another party",
                "could damage a third party repository",
            ],
        },
    }


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def evaluate(
    answers: Dict[str, Any], settings: Settings
) -> Tuple[Verdict, List[str], Dict[str, Any]]:
    """把三个答案折叠成一个判决。

    返回 (verdict, reasons, extracted)。extracted 用于留痕，只放三个值。
    """

    block_risk = _number((answers.get("block") or {}).get("noul"))
    noise = _number((answers.get("noise") or {}).get("noul"))
    risk_class = (answers.get("risk_class") or {}).get("choice")
    blast = _number((answers.get("blast_radius") or {}).get("score"))

    if block_risk is None or noise is None or not isinstance(risk_class, str):
        return (
            Verdict.REVIEW,
            ["answer missing required fields; treated as uncertain"],
            {
                "block": block_risk,
                "noise": noise,
                "risk_class": risk_class,
                "blast_radius": blast,
            },
        )

    extracted = {
        "block": block_risk,
        "noise": noise,
        "risk_class": risk_class,
        "blast_radius": blast,
    }
    reasons: List[str] = []

    if risk_class in VETO_CLASSES:
        reasons.append(f"risk_class={risk_class} is an automatic block")
        return Verdict.BLOCK, reasons, extracted

    if block_risk >= settings.block_above:
        reasons.append(
            f"block={block_risk:.2f} at or above block threshold {settings.block_above:.2f}"
        )
        return Verdict.BLOCK, reasons, extracted

    if noise >= settings.noise_above:
        reasons.append(
            f"noise={noise:.2f} at or above {settings.noise_above:.2f}: "
            "not dangerous, but not worth a maintainer's attention"
        )
        return Verdict.REVIEW, reasons, extracted

    if block_risk < settings.allow_below and risk_class == "routine":
        reasons.append(
            f"block={block_risk:.2f} below {settings.allow_below:.2f} and risk_class=routine"
        )
        return Verdict.ALLOW, reasons, extracted

    if block_risk < settings.allow_below:
        reasons.append(
            f"block={block_risk:.2f} is low but risk_class={risk_class} is not routine"
        )
    else:
        reasons.append(
            f"block={block_risk:.2f} falls in the uncertain band "
            f"[{settings.allow_below:.2f}, {settings.block_above:.2f})"
        )
    return Verdict.REVIEW, reasons, extracted
