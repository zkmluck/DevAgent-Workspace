"""文档更新 Agent：检查 README 缺口并生成文档草稿。"""

from __future__ import annotations

from agent.base_agent import BaseAgent
from app.config import llm_configured

REQUIRED_SECTIONS = [
    ("installation", ["安装", "installation", "pip install", "quick start", "快速开始", "getting started"]),
    ("usage", ["使用", "usage", "示例", "example", "demo"]),
    ("configuration", ["配置", "configuration", "环境变量", "env", "settings"]),
    ("testing", ["测试", "testing", "pytest", "unittest"]),
    ("contributing", ["贡献", "contributing", "参与开发"]),
    ("license", ["license", "许可", "协议"]),
]


class DocUpdateAgent(BaseAgent):
    name = "doc_update"

    def run(self, state: dict) -> dict:
        readme_path = None
        readme_content = ""
        for path, content in self.iter_text_files():
            if path.name.lower() == "readme.md":
                readme_path = path
                readme_content = content
                break

        lower = readme_content.lower()
        missing = [key for key, words in REQUIRED_SECTIONS if not any(w in lower for w in words)]

        analysis = state.get("analysis", {})
        frameworks = ", ".join(analysis.get("framework_hints", []) or ["未识别"])
        text_files = analysis.get("text_file_count", "?")

        outline = (
            f"# {self.repo_name}\n\n"
            f"> 由 DevAgent-Workspace 文档 Agent 自动生成/建议。\n\n"
            f"## 项目简介\n本项目共扫描到 {text_files} 个文本文件，识别到的主要技术栈：{frameworks}。\n\n"
            f"## 安装 Installation\n```bash\npip install -r requirements.txt\n```\n\n"
            f"## 快速开始 Usage\n```bash\npython run.py\n```\n\n"
            f"## 配置 Configuration\n请根据项目实际需要补充环境变量说明。\n\n"
            f"## 测试 Testing\n建议补充自动化测试并说明运行方式。\n\n"
            f"## 贡献 Contributing\n说明分支规范与提交格式。\n\n"
            f"## License\n请补充许可证信息。\n"
        )

        if readme_path is not None:
            head = "\n".join(readme_content.splitlines()[:12])
            if missing:
                docs = (
                    f"### 现状\nREADME 已有内容（前 12 行）：\n\n```markdown\n{head}\n```\n\n"
                    f"### 建议补充/完善以下章节\n{', '.join(missing)}\n\n"
                    "### 文档草稿\n```markdown\n" + outline + "```\n"
                )
                log_msg = f"[doc_update] README 缺少章节: {', '.join(missing)}，已生成补全草稿"
            else:
                docs = f"### 现状\nREADME 已覆盖主要章节，无需大幅补写。\n\n```markdown\n{head}\n```\n"
                log_msg = "[doc_update] README 章节完整，无需补写"
        else:
            docs = outline
            log_msg = "[doc_update] 未发现 README.md，已生成完整 README 草稿"

        llm_draft = ""
        if llm_configured():
            llm_draft = self.call_llm(
                system_prompt="你是开源项目文档工程师，根据仓库代码生成结构清晰、简洁的 README 初稿（Markdown，中文）。",
                user_prompt=f"仓库: {self.repo_name}\n代码片段:\n{self.build_context(max_files=5, max_chars=4000)}\n\n技术栈: {frameworks}",
            ) or ""

        return {
            "docs": docs,
            "docs_llm_draft": llm_draft,
            "readme_missing_sections": missing,
            "logs": [log_msg],
        }
