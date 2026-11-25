"""公众人物：AWS Rekognition RecognizeCelebrities（PROMPT §8.4）。

合规约束：
- 只对检测到的人脸裁剪后外发，不发送整帧；
- 只落库姓名 / 置信度 / bbox / 时间戳，不落人脸图像或特征；
- 未命中不做任何推断。
mock 读 `fixtures/cache/celebrity.json`（按素材 sha256 + 分镜索引）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.config import settings
from app.tools import base

logger = logging.getLogger(__name__)

COST_PER_IMAGE_USD = 0.001
MIN_FACE_PIXELS = 32


def detect_faces(image_path: str | Path) -> list[str]:
    """本地 Haar 级联找人脸，返回归一化 bbox；不可用时返回空列表。"""
    try:
        import cv2

        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(str(cascade_path))
        image = cv2.imread(str(image_path))
        if image is None or cascade.empty():
            return []
        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(MIN_FACE_PIXELS, MIN_FACE_PIXELS)
        )
    except Exception as exc:  # 人脸检测只是裁剪优化，失败就不外发
        logger.debug("人脸检测不可用 %s: %s", image_path, exc)
        return []
    boxes = []
    for x, y, w, h in faces:
        boxes.append(
            f"{x / width:.3f},{y / height:.3f},{(x + w) / width:.3f},{(y + h) / height:.3f}"
        )
    return boxes


def crop_face_bytes(image_path: Path, bbox: str, pad_ratio: float = 0.2) -> bytes:
    """裁剪人脸到内存，绝不落盘（PROMPT §2：人脸图像与特征不落盘）。"""
    from io import BytesIO

    from PIL import Image

    x1, y1, x2, y2 = (float(part) for part in bbox.split(","))
    pad_x = (x2 - x1) * pad_ratio
    pad_y = (y2 - y1) * pad_ratio
    with Image.open(image_path) as image:
        width, height = image.size
        left = max(0, int((x1 - pad_x) * width))
        top = max(0, int((y1 - pad_y) * height))
        right = min(width, int((x2 + pad_x) * width))
        bottom = min(height, int((y2 + pad_y) * height))
        crop = image.crop((left, top, right, bottom))
        buffer = BytesIO()
        crop.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signature(secret_key: str, date_stamp: str, region: str, service: str, string: str) -> str:
    key = _sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    key = _sign(key, region)
    key = _sign(key, service)
    key = _sign(key, "aws4_request")
    return hmac.new(key, string.encode("utf-8"), hashlib.sha256).hexdigest()


def _sigv4_headers(body: bytes) -> tuple[dict, str]:
    region = settings.aws_region
    host = f"rekognition.{region}.amazonaws.com"
    amz_date = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    date_stamp = amz_date[:8]
    payload_hash = hashlib.sha256(body).hexdigest()
    headers = {
        "content-type": "application/x-amz-json-1.1",
        "host": host,
        "x-amz-date": amz_date,
        "x-amz-target": "RekognitionService.RecognizeCelebrities",
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{key}:{headers[key]}\n" for key in sorted(headers))
    canonical_request = (
        f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )
    scope = f"{date_stamp}/{region}/rekognition/aws4_request"
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )
    signature = _signature(
        settings.aws_secret_access_key, date_stamp, region, "rekognition", string_to_sign
    )
    headers["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={settings.aws_access_key_id}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return headers, host


def normalize_response(payload: dict) -> dict:
    celebrities = []
    for face in payload.get("CelebrityFaces") or []:
        box = face.get("BoundingBox") or {}
        left = float(box.get("Left", 0.0))
        top = float(box.get("Top", 0.0))
        width = float(box.get("Width", 0.0))
        height = float(box.get("Height", 0.0))
        celebrities.append(
            {
                "name": face.get("Name"),
                "confidence": round(float(face.get("MatchConfidence") or 0.0) / 100, 3),
                "bbox": f"{left:.3f},{top:.3f},{left + width:.3f},{top + height:.3f}",
            }
        )
    return {
        "provider": "aws_rekognition",
        "celebrities": celebrities,
        "unrecognized_faces": len(payload.get("UnrecognizedFaces") or []),
    }


def _live_recognize(face_bytes: bytes) -> dict:
    if not (settings.aws_access_key_id and settings.aws_secret_access_key):
        raise base.ToolUnavailable("未配置 AWS 凭证")
    body = json.dumps(
        {"Image": {"Bytes": base64.b64encode(face_bytes).decode()}}
    ).encode("utf-8")
    headers, host = _sigv4_headers(body)
    response = httpx.post(
        f"https://{host}/",
        content=body,
        headers=headers,
        timeout=settings.timeout_celebrity,
    )
    response.raise_for_status()
    return normalize_response(response.json())


def recognize(
    runtime: base.ToolRuntime,
    frame_path: str | Path,
    *,
    asset_sha256: str,
    scene_index: int,
    timestamp: float,
) -> base.ToolResult:
    path = Path(frame_path)
    mode = base.resolve_mode("celebrity")
    faces = detect_faces(path) if mode == "live" else []

    def run() -> dict | None:
        if mode == "mock":
            return base.mock_entry("celebrity", asset_sha256, scene_index)
        if not faces:
            return None
        return _live_recognize(crop_face_bytes(path, faces[0]))

    return runtime.call(
        tool="celebrity",
        input_hash=f"{asset_sha256}:{scene_index}:{timestamp:.2f}",
        fn=run,
        cost_usd=COST_PER_IMAGE_USD,
        mode=mode,
        timeout=settings.timeout_celebrity + 2,
    )
