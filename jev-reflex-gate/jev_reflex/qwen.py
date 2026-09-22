"""用 Qwen 充当 Jev 的本地替身。

走 OpenAI 兼容接口，所以百炼（DashScope）、Ollama、vLLM、LM Studio 都能接。
与真 Jev 的差别只有一处值得记住：**大模型会产出非法输出**。所以这里的每个
回答都必须先过 contract.validate_answers；不合格就抛 JevError("bad_response")，
由闸门退化成"待复核"——绝不把解析失败当成放行。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, Tuple

from .config import Settings
from .contract import BadAnswers, validate_answers
from .decide import JevError
from .prompts import SYSTEM_PROMPT, build_user_prompt

RETRYABLE_STATUS = frozenset({500, 502, 503, 504, 429})
_FENCE_HEAD = re.compile(r"^```[A-Za-z]*\s*")
_FENCE_TAIL = re.compile(r"\s*```$")


def _extract_json(text: str) -> Any:
    """从模型输出里取出 JSON。容忍代码围栏和前后废话，但不容忍没有 JSON。"""

    cleaned = (text or "").strip()
    cleaned = _FENCE_HEAD.sub("", cleaned)
    cleaned = _FENCE_TAIL.sub("", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise JevError("bad_response", "model output contained no JSON object") from None
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise JevError(
                "bad_response", f"model output was not valid JSON: {exc}"
            ) from None


class QwenJev:
    """与 JevClient 同形的客户端，换成让 Qwen 回答。"""

    name = "qwen"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def _endpoint(self) -> str:
        base = self.settings.qwen_base_url.rstrip("/")
        # 有人会把完整的 chat/completions 地址整条贴进来，这种情况直接用；
        # QWEN_BASE_URL 必须是 OpenAI 兼容的那条（通常以 /compatible-mode/v1 结尾），
        # 不是 DashScope 原生风格的 /api/v1。
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.qwen_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "jev-reflex-gate/0.1",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.settings.qwen_timeout_s
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:400]
            except Exception:  # pragma: no cover
                detail = ""
            if exc.code in (401, 403):
                raise JevError("invalid_key", detail or "qwen rejected the API key") from None
            if exc.code == 400:
                raise JevError("bad_request", detail or "qwen rejected the request") from None
            if exc.code in RETRYABLE_STATUS:
                raise JevError("server_error", detail or f"qwen returned {exc.code}") from None
            raise JevError("http_error", detail or f"unexpected status {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise JevError("unreachable", f"could not reach qwen: {exc}") from None
        except json.JSONDecodeError as exc:
            raise JevError("bad_response", f"qwen returned non-JSON: {exc}") from None

    def ask(
        self, state: str, questions: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if not self.settings.qwen_api_key:
            raise JevError(
                "missing_key",
                "environment variable QWEN_API_KEY (or DASHSCOPE_API_KEY) is not set",
            )

        base_payload: Dict[str, Any] = {
            "model": self.settings.qwen_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(state, questions)},
            ],
            "temperature": 0,
            "max_tokens": self.settings.qwen_max_tokens,
            "stream": False,
        }

        attempts: list[Dict[str, Any]] = []
        if self.settings.qwen_json_mode:
            attempts.append({**base_payload, "response_format": {"type": "json_object"}})
        attempts.append(base_payload)

        last_error: JevError | None = None
        for index, payload in enumerate(attempts):
            try:
                raw = self._post(payload)
                break
            except JevError as exc:
                last_error = exc
                # 有些兼容服务不接受 response_format，回退一次不带它的请求
                if exc.code == "bad_request" and index + 1 < len(attempts):
                    continue
                if exc.code in {"server_error", "unreachable"} and index + 1 < len(attempts):
                    continue
                raise
        else:  # pragma: no cover - 循环必然 break 或 raise
            raise last_error or JevError("unknown", "qwen request failed")

        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise JevError("bad_response", f"unexpected qwen response shape: {exc}") from None

        parsed = _extract_json(content)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("answers"), dict):
            raise JevError(
                "bad_response", "model output did not contain an 'answers' object"
            )

        try:
            answers = validate_answers(parsed["answers"], questions)
        except BadAnswers as exc:
            raise JevError("bad_response", f"contract violation: {exc}") from None

        usage_raw = raw.get("usage") or {}
        usage = {
            "input_tokens": usage_raw.get("prompt_tokens"),
            "output_tokens": usage_raw.get("completion_tokens"),
            "cost_usd": 0.0,  # 由百炼按自己的计费口径结算（免费额度内为 0）
            "provider": "qwen",
            "model": self.settings.qwen_model,
        }
        return answers, usage
