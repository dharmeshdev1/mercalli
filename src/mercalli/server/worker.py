"""An event loop on a thread of its own, for ticks and notification deliveries.

The engine writes to the database synchronously and model hooks do CPU heavy work. On the server's
own loop either would stall every API request and live stream for as long as a tick runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Coroutine
from typing import Any


class WorkerLoop:
    def __init__(self, name: str = "mercalli-worker") -> None:
        self._loop = asyncio.new_event_loop()
        # A daemon thread, so a hung tick can never keep the process from exiting.
        self._thread = threading.Thread(target=self._main, name=name, daemon=True)
        self._thread.start()

    def _main(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            with contextlib.suppress(Exception):
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    async def run[T](self, coro: Coroutine[Any, Any, T], seconds: float) -> T:
        """Run a coroutine on the worker loop. Cancelling or timing out cancels it there as well."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return await asyncio.wait_for(asyncio.wrap_future(future), seconds)

    async def aclose(self, seconds: float = 5.0) -> None:
        if self._loop.is_closed() or not self._thread.is_alive():
            return

        async def drain() -> None:
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        with contextlib.suppress(Exception):
            await self.run(drain(), seconds)
        self._loop.call_soon_threadsafe(self._loop.stop)
        await asyncio.to_thread(self._thread.join, seconds)
