"""
火山引擎网关 — V4 签名 + Ark API 图片生成。
"""

import hashlib
import hmac
import json
import datetime
import uuid
from ...core.http_client import create_client
from ...core.errors import friendly_image_error_detail


class VolcengineGateway:
    """火山引擎 Ark 图片生成网关"""

    def __init__(self, provider: dict):
        self.provider = provider
        self.ak = provider.get("access_key", "")
        self.sk = provider.get("secret_key", "")
        self.region = provider.get("volcengine_region", "cn-beijing")
        self.service = "ark"

    def _endpoint(self) -> str:
        base = self.provider.get("base_url", "").rstrip("/")
        return f"{base}/images/generations"

    async def generate(
        self, prompt: str, size: str = "1024x1024", model: str = "",
        quality: str = "", n: int = 1,
        reference_images: list | None = None,
    ) -> list[str]:
        body = {
            "model": model or "doubao-seedream-4.0",
            "prompt": prompt,
            "n": n,
            "size": size,
            "response_format": "url",
        }
        body_str = json.dumps(body, ensure_ascii=False)
        headers = self._sign_headers(body_str)

        async with create_client("long") as client:
            resp = await client.post(self._endpoint(), content=body_str, headers=headers)

        if resp.status_code != 200:
            raise Exception(friendly_image_error_detail(resp.text, size, model))

        data = resp.json()
        urls = []
        for item in data.get("data", []):
            if "url" in item:
                urls.append(item["url"])
        return urls

    # ---- V4 签名 ----

    def _sign_headers(self, body: str) -> dict:
        now = datetime.datetime.now(datetime.timezone.utc)
        date_short = now.strftime("%Y%m%d")
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        host = "ark.cn-beijing.volces.com"

        # 1. 构建 canonical request
        canonical_uri = "/"
        canonical_querystring = ""
        payload_hash = hashlib.sha256(body.encode()).hexdigest()
        canonical_headers = f"content-type:application/json\nhost:{host}\nx-date:{timestamp}\n"
        signed_headers = "content-type;host;x-date"
        canonical_request = f"POST\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"

        # 2. 构建 string to sign
        algorithm = "HMAC-SHA256"
        credential_scope = f"{date_short}/{self.region}/{self.service}/request"
        string_to_sign = f"{algorithm}\n{timestamp}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"

        # 3. 计算签名
        k_date = self._hmac(f"{self.sk}", date_short)
        k_region = self._hmac(k_date, self.region)
        k_service = self._hmac(k_region, self.service)
        k_signing = self._hmac(k_service, "request")
        signature = self._hmac_hex(k_signing, string_to_sign)

        # 4. Authorization header
        authorization = f"{algorithm} Credential={self.ak}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"

        return {
            "Content-Type": "application/json",
            "Host": host,
            "X-Date": timestamp,
            "Authorization": authorization,
        }

    @staticmethod
    def _hmac(key, msg) -> bytes:
        if isinstance(key, str):
            key = key.encode("utf-8")
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    @staticmethod
    def _hmac_hex(key, msg) -> str:
        if isinstance(key, str):
            key = key.encode("utf-8")
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).hexdigest()
