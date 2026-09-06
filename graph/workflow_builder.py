"""LangGraph 工作流：把 6 个 Agent 编排成可执行流水线。"""

from __future__ import annotations

import operator
from pathlib import Path
from typing import Annotated, TypedDict

from agent.bug_detect_agent import BugDetectAgent
from agent.code_opt_agent import CodeOptAgent
from agent.doc_update_agent import DocUpdateAgent
from agent.pr_writer_agent import PRWriterAgent
from agent.repo_analyze_agent import RepoAnalyzeAgent
from agent.test_gen_agent import TestGenAgent


class WorkflowState(TypedDict):
    """LangGraph 流水线共享状态。logs 使用累加注解保证每一步日志都保留。"""

    repo_url: str
    owner: str
    repo: str
    repo_name: str
    repo_path: str
    branch: str
    logs: Annotated[list[str], operator.add]
    rag_context: str
    analysis: dict
    bugs: list[dict]
    bugs_summary: dict
    optimizations: list[dict]
    optimization_extra: str
    largest_files: list
    docs: str
    docs_llm_draft: str
    readme_missing_sections: list
    tests: str
    tests_llm: str
    py_file_count: int
    test_file_count: int
    pr: dict
    summary_md: str
    error: str


def _new_agent(agent_cls, state: dict):
    return agent_cls(
        repo_path=Path(state["repo_path"]),
        repo_name=state.get("repo_name", ""),
    )


def node_repo_analyze(state: dict) -> dict:
    return _new_agent(RepoAnalyzeAgent, state).run(state)


def node_bug_detect(state: dict) -> dict:
    return _new_agent(BugDetectAgent, state).run(state)


def node_code_opt(state: dict) -> dict:
    return _new_agent(CodeOptAgent, state).run(state)


def node_test_gen(state: dict) -> dict:
    return _new_agent(TestGenAgent, state).run(state)


def node_doc_update(state: dict) -> dict:
    return _new_agent(DocUpdateAgent, state).run(state)


def node_pr_write(state: dict) -> dict:
    return _new_agent(PRWriterAgent, state).run(state)


def build_workflow():
    """构建并编译 LangGraph 有向无环图。LangGraph 未安装时抛出 ImportError。"""
    from langgraph.graph import END, START, StateGraph

    workflow = StateGraph(WorkflowState)
    workflow.add_node("repo_analyze", node_repo_analyze)
    workflow.add_node("bug_detect", node_bug_detect)
    workflow.add_node("code_opt", node_code_opt)
    workflow.add_node("test_gen", node_test_gen)
    workflow.add_node("doc_update", node_doc_update)
    workflow.add_node("pr_write", node_pr_write)

    workflow.add_edge(START, "repo_analyze")
    workflow.add_edge("repo_analyze", "bug_detect")
    workflow.add_edge("bug_detect", "code_opt")
    workflow.add_edge("code_opt", "test_gen")
    workflow.add_edge("test_gen", "doc_update")
    workflow.add_edge("doc_update", "pr_write")
    workflow.add_edge("pr_write", END)

    return workflow.compile()
