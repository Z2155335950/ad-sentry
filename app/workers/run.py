"""独立 worker 进程：docker-compose 里与 app 分开运行（PROMPT §4）。"""

from __future__ import annotations

import asyncio
import logging
import signal

from app.db import init_db
from app.workers.cleanup import cleanup_loop
from app.workers.queue import worker_loop

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    init_db()
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # Windows 不支持
            pass

    logger.info("worker process started")
    try:
        await asyncio.gather(worker_loop(stop), cleanup_loop(stop))
    except asyncio.CancelledError:
        pass
    logger.info("worker process stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
