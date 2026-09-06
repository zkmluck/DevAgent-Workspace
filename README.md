# DevAgent-Workspace

基于 **LangGraph + FastAPI + Gradio + ChromaDB** 的多智能体开发工作台：
输入一个 GitHub 仓库地址，流水线自动完成仓库分析、缺陷检测、代码优化、
测试骨架生成、文档补全建议和 PR 草稿写作。

## 功能

- 6 个专职 Agent：仓库分析、缺陷检测、代码优化、测试生成、文档更新、PR 写作
- LangGraph 有向无环图编排；LangGraph 不可用时自动降级为本地顺序执行
- GitHub API 下载仓库，兼容默认分支，带本地缓存（`.repos/`）
- ChromaDB 把仓库文本分块入库，为 LLM 提供 RAG 检索（内置离线哈希向量器，无需下载模型）
- 可选“一键创建 GitHub PR”：产物提交到 `.devagent/` 目录的新分支，
  有 push 权限直接推原仓库，否则自动 fork 后发起 PR
- Gradio Web 工作台（默认 `http://127.0.0.1:7860`）
- FastAPI 文件读取接口（`main.py`，默认 `http://127.0.0.1:8000`）
- 无 OpenAI Key 也能完整运行：自动使用本地规则静态分析

## 环境

本项目固定使用 conda 环境 `py310`（Python 3.10）。

```bash
conda activate py310
python -m pip install -r requirements.txt
```

如果 `python` 仍指向其他解释器，请显式使用：

```powershell
E:\Anaconda\envs\py310\python.exe -m pip install -r requirements.txt
```

## 快速开始

1. 复制 `.env.example` 为 `.env`，按需填写 `GITHUB_TOKEN` 与 `OPENAI_API_KEY`。
2. 启动 Web 工作台：

```bash
python run.py
```

3. 浏览器打开 `http://127.0.0.1:7860`，粘贴仓库地址后点击
   “启动 Agent 流水线分析”。

   如要同时创建 GitHub PR，请先勾选“创建 GitHub PR”，并确保 `.env` 中
   的 `GITHUB_TOKEN` 具有 repo 写权限。

4. 如需单独读取仓库文件，也可使用 FastAPI 接口：

```bash
python main.py
curl "http://127.0.0.1:8000/read_file?repo_name=X-futur%2FerrAnalyst&file_path=README.md"
```

## 运行产物

每次流水线运行结束后，`output/<owner>__<repo>/` 下会生成：

- `analysis_report.md`：完整 Markdown 分析报告
- `pr_draft.md`：可直接编辑的 PR 描述
- `test_skeleton.py`：测试骨架
- `docs_draft.md`：文档补全草稿

仓库缓存位于 `.repos/`，Chroma 数据位于 `.chroma/`，两者都已加入 `.gitignore`。

## 目录结构

```text
.
├── agent/                  # 6 个 Agent + 编排器
│   ├── repo_analyze_agent.py
│   ├── bug_detect_agent.py
│   ├── code_opt_agent.py
│   ├── pr_writer_agent.py
│   ├── doc_update_agent.py
│   ├── test_gen_agent.py
│   └── agent_manager.py
├── app/config.py           # 全局配置（路径、Key、限制）
├── graph/workflow_builder.py  # LangGraph 状态图
├── github_api/repo_fetch.py   # GitHub API 下载/读取
├── github_api/pr_client.py    # PyGithub fork/分支/创建 PR
├── vector_db/chroma_client.py # ChromaDB 封装
├── ui/web_ui.py            # Gradio 界面
├── main.py                 # FastAPI /read_file 接口
└── run.py                  # Gradio 启动入口
```

## 常见问题

- **500 Internal Server Error**：旧版代码请求 `raw.githubusercontent.com` 会被网络拦截；
  现已改为走 `api.github.com`，见 `github_api/repo_fetch.py`。
- **GitHub API 限流**：匿名用户 60 次/小时，请在 `.env` 配置 `GITHUB_TOKEN`。
- **如何换模型**：修改 `.env` 中 `LLM_MODEL`；兼容 OpenAI 协议的网关改 `LLM_BASE_URL`。
