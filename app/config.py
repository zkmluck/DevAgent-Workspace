"""全局配置：路径、环境变量和运行参数。

项目内所有模块统一从这里读取配置，避免散落魔法路径。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 优先加载项目根目录下的 .env；缺失时静默跳过
load_dotenv(PROJECT_ROOT / ".env")

REPO_DIR = PROJECT_ROOT / ".repos"
CHROMA_DIR = PROJECT_ROOT / ".chroma"
OUTPUT_DIR = PROJECT_ROOT / "output"

for _dir in (REPO_DIR, CHROMA_DIR, OUTPUT_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_API = "https://api.github.com"

# LLM（可选；不配置时全部 Agent 自动降级为本地规则分析）
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").strip()
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))

# 仓库抓取限制
MAX_REPO_FILES = int(os.getenv("MAX_REPO_FILES", "200"))

# 下载/扫描时统一忽略的依赖与构建目录
IGNORED_DIR_PARTS = frozenset({
    "node_modules", "vendor", "dist", "build", "target", ".git",
    "__pycache__", ".next", ".gradle", "site-packages", "coverage",
    ".venv", "venv", ".idea", ".cache", ".mypy_cache", ".pytest_cache",
})


def llm_configured() -> bool:
    """是否已配置可用的 LLM API Key。"""
    return bool(OPENAI_API_KEY)


def github_configured() -> bool:
    """是否已配置 GitHub Token（可显著提高 API 限额）。"""
    return bool(GITHUB_TOKEN)
