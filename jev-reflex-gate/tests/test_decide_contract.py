"""客户端契约测试：响应结构、错误分类、重试规则、缺 key。"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest import mock

from jev_reflex.config import Settings
from jev_reflex.decide import JevClient, JevError
from jev_reflex.policy import build_questions

SAMPLE_RESPONSE = {
    "answers": {
        "block": {"type": "noul", "noul": 0.62},
        "risk_class": {
            "type": "choice",
            "choice": "low_value_spam",
            "confidence": 0.71,
            "probabilities": {"routine": 0.2, "low_value_spam": 0.71},
        },
        "blast_radius": {"type": "score", "score": 2.0, "probabilities": {"2": 1.0}},
    },
    "usage": {"input_tokens": 310, "cost_usd": 0.00013, "credits_remaining_usd": 4.99},
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


def _http_error(status: int, message: str) -> urllib.error.HTTPError:
    body = io.BytesIO(json.dumps({"error": message}).encode("utf-8"))
    return urllib.error.HTTPError(
        "https://jevtypesafeai.com/api/v1/decide", status, message, {}, body
    )


class JevClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(api_key="jv_live_test_key", max_retries=1)
        self.client = JevClient(self.settings)

    def test_answers_and_usage_are_parsed(self) -> None:
        with mock.patch(
            "jev_reflex.decide.urllib.request.urlopen",
            return_value=FakeResponse(SAMPLE_RESPONSE),
        ):
            answers, usage = self.client.ask("state text", build_questions())
        self.assertEqual(answers["block"]["noul"], 0.62)
        self.assertEqual(answers["risk_class"]["choice"], "low_value_spam")
        self.assertEqual(usage["input_tokens"], 310)

    def test_missing_key_fails_fast(self) -> None:
        client = JevClient(Settings(api_key=""))
        with self.assertRaises(JevError) as ctx:
            client.ask("state", build_questions())
        self.assertEqual(ctx.exception.code, "missing_key")

    def test_402_is_not_retried(self) -> None:
        with mock.patch(
            "jev_reflex.decide.urllib.request.urlopen",
            side_effect=_http_error(402, "Insufficient credits — top up to continue."),
        ) as fake:
            with self.assertRaises(JevError) as ctx:
                self.client.ask("state", build_questions())
        self.assertEqual(ctx.exception.code, "insufficient_credits")
        self.assertEqual(fake.call_count, 1)

    def test_401_is_not_retried(self) -> None:
        with mock.patch(
            "jev_reflex.decide.urllib.request.urlopen",
            side_effect=_http_error(401, "Invalid or revoked API key."),
        ) as fake:
            with self.assertRaises(JevError) as ctx:
                self.client.ask("state", build_questions())
        self.assertEqual(ctx.exception.code, "invalid_key")
        self.assertEqual(fake.call_count, 1)

    def test_server_error_is_retried_then_raised(self) -> None:
        with mock.patch(
            "jev_reflex.decide.urllib.request.urlopen",
            side_effect=_http_error(503, "upstream unavailable"),
        ) as fake:
            with self.assertRaises(JevError) as ctx:
                self.client.ask("state", build_questions())
        self.assertEqual(ctx.exception.code, "server_error")
        self.assertEqual(fake.call_count, 2)

    def test_retry_can_succeed_on_second_attempt(self) -> None:
        responses = [
            _http_error(503, "upstream unavailable"),
            FakeResponse(SAMPLE_RESPONSE),
        ]
        with mock.patch(
            "jev_reflex.decide.urllib.request.urlopen", side_effect=responses
        ):
            answers, _ = self.client.ask("state", build_questions())
        self.assertEqual(answers["risk_class"]["choice"], "low_value_spam")

    def test_unreachable_endpoint_is_classified(self) -> None:
        with mock.patch(
            "jev_reflex.decide.urllib.request.urlopen",
            side_effect=urllib.error.URLError("dns failure"),
        ):
            with self.assertRaises(JevError) as ctx:
                self.client.ask("state", build_questions())
        self.assertEqual(ctx.exception.code, "unreachable")

    def test_api_key_is_never_in_the_error_message(self) -> None:
        with mock.patch(
            "jev_reflex.decide.urllib.request.urlopen",
            side_effect=_http_error(401, "Invalid or revoked API key."),
        ):
            with self.assertRaises(JevError) as ctx:
                self.client.ask("state", build_questions())
        self.assertNotIn("jv_live_test_key", str(ctx.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
