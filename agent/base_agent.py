"""Agent 公共基类：文件扫描、上下文拼装、可选 LLM 调用。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional

from app.config import (
    IGNORED_DIR_PARTS,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_TEMPERATURE,
    OPENAI_API_KEY,
    llm_configured,
)

TEXT_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs",
    ".c", ".h", ".cpp", ".cs", ".php", ".rb", ".swift", ".sh", ".ps1",
    ".sql", ".html", ".css", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".md", ".rst", ".txt", ".xml",
}


class BaseAgent:
    """所有流水线 Agent 的基类。

    子类只需实现 ``run(state) -> dict``，返回值会作为 LangGraph 的
    状态增量（例如 ``{"bugs": [...], "logs": ["..."]}``）。
    """

    name = "base"

    def __init__(self, repo_path: Path, repo_name: str = "") -> None:
        self.repo_path = Path(repo_path)
        self.repo_name = repo_name or self.repo_path.name

    # ---------- 文件工具 ----------
    def iter_text_files(self) -> Iterator[tuple[Path, str]]:
        for path in sorted(self.repo_path.rglob("*")):
            if not path.is_file() or path.name.startswith(".devagent"):
                continue
            if any(part.lower() in IGNORED_DIR_PARTS for part in path.relative_to(self.repo_path).parts[:-1]):
                continue
            if path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            yield path, content

    def text_file_count(self) -> int:
        return sum(1 for _ in self.iter_text_files())

    def build_context(self, max_files: int = 10, max_chars: int = 9000) -> str:
        """抽取仓库内若干代码/文档片段，拼成给 LLM 的上下文。"""
        parts: list[str] = []
        total = 0
        for idx, (path, content) in enumerate(self.iter_text_files()):
            if idx >= max_files or total >= max_chars:
                break
            try:
                rel = path.relative_to(self.repo_path).as_posix()
            except ValueError:
                rel = path.name
            snippet = content[:1200].rstrip()
            parts.append(f"### {rel}\n{snippet}")
            total += len(snippet)
        return "\n\n".join(parts) or "(仓库内未发现可读取的文本文件)"

    # ---------- LLM ----------
    def call_llm(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """有 Key 时调用 ChatOpenAI；无 Key 或失败时返回 None，由本地规则兜底。"""
        if not llm_configured():
            return None
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from langchain_openai import ChatOpenAI

            kwargs = {
                "model": LLM_MODEL,
                "temperature": LLM_TEMPERATURE,
                "api_key": OPENAI_API_KEY,
            }
            if LLM_BASE_URL:
                kwargs["base_url"] = LLM_BASE_URL
            llm = ChatOpenAI(**kwargs)
            response = llm.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
            )
            content = getattr(response, "content", "")
            return content if isinstance(content, str) else str(content)
        except Exception as exc:  # 网络/Key 问题不应中断流水线
            return f"[LLM 调用失败，已自动使用本地分析结果] {exc}"
