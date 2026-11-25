"""FastAPI 装配：启动时建表并拉起 worker 循环。"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import review, upload
from app.db import init_db
from app.workers.queue import worker_loop


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    stop = asyncio.Event()
    worker = asyncio.create_task(worker_loop(stop), name="adsentry-worker")
    try:
        yield
    finally:
        stop.set()
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker


app = FastAPI(title="素材哨兵 AdSentry", lifespan=lifespan)
app.include_router(upload.router)
app.include_router(review.router)
