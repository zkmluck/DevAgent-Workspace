"""仓库分析 Agent：摸清项目结构、技术栈、关键文件与框架线索。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from agent.base_agent import BaseAgent
from app.config import llm_configured

FRAMEWORK_KEYWORDS = {
    "fastapi": "FastAPI", "flask": "Flask", "django": "Django",
    "torch": "PyTorch", "tensorflow": "TensorFlow", "pandas": "Pandas",
    "numpy": "NumPy", "react": "React", "vue": "Vue", "svelte": "Svelte",
    "spring": "Spring", "express": "Express", "gradio": "Gradio",
    "streamlit": "Streamlit", "chromadb": "ChromaDB", "langchain": "LangChain",
}


class RepoAnalyzeAgent(BaseAgent):
    name = "repo_analyze"

    def _scan(self) -> dict:
        ext_counter: Counter = Counter()
        file_list: list[str] = []
        total_lines = 0
        top_level: Counter = Counter()

        for path, content in self.iter_text_files():
            rel = path.relative_to(self.repo_path).as_posix()
            file_list.append(rel)
            ext_counter[path.suffix.lower() or "(no ext)"] += 1
            total_lines += content.count("\n") + 1
            first = rel.split("/", 1)[0]
            if first not in (".git",):
                top_level[first] += 1

        readme_head = ""
        for path, content in self.iter_text_files():
            if path.name.lower() in ("readme.md", "readme.rst", "readme.txt"):
                readme_head = content[:800]
                break

        haystack = ""
        for _, content in self.iter_text_files():
            haystack += content[:3000].lower()
            if len(haystack) > 200_000:
                break
        hints = [name for key, name in FRAMEWORK_KEYWORDS.items() if key in haystack]

        return {
            "text_file_count": len(file_list),
            "total_lines": total_lines,
            "top_level_entries": dict(top_level.most_common(15)),
            "extension_stats": dict(ext_counter.most_common(12)),
            "notable_files": sorted(
                [
                    f
                    for f in file_list
                    if f.lower().endswith(("readme.md", "setup.py", "pyproject.toml", "package.json", "requirements.txt", "dockerfile"))
                ]
            )[:20],
            "framework_hints": hints,
            "readme_head": readme_head,
        }

    def run(self, state: dict) -> dict:
        scan = self._scan()
        summary = ""
        if llm_configured():
            context = self.build_context(max_files=8, max_chars=6000)
            rag = state.get("rag_context") or ""
            summary = self.call_llm(
                system_prompt="你是资深代码架构师，请用简洁中文总结给定仓库的技术栈、模块职责和值得注意的设计点。",
                user_prompt=(
                    f"仓库: {self.repo_name}\n"
                    f"文本文件数: {scan['text_file_count']}, 框架线索: {scan['framework_hints']}\n"
                    f"文件样例:\n{context}\n\n"
                    f"向量检索片段:\n{rag[:6000] if rag else '(无)'}"
                ),
            ) or ""

        analysis = {
            "repo_name": self.repo_name,
            **scan,
            "llm_summary": summary,
        }
        top_ext = ", ".join(f"{ext}({n})" for ext, n in list(scan["extension_stats"].items())[:5])
        log_msg = (
            f"[repo_analyze] 扫描到 {scan['text_file_count']} 个文本文件 / "
            f"{scan['total_lines']} 行；主要类型: {top_ext or '无'}"
        )
        return {"analysis": analysis, "logs": [log_msg]}
