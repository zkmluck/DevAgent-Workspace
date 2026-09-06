"""缺陷检测 Agent：先用确定性规则扫描明显问题，有 LLM 时再做深度复查。"""

from __future__ import annotations

import re
from pathlib import Path

from agent.base_agent import BaseAgent
from app.config import llm_configured


class BugDetectAgent(BaseAgent):
    name = "bug_detect"

    def _check_python(self, rel: str, lines: list[str]) -> list[dict]:
        findings: list[dict] = []
        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if re.match(r"^except\s*:", stripped):
                findings.append({
                    "file": rel, "line": i, "severity": "high",
                    "message": "裸 except 会吞掉包括 KeyboardInterrupt 在内的所有异常，建议捕获具体异常类型",
                })
            elif re.match(r"^except\b[^\n]*:", stripped):
                # 捕获后立刻 pass 的吞异常写法
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and re.match(r"^(pass|continue)\b", lines[j].strip()):
                    findings.append({
                        "file": rel, "line": i, "severity": "high",
                        "message": "except 后直接 pass，异常被静默吞掉，排查困难",
                    })
            if re.search(r"=\s*None\b", stripped) or re.search(r"!=\s*None\b", stripped):
                findings.append({
                    "file": rel, "line": i, "severity": "medium",
                    "message": "建议使用 'is None' / 'is not None' 判断 None",
                })
            if re.search(r"\b(eval|exec)\s*\(", stripped):
                findings.append({
                    "file": rel, "line": i, "severity": "high",
                    "message": "eval/exec 存在代码注入风险，应避免执行不可信输入",
                })
            if "TODO" in stripped.upper() or "FIXME" in stripped.upper():
                findings.append({
                    "file": rel, "line": i, "severity": "low",
                    "message": "发现 TODO/FIXME，可能存在未完成或已知问题",
                })
        return findings

    def _check_js(self, rel: str, lines: list[str]) -> list[dict]:
        findings: list[dict] = []
        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if "debugger;" in stripped:
                findings.append({
                    "file": rel, "line": i, "severity": "high",
                    "message": "残留 debugger; 会中断浏览器执行",
                })
            if re.search(r"\beval\s*\(", stripped):
                findings.append({
                    "file": rel, "line": i, "severity": "high",
                    "message": "eval 存在 XSS/注入风险，应避免执行不可信字符串",
                })
            if "console.log" in stripped:
                findings.append({
                    "file": rel, "line": i, "severity": "low",
                    "message": "遗留 console.log 调试输出，上线前建议清理或替换为日志库",
                })
            if "TODO" in stripped.upper() or "FIXME" in stripped.upper():
                findings.append({
                    "file": rel, "line": i, "severity": "low",
                    "message": "发现 TODO/FIXME，可能存在未完成或已知问题",
                })
        return findings

    def _check_secrets(self, rel: str, lines: list[str]) -> list[dict]:
        findings: list[dict] = []
        pattern = re.compile(
            r"(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\s*[:=]\s*['\"][^'\"]{4,}['\"]",
            re.IGNORECASE,
        )
        for i, line in enumerate(lines, start=1):
            if pattern.search(line):
                findings.append({
                    "file": rel, "line": i, "severity": "high",
                    "message": "疑似硬编码密钥/口令，应立即改为环境变量或密钥管理服务",
                })
        return findings

    def run(self, state: dict) -> dict:
        findings: list[dict] = []
        for path, content in self.iter_text_files():
            rel = path.relative_to(self.repo_path).as_posix()
            lines = content.splitlines()
            suffix = path.suffix.lower()
            if suffix == ".py":
                findings.extend(self._check_python(rel, lines))
            elif suffix in (".js", ".jsx", ".ts", ".tsx"):
                findings.extend(self._check_js(rel, lines))
            findings.extend(self._check_secrets(rel, lines))

        # 去除同文件同行重复项，并限制数量避免报告过长
        unique = []
        seen = set()
        for item in findings:
            key = (item["file"], item["line"], item["message"])
            if key not in seen:
                seen.add(key)
                unique.append(item)
        bugs = unique[:60]

        llm_extra = ""
        if llm_configured() and bugs:
            context = self.build_context(max_files=6, max_chars=5000)
            rag = state.get("rag_context") or ""
            summary = "\n".join(
                f"- {b['file']}:{b['line']} [{b['severity']}] {b['message']}" for b in bugs[:20]
            )
            llm_extra = self.call_llm(
                system_prompt="你是资深代码审查专家。根据已有规则发现和代码上下文，补充最值得优先修复的问题，中文简洁输出。",
                user_prompt=(
                    f"仓库: {self.repo_name}\n已有发现:\n{summary}\n\n"
                    f"代码上下文:\n{context}\n\n向量检索片段:\n{rag[:6000] if rag else '(无)'}"
                ),
            ) or ""

        counts = {"high": 0, "medium": 0, "low": 0}
        for b in bugs:
            counts[b["severity"]] = counts.get(b["severity"], 0) + 1
        log_msg = (
            f"[bug_detect] 发现 {len(bugs)} 个疑点 "
            f"(high={counts['high']}, medium={counts['medium']}, low={counts['low']})"
        )
        return {
            "bugs": bugs,
            "bugs_summary": {**counts, "llm_extra": llm_extra},
            "logs": [log_msg],
        }
