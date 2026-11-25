"""素材层：类型判定、落盘、去重、TTL。"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from io import BytesIO
from pathlib import Path

from sqlmodel import Session, select

from app.config import settings
from app.models import Asset, Job, MediaType, utcnow

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v", ".avi"}
ASSET_TTL = timedelta(hours=24)


class UnsupportedMedia(ValueError):
    pass


def detect_media_type(filename: str | None, content_type: str | None) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext in IMAGE_EXTS:
        return MediaType.image.value
    if ext in VIDEO_EXTS:
        return MediaType.video.value
    if content_type:
        if content_type.startswith("image/"):
            return MediaType.image.value
        if content_type.startswith("video/"):
            return MediaType.video.value
    raise UnsupportedMedia(f"不支持的媒体类型：{filename or content_type or 'unknown'}")


def _read_image_size(asset: Asset, data: bytes) -> None:
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as img:
            asset.width, asset.height = img.size
    except Exception:  # 尺寸读不出来不影响主流程
        pass


def store_asset(
    data: bytes,
    filename: str | None,
    content_type: str | None,
    session: Session,
) -> tuple[Asset, Job]:
    media_type = detect_media_type(filename, content_type)
    digest = hashlib.sha256(data).hexdigest()
    ext = Path(filename or "").suffix.lower()
    if not ext:
        ext = ".png" if media_type == MediaType.image.value else ".mp4"
    dest = settings.data_dir / "assets" / f"{digest}{ext}"

    asset = session.exec(select(Asset).where(Asset.sha256 == digest)).first()
    if asset is None:
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        asset = Asset(
            sha256=digest,
            media_type=media_type,
            path=str(dest),
            expires_at=utcnow() + ASSET_TTL,
        )
        if media_type == MediaType.image.value:
            _read_image_size(asset, data)
        session.add(asset)
        session.flush()
    else:
        # 记录可能还在但文件已被 TTL 清理，重新落盘并续期
        asset.path = str(dest)
        asset.media_type = media_type
        asset.expires_at = utcnow() + ASSET_TTL
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            if media_type == MediaType.image.value:
                _read_image_size(asset, data)
        session.add(asset)

    job = Job(asset_id=asset.id)
    session.add(job)
    session.commit()
    session.refresh(asset)
    session.refresh(job)
    return asset, job
