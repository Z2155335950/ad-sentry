"""版权图像：Google Vision WEB_DETECTION 反向图搜（PROMPT §8.1）。

mock 按「素材 sha256 + 分镜索引」读 `fixtures/cache/image_search.json`；
live 需要 `GOOGLE_VISION_API_KEY` 或服务账号 JSON（后者需安装 google-auth）。
未配置时返回 unavailable，由融合层决定是否改用内部 pHash 查重。
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.tools import base

logger = logging.getLogger(__name__)

VISION_URL = "https://vision.googleapis.com/v1/images:annotate"
COST_PER_IMAGE_USD = 0.0015

STOCK_DOMAINS = (
    "shutterstock.com",
    "gettyimages.com",
    "istockphoto.com",
    "adobe.com",
    "stock.adobe.com",
    "alamy.com",
    "depositphotos.com",
    "123rf.com",
    "dreamstime.com",
    "freepik.com",
    "vecteezy.com",
)
ECOMMERCE_DOMAINS = (
    "amazon.com",
    "amazon.cn",
    "ebay.com",
    "etsy.com",
    "aliexpress.com",
    "shopee.com",
    "lazada.com",
    "taobao.com",
    "tmall.com",
    "jd.com",
    "walmart.com",
    "shopify.com",
)
SOCIAL_DOMAINS = (
    "instagram.com",
    "facebook.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "pinterest.com",
    "weibo.com",
    "xiaohongshu.com",
)
BLOG_DOMAINS = (
    "medium.com",
    "blogspot.com",
    "wordpress.com",
    "substack.com",
    "zhihu.com",
    "csdn.net",
)

_STRENGTH = {"full": 3, "page": 2, "partial": 2, "similar": 1}


def classify_domain(url: str) -> str:
    parsed = urlsplit(url if "://" in url else f"//{url}")
    host = (parsed.hostname or "").lower()
    if not host:
        return "unknown"
    for domain in STOCK_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return "stock"
    for domain in ECOMMERCE_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return "ecommerce"
    for domain in SOCIAL_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return "social"
    for domain in BLOG_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return "blog"
    return "unknown"


def normalize_vision_response(payload: dict) -> dict:
    """把 Vision 响应收敛为与 mock 相同的结构，融合层不关心来源。"""
    responses = payload.get("responses") or []
    web = (responses[0] if responses else {}).get("webDetection") or {}
    matches: list[dict] = []
    seen: set[str] = set()

    def add(url: str, title: str | None, match_type: str) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        matches.append(
            {
                "url": url,
                "title": title,
                "match_type": match_type,
                "domain_kind": classify_domain(url),
            }
        )

    for item in web.get("fullMatchingImages") or []:
        add(item.get("url"), item.get("title"), "full")
    for item in web.get("partialMatchingImages") or []:
        add(item.get("url"), item.get("title"), "partial")
    for item in web.get("pagesWithMatchingImages") or []:
        url = item.get("url")
        title = item.get("pageTitle") or item.get("title")
        nested_full = item.get("fullMatchingImages") or []
        nested_partial = item.get("partialMatchingImages") or []
        match_type = "full" if nested_full else "partial" if nested_partial else "page"
        add(url, title, match_type)
    for item in web.get("visuallySimilarImages") or []:
        add(item.get("url"), item.get("title"), "similar")

    labels = [
        item.get("label")
        for item in web.get("bestGuessLabels") or []
        if item.get("label")
    ]
    return {
        "provider": "google_vision",
        "matches": matches,
        "best_guess_labels": labels,
    }


def assess(normalized: dict) -> dict:
    """按 PROMPT §8.1 定级：full/partial + 图库域名 → P0；匹配页未知来源 → P1；
    仅 visually similar → P2；无命中 → 不产出。"""
    matches = normalized.get("matches") or []
    if not matches:
        return {"level": None, "confidence": None, "matches": []}

    # 归一化后是 "similar"；手工 mock 里可能写成 "visually_similar"，一并按弱命中处理
    weak_types = {"similar", "visually_similar"}
    strong = [m for m in matches if m.get("match_type") not in weak_types]
    stock = [m for m in strong if m.get("domain_kind") == "stock"]
    if stock:
        others = [m for m in strong if m.get("domain_kind") != "stock"]
        return {"level": "P0", "confidence": 0.9, "matches": (stock + others)[:3]}
    if strong:
        return {"level": "P1", "confidence": 0.72, "matches": strong[:3]}
    return {"level": "P2", "confidence": 0.5, "matches": matches[:3]}


def _live_search(image_path: Path) -> dict:
    body = {
        "requests": [
            {
                "image": {"content": base64.b64encode(image_path.read_bytes()).decode()},
                "features": [{"type": "WEB_DETECTION", "maxResults": 10}],
            }
        ]
    }
    url, headers = vision_auth()
    response = httpx.post(
        url,
        json=body,
        headers=headers,
        timeout=settings.timeout_image_search,
    )
    response.raise_for_status()
    return normalize_vision_response(response.json())


def vision_auth() -> tuple[str, dict]:
    if settings.google_vision_api_key:
        return f"{VISION_URL}?key={settings.google_vision_api_key}", {}
    if not settings.google_application_credentials:
        raise base.ToolUnavailable("未配置 GOOGLE_VISION_API_KEY / 服务账号")
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import service_account
    except ImportError as exc:
        raise base.ToolUnavailable("服务账号鉴权需要 google-auth") from exc
    credentials = service_account.Credentials.from_service_account_file(
        settings.google_application_credentials,
        scopes=["https://www.googleapis.com/auth/cloud-vision"],
    )
    credentials.refresh(GoogleRequest())
    return VISION_URL, {"Authorization": f"Bearer {credentials.token}"}


def search_image(
    runtime: base.ToolRuntime,
    image_path: str | Path,
    *,
    asset_sha256: str,
    scene_index: int,
    timestamp: float,
) -> base.ToolResult:
    path = Path(image_path)
    mode = base.effective_mode("image_search", asset_sha256, scene_index)

    def run() -> dict | None:
        if mode == "demo":
            return base.demo_entry("image_search", asset_sha256, scene_index)
        if mode == "mock":
            return base.mock_entry("image_search", asset_sha256, scene_index)
        return _live_search(path)

    return runtime.call(
        tool="image_search",
        input_hash=f"{asset_sha256}:{scene_index}:{timestamp:.2f}",
        fn=run,
        cost_usd=COST_PER_IMAGE_USD,
        mode=mode,
        timeout=settings.timeout_image_search + 2,
    )
