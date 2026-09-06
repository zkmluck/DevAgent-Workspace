"""测试生成 Agent：为仓库生成可直接落地的 pytest 骨架。"""

from __future__ import annotations

import re

from agent.base_agent import BaseAgent
from app.config import llm_configured


def _safe_name(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", path).strip("_") or "module"


class TestGenAgent(BaseAgent):
    name = "test_gen"

    def run(self, state: dict) -> dict:
        py_files = [
            path.relative_to(self.repo_path).as_posix()
            for path, _content in self.iter_text_files()
            if path.suffix.lower() == ".py" and "/test" not in path.as_posix().lower()
        ]
        test_files = [
            path.relative_to(self.repo_path).as_posix()
            for path, _content in self.iter_text_files()
            if "/test" in path.as_posix().lower() or path.name.startswith("test_")
        ]

        if py_files:
            samples = py_files[:20]
            funcs = []
            for p in samples:
                funcs.append(
                    f"def test_parse_{_safe_name(p)}():\n"
                    f"    \"\"\"语法冒烟测试：确保 {p} 可以被 Python 正确解析。\"\"\"\n"
                    f"    ast.parse((REPO_ROOT / {p!r}).read_text(encoding='utf-8'))\n"
                )
            skeleton = (
                '"""由 DevAgent-Workspace test_gen_agent 自动生成。"""\n'
                "import ast\n"
                "import pathlib\n\n"
                "# 请将本文件保存为 <仓库根>/tests/test_devagent_generated.py 后运行：pytest -q tests/\n"
                "# REPO_ROOT 按 tests/ 位于仓库根目录一层来计算\n"
                "REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]\n\n"
                + "\n".join(funcs)
            )
            log_msg = f"[test_gen] 为 {len(py_files)} 个 Python 模块生成冒烟测试骨架（示例见 tests/ 说明）"
        else:
            skeleton = (
                "# 仓库中没有可识别的 Python 源码，测试 Agent 仅提供通用模板。\n"
                "# 可参考 https://docs.pytest.org 为你的语言/框架补充测试。\n"
            )
            log_msg = "[test_gen] 未发现 Python 源码，输出通用测试模板"

        llm_tests = ""
        if llm_configured() and py_files:
            llm_tests = self.call_llm(
                system_prompt="你是测试工程师，请为给定仓库生成 pytest 测试（中文注释），优先覆盖核心模块，输出纯代码。",
                user_prompt=f"仓库: {self.repo_name}\n文件列表:\n" + "\n".join(py_files[:30]) + "\n\n代码上下文:\n" + self.build_context(max_files=5, max_chars=5000),
            ) or ""

        return {
            "tests": skeleton,
            "tests_llm": llm_tests,
            "py_file_count": len(py_files),
            "test_file_count": len(test_files),
            "logs": [log_msg],
        }
