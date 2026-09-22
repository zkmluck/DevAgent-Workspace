"""策略层的单元测试：脱敏、state 白名单、三档分流与边界。"""

from __future__ import annotations

import unittest

from jev_reflex.config import Settings
from jev_reflex.models import Action, Artifact, Verdict
from jev_reflex.policy import build_questions, build_state, evaluate, redact


class RedactionTest(unittest.TestCase):
    def test_known_key_shapes_are_masked(self) -> None:
        text = (
            "token jv_live_sX0AQdh8LWIRUezwV5uQWKVRfiF46 and sk-abcdefghijklmnop "
            "and ghp_0123456789abcdefghijklmnopqrstuvwx and AKIAIOSFODNN7EXAMPLE"
        )
        cleaned = redact(text)
        self.assertNotIn("jv_live_", cleaned)
        self.assertNotIn("sk-abcdefghijklmnop", cleaned)
        self.assertNotIn("ghp_0123456789", cleaned)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", cleaned)
        self.assertIn("[redacted]", cleaned)

    def test_bearer_token_is_masked(self) -> None:
        self.assertNotIn("Bearer abcdefghijklmnopqrst", redact("Bearer abcdefghijklmnopqrst"))


class BuildStateTest(unittest.TestCase):
    def _action(self, **kwargs) -> Action:
        base = dict(
            repo="zkmluck/errAnalyst",
            base_branch="main",
            head_branch="devagent/auto/20260922",
            can_push=False,
            is_fork=True,
            artifacts=(Artifact("analysis_report.md", 12000, "a" * 64),),
            pr_title="docs(devagent): 自动分析报告",
            pr_body="由 DevAgent-Workspace 自动生成。",
        )
        base.update(kwargs)
        return Action(**base)

    def test_state_contains_only_whitelisted_fields(self) -> None:
        state = build_state(self._action())
        self.assertIn("Repository: zkmluck/errAnalyst", state)
        self.assertIn("Push permission: no", state)
        self.assertIn("Where the commit goes: our own fork", state)
        self.assertIn("analysis_report.md (12000 bytes, sha256:aaaaaaaa)", state)
        self.assertIn("Fork required: yes", state)

    def test_direct_push_without_permission_is_visible_in_state(self) -> None:
        state = build_state(self._action(can_push=False, is_fork=False))
        self.assertIn("Push permission: no", state)
        self.assertIn("Where the commit goes: the upstream repository", state)

    def test_state_never_carries_secrets_from_body(self) -> None:
        state = build_state(
            self._action(pr_body="here is the key jv_live_sX0AQdh8LWIRUezwV5uQWKVRfiF46")
        )
        self.assertNotIn("jv_live_sX0A", state)
        self.assertIn("[redacted]", state)

    def test_unknown_permission_is_stated_as_unknown(self) -> None:
        self.assertIn("Push permission: unknown", build_state(self._action(can_push=None)))


class EvaluateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings()

    def _answers(self, block_probability, risk_class, blast=1.0, noise=0.05):
        return {
            "block": {"type": "noul", "noul": block_probability},
            "noise": {"type": "noul", "noul": noise},
            "risk_class": {"type": "choice", "choice": risk_class},
            "blast_radius": {"type": "score", "score": blast},
        }

    def test_noul_question_asks_about_blocking_not_safety(self) -> None:
        """回归测试：真跑 Qwen 时，问句写成"是否安全"、逻辑却按"概率高=危险"判断，
        于是一个完全正常的动作被判成了要拦下。问句方向必须和判定方向一致。"""

        instruction = build_questions()["block"]["instructions"].lower()
        self.assertIn("block", instruction)
        self.assertNotIn("safe", instruction)

    def test_low_probability_routine_is_allowed(self) -> None:
        verdict, _, _ = evaluate(self._answers(0.12, "routine"), self.settings)
        self.assertIs(verdict, Verdict.ALLOW)

    def test_sensitive_content_is_blocked_even_with_low_probability(self) -> None:
        verdict, reasons, _ = evaluate(
            self._answers(0.01, "sensitive_content"), self.settings
        )
        self.assertIs(verdict, Verdict.BLOCK)
        self.assertTrue(any("automatic block" in reason for reason in reasons))

    def test_unauthorized_push_is_blocked(self) -> None:
        verdict, _, _ = evaluate(self._answers(0.2, "unauthorized_push"), self.settings)
        self.assertIs(verdict, Verdict.BLOCK)

    def test_high_probability_is_blocked(self) -> None:
        verdict, _, _ = evaluate(self._answers(0.9, "routine"), self.settings)
        self.assertIs(verdict, Verdict.BLOCK)

    def test_middle_band_is_review(self) -> None:
        verdict, _, _ = evaluate(self._answers(0.6, "routine"), self.settings)
        self.assertIs(verdict, Verdict.REVIEW)

    def test_safe_probability_but_risky_class_still_reviews(self) -> None:
        verdict, _, _ = evaluate(self._answers(0.1, "low_value_spam"), self.settings)
        self.assertIs(verdict, Verdict.REVIEW)

    def test_allow_threshold_boundary(self) -> None:
        self.assertIs(evaluate(self._answers(0.34, "routine"), self.settings)[0], Verdict.ALLOW)
        self.assertIs(evaluate(self._answers(0.35, "routine"), self.settings)[0], Verdict.REVIEW)

    def test_block_threshold_boundary(self) -> None:
        self.assertIs(evaluate(self._answers(0.75, "routine"), self.settings)[0], Verdict.BLOCK)
        self.assertIs(evaluate(self._answers(0.74, "routine"), self.settings)[0], Verdict.REVIEW)

    def test_missing_answers_review_instead_of_allow(self) -> None:
        verdict, reasons, _ = evaluate({}, self.settings)
        self.assertIs(verdict, Verdict.REVIEW)
        self.assertTrue(any("missing required fields" in reason for reason in reasons))

    def test_missing_noise_answer_reviews(self) -> None:
        answers = self._answers(0.10, "routine")
        del answers["noise"]
        self.assertIs(evaluate(answers, self.settings)[0], Verdict.REVIEW)

    def test_high_noise_downgrades_without_blocking(self) -> None:
        """没价值 ≠ 危险：该降到草稿，而不是拦下。"""

        verdict, reasons, _ = evaluate(
            self._answers(0.10, "routine", noise=0.90), self.settings
        )
        self.assertIs(verdict, Verdict.REVIEW)
        self.assertTrue(any("noise" in reason for reason in reasons))

    def test_noise_threshold_comes_from_settings(self) -> None:
        lenient = Settings(noise_above=0.95)
        self.assertIs(
            evaluate(self._answers(0.10, "routine", noise=0.90), lenient)[0], Verdict.ALLOW
        )

    def test_thresholds_come_from_settings(self) -> None:
        strict = Settings(allow_below=0.05, block_above=0.2)
        self.assertIs(evaluate(self._answers(0.1, "routine"), strict)[0], Verdict.REVIEW)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
