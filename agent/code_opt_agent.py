"""代码优化 Agent：给出可执行的结构化优化建议。"""

from __future__ import annotations

import re

from agent.base_agent import BaseAgent
from app.config import llm_configured


class CodeOptAgent(BaseAgent):
    name = "code_opt"

    def run(self, state: dict) -> dict:
        suggestions: list[dict] = []
        largest = []

        for path, content in self.iter_text_files():
            rel = path.relative_to(self.repo_path).as_posix()
            lines = content.splitlines()
            size = len(lines)
            largest.append((size, rel))

            if size > 500:
                suggestions.append({
                    "file": rel, "type": "大文件",
                    "message": f"文件有 {size} 行，建议按模块/函数拆分，便于测试和维护",
                })
            elif size > 300:
                suggestions.append({
                    "file": rel, "type": "中大型文件",
                    "message": f"文件有 {size} 行，可评估是否拆分或补充模块级 docstring",
                })

            if path.suffix.lower() == ".py":
                long_funcs = 0
                for i, line in enumerate(lines):
                    if re.match(r"^\s*def\s+", line):
                        block = 0
                        for j in range(i, min(i + 400, len(lines))):
                            if re.match(r"^\s*def\s+", lines[j]) and j != i:
                                break
                            block += 1
                        if block > 80:
                            long_funcs += 1
                if long_funcs:
                    suggestions.append({
                        "file": rel, "type": "过长函数",
                        "message": f"检测到约 {long_funcs} 个超长函数/方法，建议按单一职责拆分",
                    })

        top_suggestions = suggestions[:30]

        has_gitignore = any(p.name == ".gitignore" for p in self.repo_path.rglob("*") if p.is_file())
        if not has_gitignore:
            top_suggestions.append({
                "file": "/", "type": "工程规范",
                "message": "仓库缺少 .gitignore，建议补充并排除 .venv/ .chroma/ .repos/ output/ 等目录",
            })

        has_readme = any(p.name.lower() == "readme.md" for p in self.repo_path.rglob("*") if p.is_file())
        if not has_readme:
            top_suggestions.append({
                "file": "/", "type": "文档",
                "message": "缺少 README.md，建议补充安装、使用、配置说明",
            })

        llm_extra = ""
        if llm_configured() and top_suggestions:
            context = self.build_context(max_files=6, max_chars=5000)
            text = "\n".join(f"- {s['file']} [{s['type']}] {s['message']}" for s in top_suggestions[:15])
            llm_extra = self.call_llm(
                system_prompt="你是性能与代码质量专家，请结合代码上下文补充最重要的优化建议，中文简洁输出。",
                user_prompt=f"仓库: {self.repo_name}\n规则建议:\n{text}\n\n代码上下文:\n{context}",
            ) or ""

        log_msg = f"[code_opt] 生成 {len(top_suggestions)} 条优化建议"
        return {
            "optimizations": top_suggestions,
            "optimization_extra": llm_extra,
            "largest_files": sorted(largest, reverse=True)[:5],
            "logs": [log_msg],
        }
