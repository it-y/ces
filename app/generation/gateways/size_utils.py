"""
尺寸映射工具 — parse_size_pair / apimart_size_resolution / unwrap_apimart_response。

从原版 main.py:7256-7281 和 main.py:3512-3516 迁移，重构为独立模块。
"""

import re

# ---- 比例 + 分辨率阈值 ----

# APIMart 支持的标准比例（按原版顺序）
_ASPECT_RATIOS = [
    (1, 1, "1:1"), (3, 2, "3:2"), (2, 3, "2:3"),
    (4, 3, "4:3"), (3, 4, "3:4"),
    (5, 4, "5:4"), (4, 5, "4:5"),
    (16, 9, "16:9"), (9, 16, "9:16"),
    (2, 1, "2:1"), (1, 2, "1:2"),
    (3, 1, "3:1"), (1, 3, "1:3"),
    (21, 9, "21:9"), (9, 21, "9:21"),
]

# 分辨率档位阈值（最长边像素）
_EDGE_4K = 3000
_EDGE_2K = 1800
# 额外像素阈值
_PIXELS_4K = 4_500_000
_PIXELS_2K = 1_800_000


def parse_size_pair(size) -> tuple[int, int]:
    """解析 'WxH' 或 'W*H' 字符串 → (width, height)。失败返回 (0, 0)。"""
    if not size:
        return 0, 0
    m = re.fullmatch(r"\s*(\d+)\s*[xX*×]\s*(\d+)\s*", str(size).strip())
    if not m:
        return 0, 0
    return int(m.group(1)), int(m.group(2))


def apimart_size_resolution(size) -> tuple[str, str]:
    """
    将尺寸转为 APIMart 的 (aspect_ratio, resolution) 格式。

    - '1024x1024' → ('1:1', '2k')
    - '1920x1080' → ('16:9', '2k')
    - '1k' / '2k' / '4k' → ('1:1', '1k') 等
    - '16:9' 等比例字符串 → ('16:9', '1k')
    - 无法解析 → ('1:1', '1k')
    """
    width, height = parse_size_pair(size)

    if not width or not height:
        raw = str(size or "").strip().lower()
        if raw in {"1k", "2k", "4k"}:
            return "1:1", raw
        if re.fullmatch(r"(auto|\d+\s*:\s*\d+)", raw):
            return raw.replace(" ", ""), "1k"
        return "1:1", "1k"

    # 分辨率档位
    long_edge = max(width, height)
    pixels = width * height
    if long_edge >= _EDGE_4K or pixels > _PIXELS_4K:
        resolution = "4k"
    elif long_edge >= _EDGE_2K or pixels > _PIXELS_2K:
        resolution = "2k"
    else:
        resolution = "1k"

    # 最近比例匹配
    ratio = width / height
    best = min(_ASPECT_RATIOS, key=lambda item: abs(ratio - item[0] / item[1]))
    return best[2], resolution


def unwrap_apimart_response(raw):
    """
    解包 APIMart 的 {code: 200, data: {...}} 包裹格式。

    仅在顶层无 'choices' 时才解包（避免误解标准 OpenAI 响应）。
    幂等：重复调用结果不变。
    """
    if isinstance(raw, dict) and "data" in raw and isinstance(raw.get("data"), dict) and "choices" not in raw:
        return raw["data"]
    return raw
