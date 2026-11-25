"""品牌 logo：三路证据投票（PROMPT §8.3）。

路线：Google Vision LOGO_DETECTION（首选）→ open_clip 品牌向量库 → OCR 文本匹配；
VLM 复核在 Phase 6 接入，只允许在候选品牌内回答。
定级：至少两路一致才 ≥P1；库内必须存在注册信息才允许 P0；单路最高 P2。
OCR 单独命中不算 logo（那是商标词模块的职责），只作为佐证。
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import httpx

from app.config import settings
from app.tools import base, trademark, websearch

logger = logging.getLogger(__name__)

VISION_URL = websearch.VISION_URL
COST_PER_IMAGE_USD = 0.0015

ROUTE_LABELS = {
    "vision": "Vision logo 检测",
    "clip": "CLIP 向量库",
    "ocr": "OCR 文本",
    "vlm": "VLM 复核",
}


def _bbox_from_vertices(polygon: dict, width: int, height: int) -> list[float] | None:
    vertices = (polygon or {}).get("vertices") or []
    if not vertices or width <= 0 or height <= 0:
        return None
    xs = [float(vertex.get("x") or 0) for vertex in vertices]
    ys = [float(vertex.get("y") or 0) for vertex in vertices]
    return [
        round(max(0.0, min(1.0, min(xs) / width)), 4),
        round(max(0.0, min(1.0, min(ys) / height)), 4),
        round(max(0.0, min(1.0, max(xs) / width)), 4),
        round(max(0.0, min(1.0, max(ys) / height)), 4),
    ]


def normalize_vision_response(payload: dict, width: int, height: int) -> dict:
    responses = payload.get("responses") or []
    annotations = (responses[0] if responses else {}).get("logoAnnotations") or []
    logos: list[dict] = []
    for item in annotations:
        description = (item.get("description") or "").strip()
        if not description:
            continue
        logos.append(
            {
                "brand": description,
                "score": round(float(item.get("score") or 0.0), 3),
                "bbox": _bbox_from_vertices(item.get("boundingPoly") or {}, width, height),
            }
        )
    return {"provider": "google_vision_logo", "logos": logos}


def _image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except Exception:
        return (0, 0)


def _live_detect(path: Path) -> dict:
    body = {
        "requests": [
            {
                "image": {"content": base64.b64encode(path.read_bytes()).decode()},
                "features": [{"type": "LOGO_DETECTION", "maxResults": 10}],
            }
        ]
    }
    url, headers = websearch.vision_auth()
    response = httpx.post(url, json=body, headers=headers, timeout=8.0)
    response.raise_for_status()
    width, height = _image_size(path)
    return normalize_vision_response(response.json(), width, height)


_clip_state: dict = {"model": None, "preprocess": None, "tokenizer": None, "failed": False}


def _clip_encoder():
    """懒加载 open_clip；未安装或权重缺失时返回 None（降级为其他路线）。"""
    if _clip_state["model"] is not None:
        return _clip_state["model"], _clip_state["preprocess"]
    if _clip_state["failed"]:
        return None
    try:
        import open_clip

        model, _, preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="openai", device="cpu"
        )
        model.eval()
        _clip_state["model"] = model
        _clip_state["preprocess"] = preprocess
        return model, preprocess
    except Exception as exc:
        logger.info("open_clip 不可用，logo 向量路线跳过: %s", exc)
        _clip_state["failed"] = True
        return None


def _load_logo_vectors() -> tuple[list[str], object] | None:
    path = Path(settings.watchlists_dir) / "logo_vectors.npz"
    index_path = Path(settings.watchlists_dir) / "logo_vector_index.json"
    if not path.exists() or not index_path.exists():
        return None
    try:
        import numpy as np

        data = np.load(path)
        matrix = data["vectors"]
        marks = json.loads(index_path.read_text(encoding="utf-8")).get("marks") or []
        if len(marks) != matrix.shape[0]:
            logger.warning("logo 向量索引与矩阵行数不一致，跳过")
            return None
        return marks, matrix
    except Exception as exc:
        logger.warning("logo 向量加载失败: %s", exc)
        return None


def rank_vectors(
    image_vector,
    matrix,
    marks: list[str],
    *,
    top_k: int | None = None,
    min_score: float | None = None,
) -> list[dict]:
    """余弦相似度 top-k；纯函数，便于单测。"""
    import numpy as np

    top_k = settings.logo_clip_top_k if top_k is None else top_k
    min_score = settings.logo_clip_min_score if min_score is None else min_score
    image = np.asarray(image_vector, dtype="float32").reshape(-1)
    matrix = np.asarray(matrix, dtype="float32")
    if matrix.ndim != 2 or matrix.shape[1] != image.shape[0] or not marks:
        return []
    image_norm = image / (np.linalg.norm(image) or 1.0)
    rows = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-8)
    scores = rows @ image_norm
    order = np.argsort(-scores)[: max(1, top_k)]
    return [
        {"brand": marks[index], "score": round(float(scores[index]), 3)}
        for index in order
        if float(scores[index]) >= min_score
    ]


def clip_compare(path: Path) -> list[dict]:
    encoder = _clip_encoder()
    loaded = _load_logo_vectors()
    if encoder is None or loaded is None:
        return []
    model, preprocess = encoder
    marks, matrix = loaded
    try:
        import torch
        from PIL import Image

        with Image.open(path) as image:
            tensor = preprocess(image.convert("RGB")).unsqueeze(0)
        with torch.no_grad():
            features = model.encode_image(tensor)
            features = features / features.norm(dim=-1, keepdim=True)
        return rank_vectors(features[0].cpu().numpy(), matrix, marks)
    except Exception as exc:
        logger.warning("CLIP 比对失败 %s: %s", path, exc)
        return []


def detect(
    runtime: base.ToolRuntime,
    image_path: str | Path,
    *,
    asset_sha256: str,
    scene_index: int,
    timestamp: float,
) -> base.ToolResult:
    path = Path(image_path)
    mode = base.resolve_mode("logo")

    def run() -> dict | None:
        if mode == "mock":
            return base.mock_entry("logo", asset_sha256, scene_index)
        data = _live_detect(path)
        data["clip"] = clip_compare(path)
        return data

    return runtime.call(
        tool="logo",
        input_hash=f"{asset_sha256}:logo:{scene_index}:{timestamp:.2f}",
        fn=run,
        cost_usd=COST_PER_IMAGE_USD,
        mode=mode,
        timeout=10.0,
    )


def _merge_route(merged: dict, hit: dict, route: str) -> None:
    name = str(hit.get("brand") or "").strip()
    key = trademark.normalize_text(name)
    if not key:
        return
    entry = merged.setdefault(
        key, {"brand": name, "routes": set(), "bbox": None, "score": 0.0}
    )
    entry["routes"].add(route)
    entry["score"] = max(entry["score"], float(hit.get("score") or 0.0))
    if entry["bbox"] is None and hit.get("bbox") is not None:
        entry["bbox"] = hit["bbox"]


def assess_hits(
    vision_hits: list[dict],
    clip_hits: list[dict],
    ocr_hits: list[dict],
    *,
    vlm_hits: list[dict] | None = None,
    catalog: trademark.Catalog | None = None,
) -> list[dict]:
    """三路投票 → 待落库信号（不含时间戳证据）。"""
    catalog = catalog or trademark.load_catalog()
    merged: dict[str, dict] = {}
    for hit in vision_hits:
        _merge_route(merged, hit, "vision")
    for hit in clip_hits:
        _merge_route(merged, hit, "clip")
    for hit in ocr_hits:
        _merge_route(merged, hit, "ocr")
    for hit in vlm_hits or []:
        _merge_route(merged, hit, "vlm")

    signals: list[dict] = []
    for entry in merged.values():
        routes = entry["routes"]
        if not routes - {"ocr"}:
            continue  # 只有 OCR 文本命中时交给商标词模块
        brand = trademark.resolve_brand(entry["brand"], catalog)
        votes = len(routes)
        if brand is not None and votes >= 2:
            level = "P0" if brand.has_registry else "P1"
        else:
            level = "P2"

        evidence: list[dict] = []
        if brand is not None and brand.has_registry:
            evidence.append(
                {
                    "kind": "reference",
                    "ref_record_id": (
                        f"trademark:{brand.reg_office or 'registry'}:{brand.reg_no}"
                    ),
                    "ref_url": brand.source_url,
                    "note": (
                        f"{brand.mark} · {brand.registry_label}"
                        + (f" · {brand.owner}" if brand.owner else "")
                        + f" · 证据路线 {'/'.join(sorted(ROUTE_LABELS[r] for r in routes))}"
                    ),
                }
            )
        else:
            evidence.append(
                {
                    "kind": "reference",
                    "ref_record_id": f"logo_library:{entry['brand']}",
                    "note": (
                        f"logo 库命中「{entry['brand']}」"
                        + ("（无注册信息）" if brand is not None else "（词表外品牌）")
                        + f" · 证据路线 {'/'.join(sorted(ROUTE_LABELS[r] for r in routes))}"
                    ),
                }
            )
        if entry["bbox"]:
            evidence.append(
                {
                    "kind": "bbox",
                    "bbox": trademark.format_bbox(entry["bbox"]),
                    "note": "品牌标识区域",
                }
            )

        route_text = "、".join(ROUTE_LABELS[r] for r in sorted(routes))
        registry = f"，参照 {brand.registry_label}" if brand and brand.has_registry else ""
        signals.append(
            {
                "tool": "logo",
                "category": "logo",
                "level": level,
                "confidence": round(min(0.97, entry["score"] or 0.6), 3),
                "suggestion": (
                    f"检测到疑似 {entry['brand']} 品牌标识（{route_text}，{votes} 路证据）"
                    f"{registry}，建议核实使用授权。"
                ),
                "dedupe_key": f"logo:{entry['brand']}",
                "evidence": evidence,
            }
        )
    return signals


def signals(
    vision_hits: list[dict],
    clip_hits: list[dict],
    ocr_hits: list[dict],
    *,
    frame: dict,
    vlm_hits: list[dict] | None = None,
    catalog: trademark.Catalog | None = None,
) -> list[dict]:
    """按帧产出信号，补时间戳证据；与其它类别共用 dedupe_key 去重。"""
    result = assess_hits(
        vision_hits, clip_hits, ocr_hits, vlm_hits=vlm_hits, catalog=catalog
    )
    timestamp = frame.get("timestamp")
    for signal in result:
        if timestamp is not None:
            signal["evidence"].append(
                {
                    "kind": "timestamp",
                    "t_start": timestamp,
                    "note": f"关键帧 #{frame.get('index')}（分镜 {frame.get('scene_index')}）",
                }
            )
    return result
