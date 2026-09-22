"""契约校验：把"大模型可能吐出的垃圾"挡在策略层之前。

真实 Jev 不需要这一层，因为它的答案形状由请求固定。用 Qwen 这类大模型替身时，
这一层就是那个保证：任何不合规的答案都变成 JevError("bad_response")，
闸门据此判"待复核"——**绝不把解析失败当成放行**。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple


class BadAnswers(ValueError):
    """模型返回的内容不符合契约。"""


def _as_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or value is None:
        raise BadAnswers(f"{field} is not a number: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            raise BadAnswers(f"{field} is not a number: {value!r}") from None
    raise BadAnswers(f"{field} is not a number: {value!r}")


def _check_range(value: float, low: float, high: float, field: str) -> float:
    if value < low or value > high:
        raise BadAnswers(f"{field}={value} outside [{low}, {high}]")
    return value


def _normalise_probabilities(
    raw: Any, allowed: List[str], field: str
) -> Dict[str, float]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise BadAnswers(f"{field}.probabilities is not an object")
    cleaned: Dict[str, float] = {}
    for key, value in raw.items():
        name = str(key)
        if allowed and name not in allowed:
            raise BadAnswers(f"{field}.probabilities has unknown key {name!r}")
        weight = _as_float(value, f"{field}.probabilities[{name}]")
        # 有些模型会给"票数"而不是概率（例如 7 和 3）。只要非负、有限，就按比例归一，
        # 而不是整条判失败——归一之后它仍然是同一个分布。
        if not math.isfinite(weight) or weight < 0:
            raise BadAnswers(f"{field}.probabilities[{name}]={weight} is not a weight")
        cleaned[name] = weight
    total = sum(cleaned.values())
    if total <= 0:
        raise BadAnswers(f"{field}.probabilities sums to zero")
    if abs(total - 1.0) > 0.1 or any(value > 1.0 for value in cleaned.values()):
        cleaned = {key: round(value / total, 4) for key, value in cleaned.items()}
    return cleaned


def validate_answers(
    answers: Any, questions: Dict[str, Any]
) -> Dict[str, Any]:
    """校验并归一化模型返回的 answers。

    比 Jev 真实响应更宽容的地方：多余的问题名会被忽略，字符串数字会被转成数字，
    概率和不为 1 会被按比例归一。更严格的地方：缺问题、选项不在 criteria 里、
    概率越界、score 越界，都会直接判失败。
    """

    if not isinstance(answers, dict):
        raise BadAnswers("answers is not an object")

    missing = [name for name in questions if name not in answers]
    if missing:
        raise BadAnswers(f"missing answers for: {missing}")

    result: Dict[str, Any] = {}
    for name, question in questions.items():
        item = answers.get(name)
        if not isinstance(item, dict):
            raise BadAnswers(f"answer {name!r} is not an object")
        kind = question.get("type")
        criteria = question.get("criteria")

        if kind == "noul":
            value = _check_range(_as_float(item.get("noul"), f"{name}.noul"), 0.0, 1.0, f"{name}.noul")
            result[name] = {"type": "noul", "noul": round(value, 4)}

        elif kind == "choice":
            if not isinstance(criteria, dict) or not criteria:
                raise BadAnswers(f"{name}: choice question needs a criteria map")
            picked = item.get("choice")
            if not isinstance(picked, str) or picked not in criteria:
                raise BadAnswers(
                    f"{name}.choice={picked!r} is not one of {sorted(criteria)}"
                )
            probabilities = _normalise_probabilities(
                item.get("probabilities"), sorted(criteria), name
            )
            confidence = item.get("confidence")
            if confidence is None:
                confidence = probabilities.get(picked, 0.5)
            else:
                confidence = _check_range(
                    _as_float(confidence, f"{name}.confidence"), 0.0, 1.0, f"{name}.confidence"
                )
            answer: Dict[str, Any] = {
                "type": "choice",
                "choice": picked,
                "confidence": round(float(confidence), 4),
            }
            if probabilities:
                answer["probabilities"] = probabilities
            result[name] = answer

        elif kind == "score":
            if not isinstance(criteria, list) or not criteria:
                raise BadAnswers(f"{name}: score question needs a criteria list")
            raw = _as_float(item.get("score"), f"{name}.score")
            high = float(len(criteria) - 1)
            # 量表边界是硬约束，但模型偶尔会略微越界；夹住它比直接失败更有用，
            # 因为 score 不参与放行判定，只用于留痕与后续阈值。
            value = min(max(raw, 0.0), high)
            levels = [str(index) for index in range(len(criteria))]
            probabilities = _normalise_probabilities(item.get("probabilities"), levels, name)
            answer = {"type": "score", "score": round(value, 4)}
            if probabilities:
                answer["probabilities"] = probabilities
            result[name] = answer

        else:
            raise BadAnswers(f"{name}: unsupported question type {kind!r}")

    return result


def split_answers(answers: Dict[str, Any]) -> Tuple[float, str, float]:
    """把三个答案抽成便于留痕的三元组。缺失的用 None 占位。"""

    block = (answers.get("block") or {}).get("noul")
    risk = (answers.get("risk_class") or {}).get("choice")
    blast = (answers.get("blast_radius") or {}).get("score")
    return block, risk, blast
