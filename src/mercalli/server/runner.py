"""Run the app under uvicorn with a shutdown order that suits long lived connections."""

from __future__ import annotations

import socket
from collections.abc import Callable

import uvicorn
from fastapi import FastAPI


class _Server(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, on_shutdown: Callable[[], None]) -> None:
        super().__init__(config)
        self._on_shutdown = on_shutdown

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        # uvicorn waits for open connections to close before it runs the app's shutdown, and a live
        # stream never closes by itself. End the streams and stop new ticks first.
        self._on_shutdown()
        await super().shutdown(sockets=sockets)


def run_server(app: FastAPI, *, host: str, port: int, access_log: bool, grace: float) -> None:
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        access_log=access_log,
        log_config=None,  # keep the process's own logging setup
        proxy_headers=True,
        server_header=False,
        lifespan="on",
        timeout_graceful_shutdown=int(grace) + 5,
    )
    _Server(config, app.state.ctx.begin_shutdown).run()
