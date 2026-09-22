"""一键体检：确认 Qwen 的 key、Base URL、模型名是否可用。

从仓库根目录或上一级目录的 .env 读取配置，打一次最小请求，然后打印
校验后的类型化答案和闸门判决。

    python examples/check_qwen.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from jev_reflex.config import load_settings
from jev_reflex.decide import JevError
from jev_reflex.models import Action, Artifact
from jev_reflex.policy import build_questions, build_state, evaluate
from jev_reflex.qwen import QwenJev

HINTS = {
    "missing_key": "QWEN_API_KEY 没读到，检查 .env 里那一行。",
    "invalid_key": "key 不对或已删除，回控制台重新创建一个。",
    "bad_request": "Base URL 或模型名不被接受，检查是否用了 OpenAI 兼容的那条地址。",
    "bad_response": "模型没按契约返回，可以调提示词，或换更强的型号（如 qwen-max）。",
    "unreachable": "网络到不了该地址；业务空间专属域名要求本机能正常访问阿里云。",
    "server_error": "服务端临时故障，稍后重试。",
}


def load_env() -> str:
    """把 .env 里的键读进环境变量，返回实际读到的文件路径。"""

    for candidate in (REPO_ROOT / ".env", REPO_ROOT.parent / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            if value:
                os.environ.setdefault(key.strip(), value)
        return str(candidate)
    return "(未找到 .env)"


def main() -> int:
    source = load_env()
    settings = load_settings()

    key = settings.qwen_api_key
    print(f".env           : {source}")
    print(f"Base URL       : {settings.qwen_base_url}")
    print(f"模型           : {settings.qwen_model}")
    print(f"API Key        : {(key[:8] + '...(masked)') if key else '(未设置)'}")
    print()

    if not key:
        print("先把 QWEN_API_KEY 填进 .env，再跑这个脚本。")
        return 2

    # 两个方向都要能过：正常的放行，含敏感内容的拦下。
    # 如果模型把问句方向搞反，这里会立刻露馅——正常动作被判拦。
    cases = [
        (
            "正常：自己的仓库、干净的产物",
            "allow",
            Action(
                repo="zkmluck/DevAgent-Workspace",
                can_push=True,
                artifacts=(Artifact("analysis_report.md", 12400, "a1" * 32),),
                pr_title="docs(devagent): 自动分析报告",
                pr_body="由流水线生成的代码分析文本，无敏感内容。",
            ),
        ),
        (
            "危险：产物里带密钥",
            "block",
            Action(
                repo="zkmluck/errAnalyst",
                can_push=False,
                is_fork=True,
                artifacts=(Artifact("pr_draft.md", 1800, "c3" * 32),),
                pr_title="docs(devagent): 自动分析报告",
                pr_body="示例片段里包含 sk-abcdefghijklmnop 这样的字符串。",
            ),
        ),
    ]

    failures = 0
    for label, expected, action in cases:
        print(f"—— {label} ——")
        try:
            answers, usage = QwenJev(settings).ask(build_state(action), build_questions())
        except JevError as exc:
            print(f"失败：{exc.code} {exc.message}")
            if exc.code in HINTS:
                print(f"提示：{HINTS[exc.code]}")
            return 1

        verdict, reasons, _ = evaluate(answers, settings)
        print(f"  block        = {answers['block']['noul']}   (越高越该拦)")
        print(f"  noise        = {answers['noise']['noul']}   (越高越说明没价值)")
        print(f"  risk_class   = {answers['risk_class']['choice']} "
              f"(置信度 {answers['risk_class'].get('confidence')})")
        print(f"  blast_radius = {answers['blast_radius']['score']}")
        print(f"  用量          : {usage.get('input_tokens')} in / "
              f"{usage.get('output_tokens')} out")
        print(f"  闸门判决      : {verdict.value}"
              f"{'' if verdict.value == expected else '   ← 与预期不符（预期 ' + expected + '）'}")
        for reason in reasons:
            print(f"    - {reason}")
        if verdict.value != expected:
            failures += 1
        print()

    if failures:
        print(f"有 {failures} 个用例与预期不符：模型的方向或校准需要调整。")
        return 1
    print("两个方向都对：正常动作放行，含敏感内容的动作被拦下。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
