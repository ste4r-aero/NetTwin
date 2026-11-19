"""Сохранённые замеры кабеля (вкладка «Живой тестер кабеля»)."""
from fastapi import APIRouter, HTTPException

from .. import db
from ..models import MeasurementIn

router = APIRouter()


@router.get("/measurements")
def list_measurements():
    return {"items": db.q("SELECT * FROM measurements ORDER BY id DESC LIMIT 50")}


@router.post("/measurements")
def create_measurement(body: MeasurementIn):
    import time
    mid = db.x(
        "INSERT INTO measurements(ts,iface,speed,duplex,ip,gw,dns,note) VALUES(?,?,?,?,?,?,?,?)",
        (time.time(), body.iface, body.speed, body.duplex, body.ip, body.gw,
         body.dns, body.note))
    return db.q1("SELECT * FROM measurements WHERE id=?", (mid,))


@router.delete("/measurements/{mid}")
def delete_measurement(mid: int):
    db.x("DELETE FROM measurements WHERE id=?", (mid,))
    return {"ok": True}
