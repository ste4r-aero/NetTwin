"""Сессии «Я сейчас у точки / свитча» — пассивное прослушивание комнаты."""
from fastapi import APIRouter

from .. import db
from ..models import SessionStart
from ..runtime import runtime

router = APIRouter()


@router.post("/sessions/start")
def start_session(body: SessionStart):
    session = runtime.sniffer.start_session(body.label, body.iface, body.duration_sec)
    return {"session": session}


@router.post("/sessions/stop")
def stop_session():
    result = runtime.sniffer.stop_session(broadcast=True)
    return {"session": result}


@router.get("/sessions/active")
def active_session():
    return {"session": runtime.sniffer.session}
