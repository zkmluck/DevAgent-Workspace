"""Gradio Web 界面：输入仓库地址，一键运行多 Agent 流水线。"""

from __future__ import annotations

import gradio as gr

from agent.agent_manager import AgentManager

_manager = AgentManager(use_rag=True)


def _run_pipeline(
    repo_url: str, branch: str, refresh: bool, create_pr: bool
) -> tuple[str, str]:
    try:
        state = _manager.run_repo(
            repo_url,
            branch=branch.strip(),
            force=refresh,
            create_pr=create_pr,
        )
        logs = "\n".join(state.get("logs", []))
        logs += f"\n\n产物目录: {state.get('artifacts_dir', '')}"
        return logs, state.get("summary_md", "")
    except Exception as exc:
        return f"[运行失败] {exc}", f"## 运行失败\n\n```text\n{exc}\n```"


def _read_file(repo_url: str, file_path: str, branch: str) -> str:
    try:
        return _manager.read_file(repo_url, file_path, branch=branch.strip())
    except Exception as exc:
        return f"[读取失败] {exc}"


def create_ui():
    with gr.Blocks(title="DevAgent Workspace") as demo:
        gr.Markdown(
            "# DevAgent Workspace 多智能体开发工作台\n"
            "输入 GitHub 仓库地址，依次执行：仓库分析 → 缺陷检测 → 代码优化 → "
            "测试生成 → 文档更新 → PR 写作。未配置 LLM Key 时自动使用本地规则分析。"
        )

        repo_input = gr.Textbox(
            label="GitHub 仓库地址",
            placeholder="https://github.com/owner/repo",
            value="https://github.com/X-futur/errAnalyst",
        )
        with gr.Row():
            branch_input = gr.Textbox(label="分支（留空自动识别）", placeholder="main")
            refresh_checkbox = gr.Checkbox(label="强制重新下载仓库", value=False)
            pr_checkbox = gr.Checkbox(
                label="创建 GitHub PR（需 .env 配置 GITHUB_TOKEN）",
                value=False,
            )

        with gr.Row():
            run_btn = gr.Button("启动 Agent 流水线分析", variant="primary")
            file_read_btn = gr.Button("读取仓库文件")

        with gr.Row():
            file_path_input = gr.Textbox(
                label="要读取的文件路径",
                value="README.md",
                scale=3,
            )
            file_read_branch = gr.Textbox(label="读取用分支（可留空）", scale=1)

        log_box = gr.Textbox(label="运行日志", lines=12, max_lines=30)
        result_md = gr.Markdown(label="分析结果")
        file_box = gr.Textbox(label="文件内容", lines=12, max_lines=30)

        run_btn.click(
            fn=_run_pipeline,
            inputs=[repo_input, branch_input, refresh_checkbox, pr_checkbox],
            outputs=[log_box, result_md],
        )
        file_read_btn.click(
            fn=_read_file,
            inputs=[repo_input, file_path_input, file_read_branch],
            outputs=[file_box],
        )
    return demo
