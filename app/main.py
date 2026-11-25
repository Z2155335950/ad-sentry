"""FastAPI 装配：启动时建表并拉起 worker 循环。"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import admin_ledger, demo, feishu, review, upload
from app.config import settings
from app.db import init_db
from app.workers.cleanup import cleanup_loop
from app.workers.queue import worker_loop


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    stop = asyncio.Event()
    tasks: list[asyncio.Task] = []
    if settings.worker_in_app:
        tasks.append(asyncio.create_task(worker_loop(stop), name="adsentry-worker"))
        tasks.append(asyncio.create_task(cleanup_loop(stop), name="adsentry-cleanup"))
    if (
        settings.feishu_enabled
        and settings.feishu_long_connection
        and (settings.feishu_mode or "").lower() != "mock"
    ):
        from app.feishu import longconn

        longconn.start_in_background()
    try:
        yield
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="素材哨兵 AdSentry", lifespan=lifespan)
app.include_router(upload.router)
app.include_router(review.router)
app.include_router(admin_ledger.router)
app.include_router(feishu.router)
app.include_router(demo.router)
