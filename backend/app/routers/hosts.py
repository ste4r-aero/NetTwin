"""Таблица устройств (паспорта + найденные радаром) и экспорт CSV."""
import csv
import io
import time

from fastapi import APIRouter, HTTPException, Response

from .. import db
from ..models import HostPatch
from ..services.bus import hub_events

router = APIRouter()


@router.get("/hosts")
def list_hosts(q: str = "", type: str = ""):
    rows = db.q("SELECT * FROM hosts ORDER BY last_seen DESC")
    ql = (q or "").lower()
    out = []
    for r in rows:
        if ql and ql not in " ".join([
                r["hostname"], r["ip"], r["mac"], r["vendor"], r["model"],
                r["serial"], r["os"], r["note"]]).lower():
            continue
        if type and r["device_type"] != type:
            continue
        out.append(r)
    return {"hosts": out, "total": len(out)}


@router.patch("/hosts/{host_id}")
def patch_host(host_id: int, patch: HostPatch):
    row = db.q1("SELECT * FROM hosts WHERE id=?", (host_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Устройство не найдено")
    updates, params = [], []
    for field in ("hostname", "device_type", "note", "ip"):
        val = getattr(patch, field)
        if val is not None:
            updates.append(f"{field}=?")
            params.append(val)
    if updates:
        params.append(host_id)
        db.x(f"UPDATE hosts SET {', '.join(updates)} WHERE id=?", params)
    # синхронизируем подпись узла карты, если она автоматическая
    node = db.q1("SELECT * FROM map_nodes WHERE host_mac=? AND auto_label=1", (row["mac"],))
    if node:
        fresh = db.q1("SELECT * FROM hosts WHERE id=?", (host_id,))
        label = (fresh["hostname"] or fresh["ip"] or node["label"])[:64]
        db.x("UPDATE map_nodes SET label=? WHERE id=?", (label, node["id"]))
    return {"ok": True}


@router.delete("/hosts/{host_id}")
def delete_host(host_id: int):
    row = db.q1("SELECT * FROM hosts WHERE id=?", (host_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Устройство не найдено")
    node = db.q1("SELECT id FROM map_nodes WHERE host_mac=?", (row["mac"],))
    if node:
        db.x("DELETE FROM map_edges WHERE src=? OR dst=?", (node["id"], node["id"]))
        db.x("DELETE FROM map_nodes WHERE id=?", (node["id"],))
    db.x("DELETE FROM hosts WHERE id=?", (host_id,))
    return {"ok": True}


@router.get("/export/hosts.csv")
def export_csv():
    rows = db.q("SELECT * FROM hosts ORDER BY last_seen DESC")

    def ts(v):
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(v or 0)))
        except (TypeError, ValueError):
            return ""

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Имя", "IP", "MAC", "Тип", "Производитель", "Модель", "CPU",
                "RAM", "ОС", "Серийный номер", "Пользователь", "Заметка",
                "Впервые замечен", "Последняя активность", "Источник"])
    for r in rows:
        w.writerow([r["hostname"], r["ip"], r["mac"], r["device_type"], r["vendor"],
                    r["model"], r["cpu"], r["ram"], r["os"], r["serial"], r["username"],
                    r["note"], ts(r["first_seen"]), ts(r["last_seen"]), r["source"]])
    return Response(
        "\ufeff" + buf.getvalue(),  # BOM — чтобы Excel сразу открыл кириллицу
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=nettwin_hosts.csv"},
    )
