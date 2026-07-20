from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from concurrent.futures import Future
from typing import Any


class AsyncRunner:
    """在后台线程维护唯一 asyncio 循环，Tk 主线程只提交工作。"""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._started = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ticket-asyncio", daemon=False)
        self._thread.start()
        self._started.wait(timeout=5)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._started.set()
        self.loop.run_forever()
        self.loop.close()

    def submit(self, coroutine: Coroutine[Any, Any, Any]) -> Future[Any]:
        if not self.loop.is_running():
            coroutine.close()
            raise RuntimeError("后台事件循环未运行")
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def stop(self, timeout: float = 10) -> None:
        if not self.loop.is_running():
            return

        async def cancel_pending() -> None:
            current = asyncio.current_task()
            pending = [task for task in asyncio.all_tasks() if task is not current]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        future = asyncio.run_coroutine_threadsafe(cancel_pending(), self.loop)
        try:
            future.result(timeout=timeout)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self._thread.join(timeout=timeout)
