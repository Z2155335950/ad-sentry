"""飞书长连接（WebSocket）监听 `im.message.receive_v1`。

把 SDK 事件对象转成 v2.0 JSON 结构，复用 `events.handle_event` 的同一条处理路径，
保证长连接与公网回调行为一致。处理放到独立线程，避免阻塞心跳与重连。
"""

from __future__ import annotations

import logging
import threading

from app.config import settings
from app.feishu import events

logger = logging.getLogger(__name__)


def _attr(obj, name: str, default: str = "") -> str:
    value = getattr(obj, name, None)
    return default if value is None else str(value)


def to_payload(data) -> dict:
    event = getattr(data, "event", None)
    message = getattr(event, "message", None)
    sender_id = getattr(getattr(event, "sender", None), "sender_id", None)
    header = getattr(data, "header", None)
    return {
        "schema": "2.0",
        "header": {
            "event_id": _attr(header, "event_id"),
            "event_type": "im.message.receive_v1",
        },
        "event": {
            "sender": {"sender_id": {"open_id": _attr(sender_id, "open_id")}},
            "message": {
                "message_id": _attr(message, "message_id"),
                "chat_id": _attr(message, "chat_id"),
                "message_type": _attr(message, "message_type"),
                "content": _attr(message, "content"),
            },
        },
    }


def _handle(data) -> None:
    try:
        logger.info("飞书事件处理结果: %s", events.handle_event(to_payload(data)))
    except Exception:
        logger.exception("飞书事件处理失败")


def start() -> None:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

    def on_message(data: P2ImMessageReceiveV1) -> None:
        threading.Thread(target=_handle, args=(data,), daemon=True).start()

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    ws_client = lark.ws.Client(
        settings.feishu_app_id,
        settings.feishu_app_secret,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )
    logger.info("飞书长连接启动（app_id=%s）", settings.feishu_app_id)
    ws_client.start()


def start_in_background() -> threading.Thread:
    thread = threading.Thread(target=start, name="feishu-ws", daemon=True)
    thread.start()
    return thread
