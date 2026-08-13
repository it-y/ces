"""
火山引擎网关 — V4 签名 + Ark API 图片生成 / Seedance 视频生成。
"""

import asyncio
import hashlib
import hmac
import json
import time
import datetime
from urllib.parse import urlparse
from ...core.errors import friendly_image_error_detail
from .openai import ImageGenerationError

# Seedream 尺寸约束
VOLCENGINE_MIN_EDGE = 1536
VOLCENGINE_MAX_EDGE = 4096
VOLCENGINE_MIN_PIXELS = 3_686_400  # ~1920²

# Seedream 支持的宽高比映射
VOLCENGINE_RATIO_CHOICES = [
    "1:1", "4:3", "3:4", "3:2", "2:3", "16:9", "9:16",
    "21:9", "9:21", "2:1", "1:2", "5:4", "4:5", "3:1", "1:3",
]

# 视频时长约束
VOLCENGINE_VIDEO_DURATION_MIN = 1
VOLCENGINE_VIDEO_DURATION_MAX = 15
VOLCENGINE_VIDEO_DURATION_DEFAULT = 5

# 视频分辨率
VOLCENGINE_VIDEO_RESOLUTIONS = {"480p", "720p", "1080p"}

# Seedance 模型列表
VOLCENGINE_SEEDANCE_MODELS = {
    "doubao-seedance-1-0-pro", "doubao-seedance-1-0-lite",
    "doubao-seedance-1-0-pro-250428", "doubao-seedance-1-0-lite-t2v-250428",
    "doubao-seedance-1-0-lite-i2v-250428",
}


def is_volcengine_seedance_model(model: str) -> bool:
    """检测是否为 Seedance 视频模型"""
    m = (model or "").lower()
    return any(s in m for s in ("seedance", "doubao-seedance"))


