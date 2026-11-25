"""飞书自定义机器人签名和投递。"""

import base64
from dataclasses import dataclass
import hmac
import http.client
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit


MAX_BODY_BYTES = 20_000


@dataclass(frozen=True, repr=False)
class FeishuWebhook:
    url: str
    secret: str

    @classmethod
    def from_file(cls, path: Path):
        from dotenv import dotenv_values

        with path.open(encoding="utf-8") as stream:
            values = dotenv_values(stream=stream, interpolate=False)
        url = values.get("DAILY_HORIZON_NEWS_WEBHOOK_URL") or ""
        secret = values.get("DAILY_HORIZON_NEWS_WEBHOOK_SECRET") or ""
        if not re.fullmatch(r"https://open\.(?:feishu\.cn|larksuite\.com)/open-apis/bot/v2/hook/[A-Za-z0-9_-]+", url):
            raise ValueError("请在 Hermes .env 中配置有效的 DAILY_HORIZON_NEWS_WEBHOOK_URL")
        if not secret.strip():
            raise ValueError("请在 Hermes .env 中配置 DAILY_HORIZON_NEWS_WEBHOOK_SECRET")
        return cls(url, secret)

    def signed_body(self, message: dict, timestamp: int) -> bytes:
        key = f"{timestamp}\n{self.secret}".encode("utf-8")
        signature = base64.b64encode(hmac.digest(key, b"", "sha256")).decode("ascii")
        body = {**message, "timestamp": str(timestamp), "sign": signature}
        return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def send(self, message: dict) -> None:
        body = self.signed_body(message, int(time.time()))
        if len(body) > MAX_BODY_BYTES:
            raise ValueError("飞书 Webhook 请求体超过 20 KB")
        endpoint = urlsplit(self.url)
        connection = http.client.HTTPSConnection(endpoint.hostname, timeout=30)
        try:
            connection.request("POST", endpoint.path, body, {"Content-Type": "application/json; charset=utf-8"})
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError(f"飞书 Webhook 返回 HTTP {response.status}")
            receipt = json.loads(response.read(65536))
        except (OSError, http.client.HTTPException, UnicodeError, json.JSONDecodeError):
            raise ValueError("未取得有效的飞书 Webhook 回执") from None
        finally:
            connection.close()
        code = receipt.get("code") if isinstance(receipt, dict) else None
        if type(code) is not int or code != 0:
            reason = f"错误码 {code}" if type(code) is int else "缺少有效的成功码"
            raise ValueError(f"飞书 Webhook 未确认投递，{reason}")
