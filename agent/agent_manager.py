"""Agent 编排器：仓库准备 → 向量索引 → LangGraph/本地降级运行 → 报告生成。"""

from __future__ import annotations

from pathlib import Path

from agent.bug_detect_agent import BugDetectAgent
from agent.code_opt_agent import CodeOptAgent
from agent.doc_update_agent import DocUpdateAgent
from agent.pr_writer_agent import PRWriterAgent
from agent.repo_analyze_agent import RepoAnalyzeAgent
from agent.test_gen_agent import TestGenAgent
from app.config import OUTPUT_DIR
from github_api.repo_fetch import (
    GitHubError,
    fetch_repo,
    parse_repo_url,
    read_repo_file,
)
from github_api.pr_client import PRCreationError, create_github_pr
from vector_db.chroma_client import RepoVectorDB


def _finding_line(item: dict) -> str:
    return (
        f"- [{item.get('severity', 'info')}] `{item.get('file', '')}`"
        f"{(':' + str(item.get('line', ''))) if item.get('line') else ''}: {item.get('message', '')}"
    )


def build_report_markdown(state: dict) -> str:
    """把流水线状态渲染成一份可直接阅读的 Markdown 报告。"""
    lines = [f"# DevAgent-Workspace 分析报告", ""]
    analysis = state.get("analysis") or {}
    lines.append(f"**仓库**: {state.get('repo_name', '')}  |  **分支**: {state.get('branch', '')}")
    lines += [
        "",
        f"**文本文件数**: {analysis.get('text_file_count', '?')}  |  "
        f"**代码行数**: {analysis.get('total_lines', '?')}",
        "",
    ]

    if analysis.get("llm_summary"):
        lines += ["## 架构摘要（LLM）", analysis["llm_summary"], ""]

    if analysis.get("framework_hints"):
        lines += ["## 技术栈线索", ", ".join(analysis["framework_hints"]), ""]

    bugs = state.get("bugs") or []
    if bugs:
        lines += ["## 缺陷疑点", ""]
        lines += [_finding_line(b) for b in bugs]
        lines += [""]
    if state.get("bugs_summary", {}).get("llm_extra"):
        lines += ["### LLM 补充", state["bugs_summary"]["llm_extra"], ""]

    optimizations = state.get("optimizations") or []
    if optimizations:
        lines += ["## 优化建议", ""]
        lines += [
            f"- `{o.get('file', '')}` [{o.get('type', '')}] {o.get('message', '')}"
            for o in optimizations
        ]
        lines += [""]
    if state.get("optimization_extra"):
        lines += ["### LLM 补充", state["optimization_extra"], ""]

    docs = state.get("docs") or ""
    if docs:
        lines += ["## 文档建议", docs, ""]

    tests = state.get("tests") or ""
    if tests:
        lines += ["## 测试骨架", "```python", tests, "```", ""]

    pr = state.get("pr") or {}
    if pr:
        lines += ["## PR 草稿", f"**标题**: {pr.get('title', '')}", "", pr.get("body", ""), ""]

    if state.get("error"):
        lines += ["## 错误", state["error"], ""]
    return "\n".join(lines).rstrip() + "\n"


def _agent_steps():
    return [
        RepoAnalyzeAgent,
        BugDetectAgent,
        CodeOptAgent,
        TestGenAgent,
        DocUpdateAgent,
        PRWriterAgent,
    ]