def normalize_volcengine_size(size: str) -> str:
    """
    将用户输入的尺寸规范化为火山引擎支持的格式。
    - 长边限制在 1536-4096
    - 宽高比对齐到已知比例列表
    """
    if not size:
        return "2048x2048"
    parts = size.lower().replace("x", " ").split()
    if len(parts) != 2:
        return "2048x2048"
    try:
        w, h = int(parts[0]), int(parts[1])
    except ValueError:
        return "2048x2048"

    # 长边裁剪
    long = max(w, h)
    if long > VOLCENGINE_MAX_EDGE:
        scale = VOLCENGINE_MAX_EDGE / long
        w, h = int(w * scale), int(h * scale)
    elif long < VOLCENGINE_MIN_EDGE:
        scale = VOLCENGINE_MIN_EDGE / long
        w, h = int(w * scale), int(h * scale)

    # 像素数下限
    if w * h < VOLCENGINE_MIN_PIXELS:
        scale = (VOLCENGINE_MIN_PIXELS / (w * h)) ** 0.5
        w, h = int(w * scale), int(h * scale)

    # 16 像素对齐
    w = (w // 16) * 16
    h = (h // 16) * 16
    return f"{w}x{h}"


def volcengine_video_duration(duration: int) -> int:
    """截断到合法范围"""
    d = max(VOLCENGINE_VIDEO_DURATION_MIN, min(VOLCENGINE_VIDEO_DURATION_MAX, int(duration or VOLCENGINE_VIDEO_DURATION_DEFAULT)))
    return d


def volcengine_video_resolution(resolution: str) -> str:
    """校验分辨率为 480p/720p/1080p，默认 720p"""
    r = str(resolution or "").lower().rstrip("p")
    if f"{r}p" in VOLCENGINE_VIDEO_RESOLUTIONS:
        return f"{r}p"
    return "720p"


def _content_role(ref: dict) -> str:
    """从 ref 的 role 字段推断 volcengine content role"""
    role = str(ref.get("role", "") or "").lower()
    if "first" in role:
        return "first_frame"
    if "last" in role:
        return "last_frame"
    if "ref" in role or "reference" in role:
        return "reference_image"
    return "reference_image"


class VolcengineGateway:
    """火山引擎 Ark 图片 + Seedance 视频生成网关"""

    def __init__(self, provider: dict):
        self.provider = provider
        self.ak = provider.get("access_key", "")
        self.sk = provider.get("secret_key", "")
        self.region = provider.get("volcengine_region", "cn-beijing")
        self.service = "ark"

    @staticmethod
    def _parse_url(url: str) -> tuple[str, str]:
        """从完整 URL 提取 (host, path)"""
        parsed = urlparse(url)
        return parsed.hostname or "", parsed.path or "/"

    def _endpoint(self) -> str:
        base = self.provider.get("base_url", "").rstrip("/")
        return f"{base}/images/generations"

    def _video_endpoint(self) -> str:
        """视频提交端点"""
        base = self.provider.get("base_url", "").rstrip("/")
        return f"{base}/contents/generations/tasks"

    def _video_task_url(self, task_id: str) -> str:
        """视频查询端点"""
        base = self.provider.get("base_url", "").rstrip("/")
        return f"{base}/contents/generations/tasks/{task_id}"

    # ================================================================
    # 图片生成
    # ================================================================

    async def generate(
        self, prompt: str, size: str = "1024x1024", model: str = "",
        quality: str = "", n: int = 1,
        reference_images: list | None = None,
    ) -> list[str]:
        size = normalize_volcengine_size(size)
        body = {
            "model": model or "doubao-seedream-4.0",
            "prompt": prompt,
            "n": n,
            "size": size,
            "response_format": "url",
        }
        body_str = json.dumps(body, ensure_ascii=False)
        url = self._endpoint()
        headers = self._sign_headers(body_str, url=url)

        from ...core.http_client import retry_request
        resp = await retry_request("POST", url, content=body_str, headers=headers)

        if resp.status_code != 200:
            raise ImageGenerationError(friendly_image_error_detail(resp.text, size, model), resp.status_code)

        data = resp.json()
        urls = []
        for item in data.get("data", []):
            if "url" in item:
                urls.append(item["url"])
        return urls

    # ================================================================
    # 视频生成（Seedance）
    # ================================================================

    async def generate_video(
        self,
        prompt: str,
        model: str = "",
        duration: int = 5,
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        size: str = "",
        images: list | None = None,
        videos: list | None = None,
        audios: list | None = None,
        **kwargs,
    ) -> list[str]:
        """Seedance 视频生成 — 提交 + 轮询"""
        model = model or "doubao-seedance-1-0-pro"
        duration = volcengine_video_duration(duration)
        resolution = volcengine_video_resolution(resolution)

        # 构造 content 数组（typed blocks）
        content = [{"type": "text", "text": prompt}]

        if images:
            for ref in images:
                url = ref.get("url", "") if isinstance(ref, dict) else str(ref)
                if url:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": url},
                        "role": _content_role(ref) if isinstance(ref, dict) else "reference_image",
                    })

        if videos:
            for ref in videos:
                url = ref.get("url", "") if isinstance(ref, dict) else str(ref)
                if url:
                    content.append({
                        "type": "video_url",
                        "video_url": {"url": url},
                    })

        if audios:
            for ref in audios:
                url = ref.get("url", "") if isinstance(ref, dict) else str(ref)
                if url:
                    content.append({
                        "type": "audio_url",
                        "audio_url": {"url": url},
                    })

        body = {
            "model": model,
            "content": content,
            "parameters": {
                "duration": duration,
                "resolution": resolution,
                "aspect_ratio": aspect_ratio,
            },
        }

        body_str = json.dumps(body, ensure_ascii=False)
        submit_url = self._video_endpoint()
        headers = self._sign_headers(body_str, url=submit_url)

        from ...core.http_client import retry_request, create_client

        # 提交任务
        resp = await retry_request("POST", submit_url, content=body_str, headers=headers)
        if resp.status_code not in (200, 201):
            raise ImageGenerationError(friendly_image_error_detail(resp.text, model=model), resp.status_code)

        data = resp.json()
        task_id = data.get("id") or data.get("task_id", "")
        if not task_id:
            raise ImageGenerationError("火山视频提交成功但未返回 task_id", 502)

        # 轮询
        poll_url = self._video_task_url(task_id)
        deadline = time.monotonic() + 600  # 10 分钟

        while time.monotonic() < deadline:
            await asyncio.sleep(3)
            poll_headers = self._sign_headers("", method="GET", url=poll_url)
            async with create_client("normal") as client:
                pr = await client.get(poll_url, headers=poll_headers)
            if pr.status_code != 200:
                continue
            pd = pr.json()
            status = str(pd.get("status", "")).lower()
            if status in ("succeeded", "completed", "done", "success"):
                return self._parse_video_urls(pd)
            if status in ("failed", "error", "cancelled"):
                err = pd.get("error", {}).get("message", "视频生成失败") if isinstance(pd.get("error"), dict) else str(pd.get("error", "视频生成失败"))
                raise ImageGenerationError(err, 502)

        raise ImageGenerationError("火山视频任务超时", 504)

    def _parse_video_urls(self, data: dict) -> list[str]:
        """从响应中提取视频 URL"""
        urls = []
        # 标准 seeds_results 格式
        seeds = data.get("seeds_results", [])
        if not seeds and isinstance(data.get("output"), dict):
            seeds = [data["output"]]

        for seed in seeds:
            for item in seed.get("videos", []) if isinstance(seed, dict) else []:
                url = item.get("url", "") if isinstance(item, dict) else str(item)
                if url:
                    urls.append(url)
            # 也检查 images 字段（某些模型返回 video 在 image 字段）
            for item in seed.get("images", []) if isinstance(seed, dict) else []:
                url = item.get("url", "") if isinstance(item, dict) else str(item)
                if url and (".mp4" in url.lower() or "video" in url.lower()):
                    urls.append(url)

        if not urls:
            # 深度遍历
            urls = self._deep_find_urls(data)
        return urls

    @staticmethod
    def _deep_find_urls(obj, depth: int = 0) -> list[str]:
        """递归扫描视频 URL"""
        if depth > 8:
            return []
        urls = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and ("mp4" in v.lower() or "video" in v.lower()):
                    if v.startswith("http") or v.startswith("data:"):
                        urls.append(v)
                urls.extend(VolcengineGateway._deep_find_urls(v, depth + 1))
        elif isinstance(obj, list):
            for item in obj:
                urls.extend(VolcengineGateway._deep_find_urls(item, depth + 1))
        return urls[:20]

    # ================================================================
    # V4 签名
    # ================================================================

    def _sign_headers(self, body: str, method: str = "POST", url: str = "") -> dict:
        """
        V4 签名。

        参数：
          - body: 请求体 JSON 字符串（GET 请求传 ""）
          - method: HTTP 方法（"POST"/"GET"）
          - url: 完整请求 URL，用于提取 host + canonical_uri
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        date_short = now.strftime("%Y%m%d")
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")

        # 从 URL 提取 host + path（不再硬编码）
        host, canonical_uri = self._parse_url(url) if url else ("ark.cn-beijing.volces.com", "/")
        canonical_querystring = ""
        payload_hash = hashlib.sha256(body.encode()).hexdigest()
        canonical_headers = f"content-type:application/json\nhost:{host}\nx-date:{timestamp}\n"
        signed_headers = "content-type;host;x-date"
        canonical_request = f"{method}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"

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
