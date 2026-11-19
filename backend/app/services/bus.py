"""Шина событий: рассылка JSON-событий всем WebSocket-клиентам.

Фоновые потоки (сниффер, монитор интерфейсов) публикуют события через
hub.publish(...) — метод безопасно переносит вызов в asyncio-цикл uvicorn.
"""
import asyncio
import json
import logging

log = logging.getLogger("nettwin.bus")


class Hub:
    def __init__(self, name: str = "hub"):
        self.name = name
        self.clients: set = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    async def connect(self, ws) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws) -> None:
        self.clients.discard(ws)

    async def _broadcast(self, message: str) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def publish(self, event: dict) -> None:
        """Потокобезопасная публикация события (можно звать из любого потока)."""
        if self.loop is None or not self.clients:
            return
        try:
            data = json.dumps(event, ensure_ascii=False, default=str)
            asyncio.run_coroutine_threadsafe(self._broadcast(data), self.loop)
        except RuntimeError:
            pass  # сервер останавливается — игнорируем


# Снимки интерфейсов (каждые 2 секунды)
hub_ifaces = Hub("ifaces")
# События радара: discovery / session / lldp / map_changed
hub_events = Hub("events")
