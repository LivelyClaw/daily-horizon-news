import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import horizon_webhook as webhook


class WebhookTest(unittest.TestCase):
    def setUp(self):
        self.url = "https://open.feishu.cn/open-apis/bot/v2/hook/test-only"
        self.bot = webhook.FeishuWebhook(self.url, "demo")

    def test_signature_vector_and_unicode_body(self):
        message = {"msg_type": "text", "content": {"text": "中文日报"}}
        payload = json.loads(self.bot.signed_body(message, 1599360473))
        self.assertEqual(payload, {**message, "timestamp": "1599360473",
                                   "sign": "l1N0gAcBjdwBvGm1xMjOF0XSyaLRpR7tuO5dHfhAYc8="})

    def test_file_is_authoritative_and_credentials_are_literal(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, DAILY_HORIZON_NEWS_WEBHOOK_SECRET="ambient"):
            env = Path(temp) / ".env"
            env.write_text(f'DAILY_HORIZON_NEWS_WEBHOOK_URL={self.url}\n')
            with self.assertRaisesRegex(ValueError, "SECRET"):
                webhook.FeishuWebhook.from_file(env)
            with env.open("a") as f:
                f.write("DAILY_HORIZON_NEWS_WEBHOOK_SECRET='literal-${EXAMPLE}'\n")
            bot = webhook.FeishuWebhook.from_file(env)
            self.assertEqual(bot.secret, "literal-${EXAMPLE}")
            self.assertNotIn(bot.url, repr(bot))
            self.assertNotIn(bot.secret, repr(bot))

    @patch.object(webhook.http.client, "HTTPSConnection")
    def test_requires_successful_http_and_business_receipt(self, connect):
        response = connect.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"code":0}'
        message = {"msg_type": "text", "content": {"text": "test"}}
        self.bot.send(message)
        method, path, body, _ = connect.return_value.request.call_args.args
        self.assertEqual((method, path), ("POST", "/open-apis/bot/v2/hook/test-only"))
        self.assertIn("sign", json.loads(body))
        for status, raw in [(200, b'{"code":19021}'), (200, b'{"code":false}'),
                            (200, b'{}'), (200, b'not json'), (302, b'{"code":0}')]:
            with self.subTest(status=status, raw=raw):
                response.status, response.read.return_value = status, raw
                with self.assertRaises(ValueError) as error:
                    self.bot.send(message)
                self.assertNotIn(self.url, str(error.exception))
