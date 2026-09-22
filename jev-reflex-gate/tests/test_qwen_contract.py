"""契约校验与 Qwen 替身的测试。

这一组测的不是"模型答得好不好"，而是"模型答歪了会不会被放过去"——
后者才是安全属性。
"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest import mock

from jev_reflex.config import Settings
from jev_reflex.contract import BadAnswers, validate_answers
from jev_reflex.decide import JevError
from jev_reflex.policy import build_questions
from jev_reflex.qwen import QwenJev

QUESTIONS = build_questions()

GOOD_ANSWERS = {
    "block": {"type": "noul", "noul": 0.62},
    "noise": {"type": "noul", "noul": 0.10},
    "risk_class": {
        "type": "choice",
        "choice": "low_value_spam",
        "confidence": 0.71,
        "probabilities": {
            "routine": 0.14,
            "sensitive_content": 0.05,
            "unauthorized_push": 0.05,
            "low_value_spam": 0.71,
            "duplicate": 0.05,
        },
    },
    "blast_radius": {"type": "score", "score": 2.0, "probabilities": {"2": 1.0}},
}


def chat_response(answers: dict) -> dict:
    return {
        "choices": [{"message": {"content": json.dumps({"answers": answers}, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 1180, "completion_tokens": 90},
    }


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def http_error(status: int, message: str) -> urllib.error.HTTPError:
    body = io.BytesIO(json.dumps({"error": {"message": message}}).encode("utf-8"))
    return urllib.error.HTTPError("https://example.invalid", status, message, {}, body)


class ValidateAnswersTest(unittest.TestCase):
    def test_valid_payload_passes_through(self) -> None:
        result = validate_answers(GOOD_ANSWERS, QUESTIONS)
        self.assertEqual(result["block"]["noul"], 0.62)
        self.assertEqual(result["risk_class"]["choice"], "low_value_spam")
        self.assertEqual(result["blast_radius"]["score"], 2.0)

    def test_missing_question_fails(self) -> None:
        partial = {key: value for key, value in GOOD_ANSWERS.items() if key != "block"}
        with self.assertRaises(BadAnswers):
            validate_answers(partial, QUESTIONS)

    def test_noul_out_of_range_fails(self) -> None:
        bad = dict(GOOD_ANSWERS, block={"type": "noul", "noul": 1.4})
        with self.assertRaises(BadAnswers):
            validate_answers(bad, QUESTIONS)

    def test_invented_choice_fails(self) -> None:
        bad = dict(
            GOOD_ANSWERS,
            risk_class={"type": "choice", "choice": "totally_safe", "confidence": 0.99},
        )
        with self.assertRaises(BadAnswers):
            validate_answers(bad, QUESTIONS)

    def test_string_numbers_are_coerced(self) -> None:
        result = validate_answers(
            dict(GOOD_ANSWERS, block={"type": "noul", "noul": "0.37"}), QUESTIONS
        )
        self.assertEqual(result["block"]["noul"], 0.37)

    def test_unknown_probability_key_fails(self) -> None:
        bad = dict(
            GOOD_ANSWERS,
            risk_class={
                "type": "choice",
                "choice": "routine",
                "probabilities": {"routine": 0.5, "whatever": 0.5},
            },
        )
        with self.assertRaises(BadAnswers):
            validate_answers(bad, QUESTIONS)

    def test_probabilities_are_renormalised(self) -> None:
        result = validate_answers(
            dict(
                GOOD_ANSWERS,
                risk_class={
                    "type": "choice",
                    "choice": "routine",
                    "probabilities": {"routine": 7.0, "duplicate": 3.0},
                },
            ),
            QUESTIONS,
        )
        probs = result["risk_class"]["probabilities"]
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=3)

    def test_confidence_defaults_to_chosen_probability(self) -> None:
        result = validate_answers(
            dict(
                GOOD_ANSWERS,
                risk_class={
                    "type": "choice",
                    "choice": "routine",
                    "probabilities": {"routine": 0.8, "duplicate": 0.2},
                },
            ),
            QUESTIONS,
        )
        self.assertEqual(result["risk_class"]["confidence"], 0.8)

    def test_score_beyond_the_scale_is_clamped(self) -> None:
        result = validate_answers(
            dict(GOOD_ANSWERS, blast_radius={"type": "score", "score": 9.0}), QUESTIONS
        )
        self.assertEqual(result["blast_radius"]["score"], 4.0)

    def test_unsupported_type_fails(self) -> None:
        with self.assertRaises(BadAnswers):
            validate_answers({"weird": {"type": "poem", "text": "hi"}}, {"weird": {"type": "poem"}})


class QwenJevTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(qwen_api_key="sk-test-key", qwen_model="qwen-plus")
        self.client = QwenJev(self.settings)

    def test_well_formed_reply_is_validated(self) -> None:
        with mock.patch(
            "jev_reflex.qwen.urllib.request.urlopen",
            return_value=FakeResponse(chat_response(GOOD_ANSWERS)),
        ):
            answers, usage = self.client.ask("state text", QUESTIONS)
        self.assertEqual(answers["risk_class"]["choice"], "low_value_spam")
        self.assertEqual(usage["provider"], "qwen")
        self.assertEqual(usage["input_tokens"], 1180)

    def test_code_fenced_reply_is_still_parsed(self) -> None:
        payload = {
            "choices": [
                {"message": {"content": "```json\n"
                             + json.dumps({"answers": GOOD_ANSWERS})
                             + "\n```"}}
            ]
        }
        with mock.patch(
            "jev_reflex.qwen.urllib.request.urlopen",
            return_value=FakeResponse(payload),
        ):
            answers, _ = self.client.ask("state text", QUESTIONS)
        self.assertEqual(answers["block"]["noul"], 0.62)

    def test_missing_key_fails_fast(self) -> None:
        with self.assertRaises(JevError) as ctx:
            QwenJev(Settings(qwen_api_key="")).ask("state", QUESTIONS)
        self.assertEqual(ctx.exception.code, "missing_key")

    def test_contract_violation_becomes_bad_response(self) -> None:
        bad = dict(GOOD_ANSWERS, risk_class={"type": "choice", "choice": "made_up"})
        with mock.patch(
            "jev_reflex.qwen.urllib.request.urlopen",
            return_value=FakeResponse(chat_response(bad)),
        ):
            with self.assertRaises(JevError) as ctx:
                self.client.ask("state text", QUESTIONS)
        self.assertEqual(ctx.exception.code, "bad_response")
        self.assertIn("contract violation", ctx.exception.message)

    def test_prose_instead_of_json_is_rejected(self) -> None:
        payload = {"choices": [{"message": {"content": "Sure! This looks safe to me."}}]}
        with mock.patch(
            "jev_reflex.qwen.urllib.request.urlopen",
            return_value=FakeResponse(payload),
        ):
            with self.assertRaises(JevError) as ctx:
                self.client.ask("state text", QUESTIONS)
        self.assertEqual(ctx.exception.code, "bad_response")

    def test_json_mode_is_dropped_when_server_rejects_it(self) -> None:
        responses = [http_error(400, "response_format is not supported"),
                     FakeResponse(chat_response(GOOD_ANSWERS))]
        with mock.patch(
            "jev_reflex.qwen.urllib.request.urlopen", side_effect=responses
        ) as fake:
            answers, _ = self.client.ask("state text", QUESTIONS)
        self.assertEqual(fake.call_count, 2)
        self.assertEqual(answers["block"]["noul"], 0.62)

    def test_bad_key_is_not_retried(self) -> None:
        with mock.patch(
            "jev_reflex.qwen.urllib.request.urlopen",
            side_effect=http_error(401, "Invalid API-key provided."),
        ) as fake:
            with self.assertRaises(JevError) as ctx:
                self.client.ask("state text", QUESTIONS)
        self.assertEqual(ctx.exception.code, "invalid_key")
        self.assertEqual(fake.call_count, 1)

    def test_api_key_never_appears_in_errors(self) -> None:
        with mock.patch(
            "jev_reflex.qwen.urllib.request.urlopen",
            side_effect=http_error(401, "Invalid API-key provided."),
        ):
            with self.assertRaises(JevError) as ctx:
                self.client.ask("state text", QUESTIONS)
        self.assertNotIn("sk-test-key", str(ctx.exception))


class GateUseQwenTest(unittest.TestCase):
    def test_gate_uses_qwen_when_configured(self) -> None:
        import tempfile
        from pathlib import Path

        from jev_reflex.gate import guard
        from jev_reflex.models import Action, Verdict

        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                client="qwen",
                qwen_api_key="sk-test-key",
                audit_path=str(Path(tmp) / "audit.jsonl"),
            )
            with mock.patch(
                "jev_reflex.qwen.urllib.request.urlopen",
                return_value=FakeResponse(chat_response(GOOD_ANSWERS)),
            ):
                decision = guard(
                    Action(repo="zkmluck/errAnalyst", can_push=False),
                    settings=settings,
                )
        self.assertEqual(decision.client, "qwen")
        self.assertIs(decision.verdict, Verdict.REVIEW)  # 0.62 落在不确定走廊
        self.assertTrue(decision.downgrade_to_draft)

    def test_qwen_contract_violation_makes_the_gate_review(self) -> None:
        import tempfile
        from pathlib import Path

        from jev_reflex.gate import guard
        from jev_reflex.models import Action, Verdict

        bad = dict(GOOD_ANSWERS, block={"type": "noul", "noul": 42})
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                client="qwen",
                qwen_api_key="sk-test-key",
                audit_path=str(Path(tmp) / "audit.jsonl"),
            )
            with mock.patch(
                "jev_reflex.qwen.urllib.request.urlopen",
                return_value=FakeResponse(chat_response(bad)),
            ):
                decision = guard(Action(repo="zkmluck/errAnalyst"), settings=settings)
        self.assertIs(decision.verdict, Verdict.REVIEW)
        self.assertEqual(decision.error, "bad_response")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
