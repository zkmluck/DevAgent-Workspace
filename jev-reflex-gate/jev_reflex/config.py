"""阈值、端点与开关。全部可以用环境变量覆盖，便于校准。"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Dict

# 托管代理（第三方站点，无需 waitlist）
DEFAULT_ENDPOINT = "https://jevtypesafeai.com/api/v1/decide"
# 官方直连，请求结构相同
OFFICIAL_ENDPOINT = "https://api.typesafe.ai/v1/systemone"

# 内置默认值是"先验"，偏保守；实际部署用 `python examples/calibrate.py`
# 在真实场景上校准，校准结果写在 .env 里（见 docs/eval.md）。
DEFAULT_ALLOW_BELOW = 0.35
DEFAULT_BLOCK_ABOVE = 0.75


def _as_bool(raw: str) -> bool:
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


@dataclass(frozen=True)
class Settings:
    endpoint: str = DEFAULT_ENDPOINT
    api_key: str = ""
    timeout_s: float = 8.0
    max_retries: int = 1
    allow_below: float = DEFAULT_ALLOW_BELOW
    block_above: float = DEFAULT_BLOCK_ABOVE
    noise_above: float = 0.60
    client: str = "mock"
    audit_path: str = "output/reflex/audit.jsonl"
    enabled: bool = True
    # Qwen 替身：用大模型模拟 Jev 的类型化决策（走 OpenAI 兼容接口）
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_api_key: str = ""
    qwen_model: str = "qwen-plus"
    qwen_timeout_s: float = 30.0
    qwen_max_tokens: int = 1024
    qwen_json_mode: bool = True

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    @property
    def masked_key(self) -> str:
        """给日志和界面看的脱敏形态。"""

        if not self.api_key:
            return "(unset)"
        return self.api_key[:12] + "...(masked)"


def load_settings(**overrides: Any) -> Settings:
    """从环境变量读配置；显式传入的 overrides 优先。

    客户端默认是 mock，也就是离线假 Jev。要打真实 API 需要显式设置
    ``REFLEX_CLIENT=jev``——这是刻意的：没充值的 key 会让每次判决都退化成
    待复核，默认走 mock 更不容易误伤。
    """

    env: Dict[str, str] = dict(os.environ)
    base = Settings(
        endpoint=env.get("JEV_ENDPOINT", DEFAULT_ENDPOINT),
        api_key=env.get("JEV_API_KEY", ""),
        timeout_s=float(env.get("REFLEX_TIMEOUT_S", "8")),
        max_retries=int(env.get("REFLEX_MAX_RETRIES", "1")),
        allow_below=float(env.get("REFLEX_ALLOW_BELOW", str(DEFAULT_ALLOW_BELOW))),
        block_above=float(env.get("REFLEX_BLOCK_ABOVE", str(DEFAULT_BLOCK_ABOVE))),
        noise_above=float(env.get("REFLEX_NOISE_ABOVE", "0.6")),
        client=env.get("REFLEX_CLIENT", "mock").strip().lower(),
        audit_path=env.get("REFLEX_AUDIT_PATH", "output/reflex/audit.jsonl"),
        enabled=_as_bool(env.get("REFLEX_ENABLED", "1")),
        qwen_base_url=env.get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        qwen_api_key=env.get("QWEN_API_KEY") or env.get("DASHSCOPE_API_KEY", ""),
        qwen_model=env.get("QWEN_MODEL", "qwen-plus"),
        qwen_timeout_s=float(env.get("QWEN_TIMEOUT_S", "30")),
        qwen_max_tokens=int(env.get("QWEN_MAX_TOKENS", "1024")),
        qwen_json_mode=_as_bool(env.get("QWEN_JSON_MODE", "1")),
    )
    clean = {key: value for key, value in overrides.items() if value is not None}
    return replace(base, **clean) if clean else base
