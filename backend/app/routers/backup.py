"""Экспорт/импорт всей карты: устройства, узлы, связи, комнаты, замеры, LLDP.

GET  /api/backup/export            -> файл nettwin_backup_ГГГГММДД_ЧЧММ.json
POST /api/backup/import?mode=merge (или replace) <- JSON-тело или multipart-файл.
Также принимает JSON, сохранённый агентом офлайн (в нём есть ключ "macs").
"""
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from .. import db
from ..services import passports
from ..services.bus import hub_events

router = APIRouter()

BACKUP_TABLES = ["hosts", "map_nodes", "map_edges", "rooms", "measurements", "lldp_neighbors"]


@router.get("/backup/export")
def export_backup():
    payload: dict[str, Any] = {
        "app": "nettwin",
        "format": 1,
        "exported_at": time.time(),
    }
    for table in BACKUP_TABLES:
        payload[table] = db.q(f"SELECT * FROM {table}")
    data = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    fname = time.strftime("nettwin_backup_%Y%m%d_%H%M.json")
    return Response(content=data, media_type="application/json; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


async def _read_payload(request: Request) -> dict:
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None:
            raise HTTPException(status_code=400, detail="Файл не передан")
        raw = await upload.read()
    else:
        raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой запрос")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Некорректный JSON: {e}")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Ожидался JSON-объект")
    return data


def _is_agent_json(data: dict) -> bool:
    return "macs" in data and "hostname" in data and "hosts" not in data


def _import_full(data: dict, mode: str) -> dict:
    counts = {}
    if mode == "replace":
        db.wipe_tables(BACKUP_TABLES)
        for table in BACKUP_TABLES:
            rows = data.get(table, []) or []
            for row in rows:
                db.insert_row(table, row)
            counts[table] = len(rows)
    else:  # merge
        for row in data.get("hosts", []) or []:
            existing = db.q1("SELECT id FROM hosts WHERE mac=?", (row.get("mac", ""),))
            if existing:
                continue
            db.insert_row("hosts", {k: v for k, v in row.items() if k != "id"})
        counts["hosts"] = len(data.get("hosts", []) or [])

        remap: dict[str, int] = {}
        for row in data.get("rooms", []) or []:
            old_id = row.get("id")
            rid = db.insert_row("rooms", {k: v for k, v in row.items() if k != "id"})
            if old_id is not None:
                remap[f"room:{old_id}"] = rid
        counts["rooms"] = len(data.get("rooms", []) or [])

        node_remap: dict[int, int] = {}
        for row in data.get("map_nodes", []) or []:
            old_id = row.get("id")
            new_row = {k: v for k, v in row.items() if k != "id"}
            if row.get("room_id"):
                new_row["room_id"] = remap.get(f"room:{row['room_id']}", 0)
            nid = db.insert_row("map_nodes", new_row)
            if old_id is not None:
                node_remap[old_id] = nid
        counts["map_nodes"] = len(data.get("map_nodes", []) or [])

        edges = 0
        for row in data.get("map_edges", []) or []:
            src = node_remap.get(row.get("src"))
            dst = node_remap.get(row.get("dst"))
            if not src or not dst:
                continue
            db.insert_row("map_edges", {**{k: v for k, v in row.items() if k != "id"},
                                        "src": src, "dst": dst})
            edges += 1
        counts["map_edges"] = edges

        for row in data.get("measurements", []) or []:
            db.insert_row("measurements", {k: v for k, v in row.items() if k != "id"})
        counts["measurements"] = len(data.get("measurements", []) or [])

        for row in data.get("lldp_neighbors", []) or []:
            db.x(
                "INSERT INTO lldp_neighbors(iface,chassis,port,sysname,sysdesc,portdesc,"
                "mgmt,proto,last_seen) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(iface,chassis,port) DO UPDATE SET last_seen=excluded.last_seen",
                (row.get("iface", ""), row.get("chassis", ""), row.get("port", ""),
                 row.get("sysname", ""), row.get("sysdesc", ""), row.get("portdesc", ""),
                 row.get("mgmt", ""), row.get("proto", "lldp"),
                 row.get("last_seen", time.time())))
        counts["lldp_neighbors"] = len(data.get("lldp_neighbors", []) or [])
    return counts


@router.post("/backup/import")
async def import_backup(request: Request, mode: str = "merge"):
    if mode not in ("merge", "replace"):
        raise HTTPException(status_code=400, detail="mode должен быть merge или replace")
    data = await _read_payload(request)

    if _is_agent_json(data):
        result = passports.ingest_payload(data)
        return {"ok": True, "kind": "agent", "result": result}

    counts = _import_full(data, mode)
    hub_events.publish({"type": "map_changed", "full_reload": True})
    return {"ok": True, "kind": "backup", "mode": mode, "counts": counts}
