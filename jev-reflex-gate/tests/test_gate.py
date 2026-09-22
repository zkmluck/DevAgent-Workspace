"""闸门层测试：三档判决、失败降级、审计留痕、总开关。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jev_reflex.audit import read_all, replay
from jev_reflex.config import Settings
from jev_reflex.decide import JevError
from jev_reflex.gate import guard, guard_github_pr
from jev_reflex.models import Action, Artifact, Verdict


class BrokenClient:
    name = "broken"

    def ask(self, state, questions):
        raise JevError("insufficient_credits", "prepaid balance is empty")


class ExplodingClient:
    name = "exploding"

    def ask(self, state, questions):
        raise ValueError("something nobody expected")


class GateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.audit_path = str(Path(self.tmp.name) / "audit.jsonl")
        self.settings = Settings(audit_path=self.audit_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _action(self, **kwargs) -> Action:
        base = dict(
            repo="zkmluck/DevAgent-Workspace",
            can_push=True,
            artifacts=(Artifact("analysis_report.md", 12000, "b" * 64),),
            pr_title="docs(devagent): 自动分析报告",
            pr_body="generated analysis text only",
        )
        base.update(kwargs)
        return Action(**base)

    def test_clean_action_is_allowed(self) -> None:
        decision = guard(self._action(), settings=self.settings)
        self.assertIs(decision.verdict, Verdict.ALLOW)
        self.assertEqual(decision.client, "mock")
        self.assertEqual(len(decision.action_id), 8)
        self.assertFalse(decision.downgrade_to_draft)

    def test_secret_bearing_action_is_blocked(self) -> None:
        decision = guard(
            self._action(pr_body="snippet contains sk-abcdefghijklmnop"), settings=self.settings
        )
        self.assertIs(decision.verdict, Verdict.BLOCK)
        self.assertEqual(decision.answers["risk_class"]["choice"], "sensitive_content")

    def test_empty_artifacts_get_reviewed_and_downgraded(self) -> None:
        decision = guard(self._action(artifacts=()), settings=self.settings)
        self.assertIs(decision.verdict, Verdict.REVIEW)
        self.assertTrue(decision.downgrade_to_draft)

    def test_client_failure_reviews_instead_of_approving(self) -> None:
        decision = guard(self._action(), settings=self.settings, client=BrokenClient())
        self.assertIs(decision.verdict, Verdict.REVIEW)
        self.assertEqual(decision.error, "insufficient_credits")
        self.assertTrue(any("do not auto-approve" in r for r in decision.reasons))

    def test_unexpected_client_failure_is_contained(self) -> None:
        decision = guard(self._action(), settings=self.settings, client=ExplodingClient())
        self.assertIs(decision.verdict, Verdict.REVIEW)
        self.assertEqual(decision.error, "unexpected")

    def test_disabled_gate_allows_and_says_so(self) -> None:
        disabled = Settings(audit_path=self.audit_path, enabled=False)
        decision = guard(self._action(), settings=disabled)
        self.assertIs(decision.verdict, Verdict.ALLOW)
        self.assertTrue(any("disabled" in reason for reason in decision.reasons))

    def test_every_decision_is_recorded_and_replayable(self) -> None:
        decision = guard(self._action(), settings=self.settings)
        entries = read_all(self.audit_path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action_id"], decision.action_id)
        self.assertNotIn("jv_live_", Path(self.audit_path).read_text(encoding="utf-8"))
        text = replay(decision.action_id, self.audit_path)
        self.assertIn(decision.action_id, text)
        self.assertIn("threshold", text)

    def test_audit_can_be_switched_off(self) -> None:
        guard(self._action(), settings=self.settings, audit=False)
        self.assertEqual(read_all(self.audit_path), [])


class GuardGithubPrTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "analysis_report.md").write_text("report body", encoding="utf-8")
        (self.dir / "test_skeleton.py").write_text("def test_x(): pass", encoding="utf-8")
        self.settings = Settings(audit_path=str(self.dir / "audit.jsonl"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_scans_artifacts_and_allows_routine_work(self) -> None:
        decision = guard_github_pr(
            "https://github.com/zkmluck/DevAgent-Workspace",
            self.dir,
            can_push=True,
            settings=self.settings,
        )
        self.assertIs(decision.verdict, Verdict.ALLOW)
        self.assertEqual(decision.repo, "zkmluck/DevAgent-Workspace")

    def test_third_party_fork_is_still_allowed_when_content_is_clean(self) -> None:
        decision = guard_github_pr(
            "zkmluck/errAnalyst",
            self.dir,
            can_push=False,
            is_fork=True,
            settings=self.settings,
        )
        self.assertIs(decision.verdict, Verdict.ALLOW)

    def test_secret_inside_artifact_content_is_not_sent_but_body_is_scrutinised(self) -> None:
        (self.dir / "pr_draft.md").write_text(
            "leaked jv_live_sX0AQdh8LWIRUezwV5uQWKVRfiF46", encoding="utf-8"
        )
        decision = guard_github_pr(
            "zkmluck/errAnalyst",
            self.dir,
            can_push=False,
            pr_body="draft mentions jv_live_sX0AQdh8LWIRUezwV5uQWKVRfiF46",
            settings=self.settings,
        )
        self.assertIs(decision.verdict, Verdict.BLOCK)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
