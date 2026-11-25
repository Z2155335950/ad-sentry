"""飞书事件入口（公网回调模式）。

长连接模式不会走这个路由，两种模式共用 `app.feishu.events.handle_event`。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.feishu import events

router = APIRouter()


@router.post("/api/feishu/event")
async def feishu_event(request: Request):
    if not settings.feishu_enabled:
        raise HTTPException(status_code=503, detail="FEISHU_ENABLED=false，飞书入口未启用")

    body = await request.body()
    if not events.verify_signature(request.headers, body):
        raise HTTPException(status_code=403, detail="请求签名无效")

    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法 JSON") from exc

    if payload.get("encrypt"):
        if not settings.feishu_encrypt_key:
            raise HTTPException(
                status_code=400, detail="收到加密事件但未配置 FEISHU_ENCRYPT_KEY"
            )
        payload = events.decrypt_event(payload["encrypt"])

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    if not events.token_ok(payload):
        raise HTTPException(status_code=403, detail="Verification Token 不匹配")

    return events.handle_event(payload)
