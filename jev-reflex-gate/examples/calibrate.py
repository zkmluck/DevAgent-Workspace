"""跑评测集，扫阈值，出一份报告。

    python examples/calibrate.py                          # 用 .env 里的客户端
    python examples/calibrate.py --client mock            # 离线跑，验证流程
    python examples/calibrate.py --client qwen --limit 5  # 先试几条
    python examples/calibrate.py --client qwen --write docs/eval.md
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from jev_reflex.config import load_settings
from jev_reflex.envfile import load_env
from jev_reflex.evalkit import (
    load_cases,
    load_outcomes,
    metrics_at,
    report,
    run_cases,
    save_outcomes,
    score,
    sweep,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="评测集与阈值校准")
    parser.add_argument("--cases", default=str(REPO_ROOT / "eval" / "cases.jsonl"))
    parser.add_argument("--client", default=None, help="mock / qwen / jev，默认读 .env")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条")
    parser.add_argument("--sleep", type=float, default=0.0, help="每条之间的间隔秒数")
    parser.add_argument("--write", default=None, help="把 Markdown 报告写到该路径")
    parser.add_argument(
        "--save",
        default=None,
        help="把模型的原始答案落盘（默认 eval/results-<client>.json）",
    )
    parser.add_argument(
        "--from",
        dest="replay",
        default=None,
        help="直接从落盘的结果继续（不再调用模型，纯离线扫阈值）",
    )
    parser.add_argument(
        "--thresholds",
        default=None,
        help="手动指定 allow_below,block_above,noise_above，例如 0.35,0.75,0.70",
    )
    args = parser.parse_args()

    source = load_env([REPO_ROOT / ".env", REPO_ROOT.parent / ".env"])
    settings = load_settings(client=args.client) if args.client else load_settings()
    if args.thresholds:
        allow_below, block_above, noise_above = (
            float(value) for value in args.thresholds.split(",")
        )
        settings = replace(
            settings,
            allow_below=allow_below,
            block_above=block_above,
            noise_above=noise_above,
        )
    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[: args.limit]

    print(f".env        : {source}")
    print(f"客户端       : {settings.client}")
    print(f"模型         : {settings.qwen_model if settings.client == 'qwen' else '—'}")
    print(f"评测集       : {args.cases}（{len(cases)} 条）")
    print(f"当前阈值     : allow_below={settings.allow_below}  "
          f"block_above={settings.block_above}  noise_above={settings.noise_above}")
    print()

    import time

    def show(outcome) -> None:
        block = "—" if outcome.block is None else f"{outcome.block:.2f}"
        noise = "—" if outcome.noise is None else f"{outcome.noise:.2f}"
        error = f"  [{outcome.decision.error}]" if outcome.failed else ""
        print(
            f"  {outcome.case.id:<16} 期望 {outcome.case.expect:<6} "
            f"block={block:<5} noise={noise:<5} risk={outcome.risk_class or '—':<18}"
            f"{outcome.case.note}{error}"
        )
        if args.sleep:
            time.sleep(args.sleep)

    if args.replay:
        outcomes = load_outcomes(args.replay)
        print(f"离线复用：{args.replay}（{len(outcomes)} 条，未调用模型）")
        print()
    else:
        outcomes = run_cases(cases, settings, limit=None, on_result=show)
        save_path = args.save or str(REPO_ROOT / "eval" / f"results-{settings.client}.json")
        if settings.client != "mock" or args.save:
            save_outcomes(outcomes, save_path)
            print()
            print(f"原始答案已落盘：{save_path}")

    current = metrics_at(
        outcomes, settings.allow_below, settings.block_above, settings.noise_above
    )
    print()
    print(f"当前阈值下的 score：{score(current)}（越小越好）")
    print(f"  误放行={current['false_allow']} 误拦={current['false_block']} "
          f"过度降级={current['over_escalation']} 该拦未拦={current['under_block']} "
          f"该升级却放行={current['under_escalation']} 正确={current['correct']}/{current['total']}")

    recommended, _ = sweep(outcomes)
    print()
    print(f"扫描出的推荐阈值：allow_below={recommended[0]:.2f}  "
          f"block_above={recommended[1]:.2f}  noise_above={recommended[2]:.2f}")
    rec = metrics_at(outcomes, *recommended)
    print(f"  误放行={rec['false_allow']} 误拦={rec['false_block']} "
          f"过度降级={rec['over_escalation']} 该拦未拦={rec['under_block']} "
          f"正确={rec['correct']}/{rec['total']}")

    if args.write:
        text = report(
            outcomes,
            settings.allow_below,
            settings.block_above,
            settings.noise_above,
            client=settings.client,
            model=settings.qwen_model if settings.client == "qwen" else "",
            cases_path=str(Path(args.cases).relative_to(REPO_ROOT)),
            recommended=recommended,
        )
        target = Path(args.write)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print()
        print(f"报告已写入：{target}")

    return 1 if current["false_allow"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
