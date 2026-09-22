"""评测工具本身的测试：加载、统计口径、阈值扫描的方向。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jev_reflex.config import Settings
from jev_reflex.evalkit import (
    Case,
    Outcome,
    action_from_dict,
    load_cases,
    load_outcomes,
    metrics_at,
    run_cases,
    save_outcomes,
    sweep,
    verdict_at,
)
from jev_reflex.models import Action, Artifact, Decision, Verdict


def outcome(case_id: str, expect: str, block: float, risk: str, noise: float = 0.05) -> Outcome:
    case = Case(id=case_id, expect=expect, category="x", action=Action(repo="a/b"))
    decision = Decision(
        verdict=Verdict.REVIEW,
        answers={
            "block": {"type": "noul", "noul": block},
            "noise": {"type": "noul", "noul": noise},
            "risk_class": {"type": "choice", "choice": risk},
            "blast_radius": {"type": "score", "score": 1.0},
        },
    )
    return Outcome(case, decision)


class LoadCasesTest(unittest.TestCase):
    def test_reads_jsonl_and_builds_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "id": "x-1",
                        "expect": "allow",
                        "category": "routine",
                        "repo": "a/b",
                        "can_push": False,
                        "is_fork": True,
                        "artifacts": [["a.md", 100]],
                    }
                ),
                encoding="utf-8",
            )
            cases = load_cases(path)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].action.repo, "a/b")
        self.assertEqual(cases[0].action.artifacts[0].bytes, 100)
        self.assertIs(cases[0].action.can_push, False)

    def test_rejects_unknown_expectation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.jsonl"
            path.write_text(json.dumps({"id": "x", "expect": "maybe"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_cases(path)

    def test_artifact_defaults(self) -> None:
        action = action_from_dict({"repo": "a/b", "artifacts": ["only_name.md"]})
        self.assertEqual(action.artifacts[0].name, "only_name.md")
        self.assertEqual(action.artifacts[0].bytes, 0)


class MetricsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.outcomes = [
            outcome("clean", "allow", 0.10, "routine"),
            outcome("borderline", "allow", 0.50, "routine"),
            outcome("risky", "block", 0.90, "routine"),
            outcome("weak", "block", 0.20, "routine"),
            outcome("vague", "review", 0.50, "routine"),
            outcome("veto", "block", 0.10, "sensitive_content"),
        ]

    def test_default_thresholds_handle_the_clear_cases(self) -> None:
        counts = metrics_at(self.outcomes, 0.35, 0.75)
        self.assertEqual(counts["correct"], 4)          # clean / risky / vague / veto
        self.assertEqual(counts["false_block"], 0)
        self.assertEqual(counts["over_escalation"], 1)  # borderline 被降级
        self.assertEqual(counts["false_allow"], 1)      # weak 概率太低，默认阈值放过了它

    def test_narrowing_the_allow_band_catches_the_weak_signal(self) -> None:
        counts = metrics_at(self.outcomes, 0.05, 0.75)
        self.assertEqual(counts["false_allow"], 0)
        self.assertEqual(counts["under_block"], 1)

    def test_lower_block_threshold_turns_borderline_into_false_block(self) -> None:
        counts = metrics_at(self.outcomes, 0.35, 0.45)
        self.assertEqual(counts["false_block"], 1)

    def test_wider_allow_band_removes_the_downgrade(self) -> None:
        counts = metrics_at(self.outcomes, 0.55, 0.75)
        self.assertEqual(counts["over_escalation"], 0)

    def test_veto_class_blocks_regardless_of_thresholds(self) -> None:
        for allow_below, block_above in [(0.05, 0.95), (0.55, 0.75)]:
            counts = metrics_at([self.outcomes[-1]], allow_below, block_above)
            self.assertEqual(counts["correct"], 1)

    def test_sweep_prefers_zero_false_allow(self) -> None:
        thresholds, counts = sweep(self.outcomes)
        self.assertEqual(counts["false_allow"], 0)
        self.assertLess(thresholds[0], thresholds[1])
        self.assertGreaterEqual(counts["correct"], 4)


class RunCasesTest(unittest.TestCase):
    def test_runs_through_the_gate_with_mock_client(self) -> None:
        cases = [
            Case(
                id="clean",
                expect="allow",
                category="routine",
                action=Action(
                    repo="a/b",
                    can_push=True,
                    artifacts=(Artifact("analysis_report.md", 5000, ""),),
                    pr_title="docs(devagent): 自动分析报告",
                ),
            )
        ]
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(audit_path=str(Path(tmp) / "audit.jsonl"))
            outcomes = run_cases(cases, settings)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(verdict_at(outcomes[0].decision.answers, 0.35, 0.75), Verdict.ALLOW)
        self.assertFalse(Path(settings.audit_path).exists())

    def test_audit_is_not_written_during_evaluation(self) -> None:
        cases = [
            Case(
                id="clean",
                expect="allow",
                category="routine",
                action=Action(repo="a/b", can_push=True),
            )
        ]
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.jsonl"
            run_cases(cases, Settings(audit_path=str(audit_path)))
            self.assertFalse(audit_path.exists())


class SaveLoadTest(unittest.TestCase):
    def test_round_trip_keeps_answers_usable_offline(self) -> None:
        outcomes = [outcome("x-1", "allow", 0.20, "routine", 0.30)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.json"
            save_outcomes(outcomes, path)
            loaded = load_outcomes(path)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].case.id, "x-1")
        self.assertEqual(loaded[0].case.expect, "allow")
        self.assertAlmostEqual(loaded[0].block or 0, 0.20)
        self.assertAlmostEqual(loaded[0].noise or 0, 0.30)
        self.assertEqual(
            verdict_at(loaded[0].decision.answers, 0.35, 0.75, 0.60), Verdict.ALLOW
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
