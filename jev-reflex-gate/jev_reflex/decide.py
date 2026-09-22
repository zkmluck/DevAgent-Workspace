"""Jev Decision API 的最小客户端。只用标准库，不引入任何依赖。

刻意不做三件事：

* 不在鉴权错误（401/402）上重试——那是钱包和配置问题，重试只会重复失败；
* 不把 key 写进任何日志或异常消息；
* 不解析自由文本——答案的形状由请求固定。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Tuple

from .config import Settings

RETRYABLE_STATUS = frozenset({500, 502, 503, 504, 429})


class JevError(RuntimeError):
    """带机器可读 code 的客户端错误，便于闸门决定怎么降级。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _classify_http(status: int, payload: str) -> JevError:
    detail = payload.strip()[:300]
    if status == 401:
        return JevError("invalid_key", detail or "invalid or revoked API key")
    if status == 402:
        return JevError(
            "insufficient_credits",
            detail or "prepaid balance is empty; top up on the pricing page",
        )
    if status == 429:
        return JevError("rate_limited", detail or "rate limited")
    if status in RETRYABLE_STATUS:
        return JevError("server_error", detail or f"server returned {status}")
    return JevError("http_error", detail or f"unexpected status {status}")


class JevClient:
    """真实 API 客户端。"""

    name = "jev"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def ask(
        self, state: str, questions: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """返回 (answers, usage)。任何失败都抛 JevError。"""

        if not self.settings.api_key:
            raise JevError("missing_key", "environment variable JEV_API_KEY is not set")

        body = json.dumps(
            {"state": state, "questions": questions}, ensure_ascii=False
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "jev-reflex-gate/0.1",
        }

        attempts = max(1, self.settings.max_retries + 1)
        last: BaseException | None = None
        for attempt in range(attempts):
            request = urllib.request.Request(
                self.settings.endpoint, data=body, headers=headers, method="POST"
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.settings.timeout_s
                ) as response:
                    raw = response.read().decode("utf-8")
                parsed = json.loads(raw)
                return parsed.get("answers", {}) or {}, parsed.get("usage", {}) or {}
            except urllib.error.HTTPError as exc:
                try:
                    detail = exc.read().decode("utf-8", errors="replace")
                except Exception:  # pragma: no cover - 极端情况下读不到 body
                    detail = ""
                error = _classify_http(exc.code, detail)
                if exc.code in RETRYABLE_STATUS and attempt + 1 < attempts:
                    last = error
                    continue
                raise error from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                error = JevError("unreachable", f"could not reach endpoint: {exc}")
                if attempt + 1 < attempts:
                    last = error
                    continue
                raise error from None
            except json.JSONDecodeError as exc:
                raise JevError("bad_response", f"response was not JSON: {exc}") from None

        raise last if isinstance(last, JevError) else JevError("unknown", "request failed")
