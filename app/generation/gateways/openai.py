"""
OpenAI 兼容协议网关 — 支持 OpenAI/Apimart/Gemini-compatible 等。

修改记录：
  - 加 retry（指数退避 3 次）
  - _parse_image_urls 加深度递归 fallback
  - HTTP/1.1 兼容（中转站常见不支持 HTTP/2）
"""

import asyncio
import base64
import json
import re
import time
from typing import Optional
from pathlib import Path

from ...core.http_client import create_client, retry_request
from ...core.errors import friendly_image_error_detail
from ...config import UPLOAD_DIR, OUTPUT_DIR, CANVAS_FILES_DIR
from .size_utils import apimart_size_resolution, unwrap_apimart_response
from ..task_poller import extract_task_id, poll_image_task


# ---- GPT-Image-2 常量 ----

GPT_IMAGE2_MAX_EDGE = 3840
GPT_IMAGE2_MAX_PIXELS = 8_294_400
GPT_IMAGE2_MIN_PIXELS = 655_360


class OpenAIGateway:
    """OpenAI 兼容图片生成网关"""

    def __init__(self, provider: dict):
        self.provider = provider
        self.base_url = provider.get("base_url", "").rstrip("/")
        self.api_key = provider.get("api_key", "")
        self.protocol = provider.get("protocol", "openai")
        from ...system.providers import effective_image_request_mode
        self.image_request_mode = effective_image_request_mode(provider)
        from ...system.providers import is_apimart_provider, is_openrouter_provider
        self.is_apimart = is_apimart_provider(provider)
        self.is_openrouter = is_openrouter_provider(provider)

    # ---- 主入口 ----

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        model: str = "",
        quality: str = "auto",
        n: int = 1,
        reference_images: list | None = None,
    ) -> list[str]:
        """
        生成图片。返回 URL 列表。
        如果有参考图，走 edit 端点；否则走 generations 端点。
        """
        if reference_images:
            return await self._generate_with_refs(prompt, size, model, reference_images)
        return await self._generate_simple(prompt, size, model, quality, n)

    # ---- 视频生成 ----

    async def generate_video(
        self,
        prompt: str,
        model: str = "",
        duration: int = 8,
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        size: str = "",
        images: list | None = None,
        videos: list | None = None,
        audios: list | None = None,
        seed: int | None = None,
        return_last_frame: bool = False,
        generate_audio: bool = False,
        **kwargs,
    ) -> list[str]:
        """
        APIMart 视频生成（VEO 3.1 / Seedance）。
        返回视频 URL 列表。
        """
        from .apimart_video import (
            is_veo31_model, build_veo31_body, build_seedance_body,
            video_submit_url, upload_image_for_apimart,
            valid_apimart_media_url, poll_video_task, video_output_urls,
            extract_task_id as _extract_task_id,
        )
        from ...core.http_client import create_client

        if not model:
            vmodels = self.provider.get("video_models", [])
            model = vmodels[0] if vmodels else "veo3.1-fast"

        is_veo31 = is_veo31_model(model)
        submit_url = video_submit_url(self.provider)

        # 上传参考图（APIMart 需要 http/https URL）
        image_urls = []
        if images:
            async with create_client("normal") as client:
                for ref in (images or [])[:9]:
                    ref_url = str(ref.get("url", "") if isinstance(ref, dict) else str(ref or ""))
                    if not ref_url:
                        continue
                    if valid_apimart_media_url(ref_url):
                        image_urls.append(ref_url)
                    else:
                        up = await upload_image_for_apimart(client, self.provider, ref_url)
                        if valid_apimart_media_url(up):
                            image_urls.append(up)

        # 构造 body
        if is_veo31:
            body = build_veo31_body(
                prompt, model, duration, aspect_ratio, resolution, image_urls,
            )
        else:
            body = build_seedance_body(
                prompt, model, duration, aspect_ratio, resolution, size,
                image_urls=image_urls,
                seed=seed, return_last_frame=return_last_frame,
                generate_audio=generate_audio,
            )

        # 提交
        async with create_client("fast") as client:
            headers = self._auth_headers()
            headers["Content-Type"] = "application/json"
            resp = await client.post(submit_url, json=body, headers=headers, timeout=1800)
            if resp.status_code not in (200, 201):
                from ...core.errors import friendly_image_error_detail
                msg = friendly_image_error_detail(resp.text, model=model)
                raise ImageGenerationError(msg or f"视频提交失败：{resp.text[:300]}", resp.status_code)

            raw = resp.json()

            # 尝试直接提取视频 URL
            urls = video_output_urls(raw)
            if urls:
                return urls

            # 提取 task_id 并轮询
            task_id = _extract_task_id(raw) or raw.get("task_id") or raw.get("id")
            if task_id:
                result = await poll_video_task(client, self.provider, task_id, submit_url)
                urls = video_output_urls(result)
                if urls:
                    return urls

            raise ImageGenerationError("视频生成成功但未返回视频数据", 502)

    # ---- retry 请求包装 ----

    async def _post_with_retry(self, url: str, **kwargs) -> dict:
        """直连 POST + 重试，返回 JSON 响应体"""
        resp = await retry_request("POST", url, **kwargs)
        if resp.status_code != 200:
            msg = friendly_image_error_detail(resp.text)
            raise ImageGenerationError(msg, resp.status_code)
        return resp.json()

    # ---- 简单生成（无参考图） ----

    async def _generate_simple(
        self, prompt: str, size: str, model: str,
        quality: str, n: int,
    ) -> list[str]:
        url = f"{self.base_url}/v1/images/generations"
        model = model or self.provider.get("image_models", [""])[0]

        # Apimart 协议：专用 body 格式
        if self.is_apimart:
            aspect, resolution = apimart_size_resolution(size)
            body = {
                "model": model,
                "prompt": prompt,
                "n": 1,
                "size": aspect,
                "resolution": resolution,
                "official_fallback": False,
            }
            quality = self._normalize_quality(quality)
            if quality:
                body["quality"] = quality
            data = await self._post_with_retry(
                url, json=body, headers=self._auth_headers(),
            )
            data = unwrap_apimart_response(data)
            return await self._images_or_poll(data)

        # 标准 OpenAI 协议
        quality = self._normalize_quality(quality)
        # GPT-Image-2 不带 n（对齐原版行为：部分中转站带 n 会拒绝/挂起）
        body = {
            "prompt": prompt,
            "model": model,
            "size": size,
        }
        if not self._is_gpt_image_2(model):
            body["n"] = n
        if quality:
            body["quality"] = quality
        if self.image_request_mode == "openai-json":
            body = {"extra_body": body}

        try:
            data = await self._post_with_retry(
                url,
                json=body,
                headers=self._auth_headers(),
            )
            return await self._images_or_poll(data)
        except ImageGenerationError as e:
            # 某些中转站不支持 /images/generations，回退到 edits 端点
            if self._images_api_unsupported(str(e)):
                raise ImageGenerationError(
                    "当前供应商不支持 /images/generations 接口。请尝试使用参考图模式，"
                    "或在供应商设置中将协议切换为其他类型。",
                    400,
                )
            raise

    # ---- 带参考图生成 ----

    async def _generate_with_refs(
        self, prompt: str, size: str, model: str, refs: list,
    ) -> list[str]:
        """带参考图的生成。
        - apimart：走 JSON image_urls（数据 URL 内嵌）
        - openai-json 模式：走 /v1/images/generations（JSON body，参考图转为 data URL）
        - 其他模式：走 /v1/images/edits（multipart）
        如果无法获取任何参考图文件，降级为无参考图生成。
        """
        model = model or self.provider.get("image_models", [""])[0]

        # Apimart：参考图用 image_urls JSON，不走 multipart
        if self.is_apimart:
            image_urls = []
            for ref in refs:
                data_url = await self._ref_to_data_url(ref, max_size=1536)
                if data_url:
                    image_urls.append(data_url)
            if not image_urls:
                return await self._generate_simple(prompt, size, model, "auto", 1)

            aspect, resolution = apimart_size_resolution(size)
            body = {
                "model": model,
                "prompt": prompt,
                "n": 1,
                "size": aspect,
                "resolution": resolution,
                "official_fallback": False,
                "image_urls": image_urls,
            }
            quality = self._normalize_quality("auto")
            if quality:
                body["quality"] = quality
            data = await self._post_with_retry(
                f"{self.base_url}/v1/images/generations",
                json=body, headers=self._auth_headers(),
            )
            data = unwrap_apimart_response(data)
            return await self._images_or_poll(data)

        # OpenRouter：使用 input_references 而非 image_urls
        if self.is_openrouter:
            input_refs = []
            for ref in refs:
                data_url = await self._ref_to_data_url(ref, max_size=1536)
                if data_url:
                    input_refs.append({"url": data_url, "detail": "high"})
            if not input_refs:
                return await self._generate_simple(prompt, size, model, "auto", 1)

            body = {
                "model": model,
                "prompt": prompt,
                "n": 1,
                "size": size,
                "response_format": "url",
                "input_references": input_refs,
            }
            quality = self._normalize_quality("auto")
            if quality:
                body["quality"] = quality

            data = await self._post_with_retry(
                f"{self.base_url}/v1/images/generations",
                json=body, headers=self._auth_headers(),
            )
            return await self._images_or_poll(data)

        # openai-json 模式：JSON body 传 data URL
        if self.image_request_mode == "openai-json":
            extra_body = {"response_format": "url"}
            image_urls = []
            for ref in refs:
                data_url = await self._ref_to_data_url(ref)
                if data_url:
                    image_urls.append(data_url)
            if image_urls:
                extra_body["image"] = image_urls
            body = {"model": model, "prompt": prompt, "size": size, "extra_body": extra_body}
            url = f"{self.base_url}/v1/images/generations"
            raw = await self._post_with_retry(url, json=body, headers=self._auth_headers())
            return await self._images_or_poll(raw)

        # multipart 模式：走 /v1/images/edits
        # GPT-Image-2 参考图不能走 /images/generations JSON，必须走 multipart edits。
        # edits 失败 + GPT-2 → 直接报错（防重复扣费，上游可能已扣费）
        # edits 失败 + 非 GPT-2 → 回退 JSON /images/generations + image:[data_urls]
        is_gpt2 = self._is_gpt_image_2(model)
        edit_failed_text = ""

        files = []
        for ref in refs:
            img_data = await self._fetch_image_bytes(ref.get("url", ""))
            if img_data:
                files.append(("image", (ref.get("name", "ref.png"), img_data, ref.get("mime", "image/png"))))

        if not files:
            return await self._generate_simple(prompt, size, model, "auto", 1)

        url = f"{self.base_url}/v1/images/edits"
        data = {"prompt": prompt, "model": model, "size": size}

        try:
            raw = await self._post_with_retry(url, files=files, data=data, headers=self._auth_headers())
            return await self._images_or_poll(raw)
        except ImageGenerationError as e:
            edit_failed_text = str(e)

        # ---- edits 失败回退 ----
        if is_gpt2:
            raise ImageGenerationError(
                f"GPT-Image-2 编辑接口 /images/edits 调用失败：{edit_failed_text[:300]}。"
                "已停止自动重试，避免上游可能已扣费后再次请求。",
                status_code=502,
            )

        # 非 GPT-2：回退到 JSON /images/generations + image:[data_urls]
        image_urls = []
        for ref in refs:
            data_url = await self._ref_to_data_url(ref, max_size=1536)
            if data_url:
                image_urls.append(data_url)
        if not image_urls:
            return await self._generate_simple(prompt, size, model, "auto", 1)

        fallback_body = {
            "model": model, "prompt": prompt, "size": size,
            "response_format": "url", "n": 1,
            "image": image_urls,
        }
        quality_val = self._normalize_quality("auto")
        if quality_val:
            fallback_body["quality"] = quality_val

        gen_url = f"{self.base_url}/v1/images/generations"
        raw = await self._post_with_retry(gen_url, json=fallback_body, headers=self._auth_headers())
        return await self._images_or_poll(raw)

    async def _ref_to_data_url(self, ref: dict, max_size: int = 0) -> str | None:
        """将参考图转为 data URL。max_size>0 时缩略到最长边不超过该值（异步执行 Pillow）。"""
        data = await self._fetch_image_bytes(ref.get("url", ""))
        if not data:
            return None

        if max_size and max_size > 0:
            try:
                data = await asyncio.to_thread(self._resize_image_bytes, data, max_size)
            except Exception:
                pass  # resize 失败用原图

        import base64
        mime = ref.get("mime", "") or "image/png"
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    @staticmethod
    def _resize_image_bytes(data: bytes, max_size: int) -> bytes:
        """同步 Pillow 缩略（在 asyncio.to_thread 中执行）。"""
        from io import BytesIO
        from PIL import Image
        img = Image.open(BytesIO(data))
        img.load()
        w, h = img.size
        if max(w, h) <= max_size:
            return data
        img.thumbnail((max_size, max_size), Image.LANCZOS)
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        fmt = "PNG" if has_alpha else "JPEG"
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if has_alpha else "RGB")
        buf = BytesIO()
        img.save(buf, format=fmt, quality=88 if fmt == "JPEG" else None)
        return buf.getvalue()

    # ---- 结果解析 + 轮询 fallback ----

    async def _images_or_poll(self, data: dict) -> list[str]:
        """
        先尝试从响应中直接提取图片 URL。
        若没有，检测 task_id 并轮询异步任务直到完成。
        """
        urls = self._parse_image_urls(data)
        if urls:
            return urls

        task_id = extract_task_id(data)
        if not task_id:
            return []

        polled = await poll_image_task(task_id, self.provider)
        return self._parse_image_urls(polled)

    def _parse_image_urls(self, data: dict) -> list[str]:
        """
        从 OpenAI 响应中提取图片 URL。
        优先标准格式 data[].url / data[].b64_json，
        失败后用深度递归扫描整个响应体。
        """
        urls = []
        for item in data.get("data", []):
            if isinstance(item, dict):
                if "url" in item and isinstance(item["url"], str):
                    urls.append(item["url"])
                elif "b64_json" in item and isinstance(item["b64_json"], str):
                    urls.append(f"data:image/png;base64,{item['b64_json']}")

        if urls:
            return urls

        # fallback: 递归扫描整个响应，找到所有合法图片 URL 或 base64
        urls = self._deep_scan_urls(data)
        return urls

    @staticmethod
    def _deep_scan_urls(obj, max_depth: int = 8) -> list[str]:
        """递归扫描对象，找出所有以 http 或 data:image 开头的图片 URL"""
        found = set()

        def _scan(val, depth=0):
            if depth > max_depth:
                return
            if isinstance(val, dict):
                for v in val.values():
                    if isinstance(v, str) and (v.startswith("http") or v.startswith("data:image")):
                        found.add(v)
                    else:
                        _scan(v, depth + 1)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str) and (item.startswith("http") or item.startswith("data:image")):
                        found.add(item)
                    else:
                        _scan(item, depth + 1)

        _scan(obj)
        return list(found)[:20]

    # ---- 工具 ----

    @staticmethod
    def _is_gpt_image_2(model: str) -> bool:
        """检测是否为 GPT-Image-2 模型（兼容各种命名变体）。"""
        raw = str(model or "").strip().lower()
        normalized = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
        compact = re.sub(r"[^a-z0-9]+", "", raw)
        return (
            normalized == "gpt-image-2"
            or normalized.startswith("gpt-image-2-")
            or normalized.endswith("-gpt-image-2")
            or "-gpt-image-2-" in normalized
            or compact == "gptimage2"
            or compact.startswith("gptimage2")
            or compact.endswith("gptimage2")
        )

    def _images_api_unsupported(self, response_text: str) -> bool:
        """检测上游是否不支持 /images API（常见于某些中转站）。"""
        text = str(response_text or "").lower()
        return "images api is not supported" in text or "not supported for this platform" in text

    @staticmethod
    def _normalize_quality(quality: str) -> str:
        """校验 quality 值，仅 'low'/'medium'/'high' 合法，其余返回空字符串（不传）。"""
        q = str(quality or "").strip().lower()
        return q if q in {"low", "medium", "high"} else ""

    def _auth_headers(self) -> dict:
        key = self.api_key
        if key and not key.startswith("Bearer "):
            key = f"Bearer {key}"
        headers = {}
        if key:
            headers["Authorization"] = key
        return headers

    def _local_path_from_url(self, url: str) -> Optional[Path]:
        """将本地 URL（/assets/xxx, /output/xxx, /cfiles/xxx）转为文件系统路径"""
        from pathlib import Path as _Path
        path_part = url.split("?")[0]
        if path_part.startswith("/assets/"):
            return _Path(UPLOAD_DIR) / path_part[len("/assets/"):]
        if path_part.startswith("/output/"):
            return _Path(OUTPUT_DIR) / path_part[len("/output/"):]
        if path_part.startswith("/cfiles/"):
            return _Path(CANVAS_FILES_DIR) / path_part[len("/cfiles/"):]

    async def _fetch_image_bytes(self, url: str) -> Optional[bytes]:
        """下载远程图片或从本地文件读取"""
        if not url:
            return None
        if url.startswith("data:"):
            try:
                _, encoded = url.split(",", 1)
                return base64.b64decode(encoded)
            except Exception:
                return None
        if url.startswith("/"):
            local = self._local_path_from_url(url)
            if local and local.exists():
                return local.read_bytes()
            return None
        try:
            async with create_client("normal") as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.content
        except Exception:
            pass
        return None


class ImageGenerationError(Exception):
    """图片生成失败"""
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code
