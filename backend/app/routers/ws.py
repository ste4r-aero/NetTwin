"""WebSocket-эндпоинты: /ws/interfaces (срез каждые 2 с) и /ws/events (радар)."""
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..services.bus import hub_events, hub_ifaces
from ..runtime import runtime

router = APIRouter()


@router.websocket("/ws/interfaces")
async def ws_interfaces(ws: WebSocket):
    await ws.accept()
    if runtime.last_snapshot:
        try:
            await ws.send_text(json.dumps(runtime.last_snapshot, ensure_ascii=False, default=str))
        except Exception:
            pass
    hub_ifaces.clients.add(ws)
    try:
        while True:
            await ws.receive_text()  # держим соединение; входящие сообщения игнорируем
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub_ifaces.clients.discard(ws)


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    hub_events.clients.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "hello"}, ensure_ascii=False))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub_events.clients.discard(ws)