class AgentManager:
    """对外统一入口，UI 层只依赖这一个类。"""

    def __init__(self, use_rag: bool = True) -> None:
        self.vector_db = RepoVectorDB() if use_rag else None

    def _initial_state(self, repo_url: str, snapshot) -> dict:
        owner, repo = parse_repo_url(repo_url)
        return {
            "repo_url": repo_url,
            "owner": owner,
            "repo": repo,
            "repo_name": snapshot.repo_name,
            "repo_path": str(snapshot.root),
            "branch": snapshot.branch,
            "logs": [f"[manager] 仓库就绪: {snapshot.repo_name} @ {snapshot.branch}（{snapshot.file_count} 个文件，缓存于 {snapshot.root}）"],
            "rag_context": "",
            "analysis": {},
            "bugs": [],
            "bugs_summary": {},
            "optimizations": [],
            "optimization_extra": "",
            "largest_files": [],
            "docs": "",
            "docs_llm_draft": "",
            "readme_missing_sections": [],
            "tests": "",
            "tests_llm": "",
            "py_file_count": 0,
            "test_file_count": 0,
            "pr": {},
            "summary_md": "",
            "error": "",
        }

    def _save_artifacts(self, state: dict) -> str:
        """把报告/PR/测试草稿落到 output/ 目录，方便直接取用。"""
        out_root = OUTPUT_DIR / f"{state['owner']}__{state['repo']}"
        out_root.mkdir(parents=True, exist_ok=True)

        (out_root / "analysis_report.md").write_text(
            state.get("summary_md", ""), encoding="utf-8"
        )
        pr = state.get("pr") or {}
        if pr.get("body"):
            (out_root / "pr_draft.md").write_text(
                f"# {pr.get('title', '')}\n\n{pr.get('body', '')}\n", encoding="utf-8"
            )
        if state.get("tests"):
            (out_root / "test_skeleton.py").write_text(state["tests"], encoding="utf-8")
        if state.get("docs"):
            (out_root / "docs_draft.md").write_text(state["docs"], encoding="utf-8")
        return str(out_root)

    def run_repo(
        self,
        repo_url: str,
        branch: str = "",
        force: bool = False,
        use_rag: bool = True,
        create_pr: bool = False,
    ) -> dict:
        """执行完整流水线，返回最终状态（含 summary_md 和 artifacts_dir）。"""
        snapshot = fetch_repo(repo_url, branch=branch, force=force)
        state = self._initial_state(repo_url, snapshot)

        if use_rag and self.vector_db and self.vector_db.available:
            try:
                chunk_count = self.vector_db.index_directory(snapshot.root, snapshot.repo_name)
                state["logs"].append(f"[vector_db] 已将仓库文本写入 Chroma（{chunk_count} 个分块）")
                hits = self.vector_db.query(
                    snapshot.repo_name, "项目的主要功能、架构与关键模块", k=8
                )
                if hits:
                    snippets = []
                    for hit in hits:
                        text = (hit.get("text") or "")[:900].strip()
                        if text:
                            snippets.append(f"### {hit.get('path', '')}\n{text}")
                    state["rag_context"] = "\n\n".join(snippets)
            except Exception as exc:
                state["logs"].append(f"[vector_db] 索引失败，已跳过 RAG: {exc}")

        try:
            from graph.workflow_builder import build_workflow

            graph = build_workflow()
            final = graph.invoke(state)
        except Exception as exc:
            final = self._fallback_run(state)
            if not final.get("error"):
                final["error"] = f"LangGraph 运行失败，已使用本地顺序模式完成: {exc}"
                final["logs"].append(f"[manager] 降级为本地顺序模式: {exc}")

        final["logs"] = final.get("logs", [])
        final["summary_md"] = build_report_markdown(final)
        final["artifacts_dir"] = self._save_artifacts(final)

        if create_pr:
            pr_data = final.get("pr") or {}
            try:
                pr_info = create_github_pr(
                    repo_url,
                    final["artifacts_dir"],
                    title=pr_data.get("title", ""),
                    body=pr_data.get("llm_body") or pr_data.get("body", ""),
                    base_branch=final.get("branch", ""),
                )
                pr_data.update(pr_info)
                final["logs"].append(
                    f"[github_pr] 已创建 PR #{pr_info['pr_number']}: {pr_info['html_url']}"
                )
            except Exception as exc:
                pr_data["github_error"] = str(exc)
                final["logs"].append(f"[github_pr] 创建 PR 失败: {exc}")
        return final

    def _fallback_run(self, state: dict) -> dict:
        """LangGraph 不可用时按同样顺序执行，保证功能不依赖额外编排包。"""
        final = dict(state)
        for agent_cls in _agent_steps():
            try:
                agent = agent_cls(
                    repo_path=Path(state["repo_path"]),
                    repo_name=state.get("repo_name", ""),
                )
                update = agent.run(final)
                final.update(update)
            except Exception as exc:
                final["error"] = f"{agent_cls.__name__} 执行失败: {exc}"
                final["logs"].append(f"[manager] {agent_cls.__name__} 执行失败: {exc}")
        return final

    def read_file(self, repo_url: str, file_path: str, branch: str = "") -> str:
        """读取仓库文件（优先本地缓存）。"""
        return read_repo_file(repo_url, file_path, branch=branch)
