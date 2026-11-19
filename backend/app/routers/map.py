"""CRUD карты сети: узлы, связи, комнаты, пакетное сохранение позиций."""
import time

from fastapi import APIRouter, HTTPException

from .. import db
from ..models import (EdgeCreate, EdgePatch, NodeCreate, NodePatch,
                      PositionsIn, RoomCreate, RoomPatch)
from ..services.bus import hub_events

router = APIRouter()


def _node_public(node: dict) -> dict:
    out = dict(node)
    if node.get("host_mac"):
        out["host"] = db.q1(
            "SELECT ip,hostname,device_type,vendor,last_seen FROM hosts WHERE mac=?",
            (node["host_mac"],))
    return out


@router.get("/map")
def get_map():
    nodes = [_node_public(n) for n in db.q("SELECT * FROM map_nodes ORDER BY id")]
    edges = db.q("SELECT * FROM map_edges ORDER BY id")
    rooms = db.q("SELECT * FROM rooms ORDER BY id")
    return {"nodes": nodes, "edges": edges, "rooms": rooms}


@router.post("/map/nodes")
def create_node(body: NodeCreate):
    nid = db.x(
        "INSERT INTO map_nodes(host_mac,label,kind,x,y,color,auto_label,created_at) "
        "VALUES(?,?,?,?,?,?,0,?)",
        (body.host_mac, body.label or "Новое устройство", body.kind, body.x, body.y,
         body.color, time.time()))
    node = db.q1("SELECT * FROM map_nodes WHERE id=?", (nid,))
    hub_events.publish({"type": "map_changed", "node": _node_public(node)})
    return node


@router.patch("/map/nodes/{node_id}")
def patch_node(node_id: int, patch: NodePatch):
    node = db.q1("SELECT * FROM map_nodes WHERE id=?", (node_id,))
    if not node:
        raise HTTPException(status_code=404, detail="Узел не найден")
    updates, params = [], []
    for field in ("label", "kind", "color", "x", "y", "room_id", "auto_label"):
        val = getattr(patch, field)
        if val is not None:
            updates.append(f"{field}=?")
            params.append(val)
    if updates:
        params.append(node_id)
        db.x(f"UPDATE map_nodes SET {', '.join(updates)} WHERE id=?", params)
    return db.q1("SELECT * FROM map_nodes WHERE id=?", (node_id,))


@router.delete("/map/nodes/{node_id}")
def delete_node(node_id: int):
    node = db.q1("SELECT * FROM map_nodes WHERE id=?", (node_id,))
    if not node:
        raise HTTPException(status_code=404, detail="Узел не найден")
    db.x("DELETE FROM map_edges WHERE src=? OR dst=?", (node_id, node_id))
    db.x("DELETE FROM map_nodes WHERE id=?", (node_id,))
    return {"ok": True}


@router.post("/map/edges")
def create_edge(body: EdgeCreate):
    if body.src == body.dst:
        raise HTTPException(status_code=400, detail="Нельзя соединить устройство с самим собой")
    a = db.q1("SELECT id FROM map_nodes WHERE id=?", (body.src,))
    b = db.q1("SELECT id FROM map_nodes WHERE id=?", (body.dst,))
    if not a or not b:
        raise HTTPException(status_code=404, detail="Один из узлов не существует")
    dup = db.q1("SELECT id FROM map_edges WHERE (src=? AND dst=?) OR (src=? AND dst=?)",
                (body.src, body.dst, body.dst, body.src))
    if dup:
        return db.q1("SELECT * FROM map_edges WHERE id=?", (dup["id"],))
    eid = db.x("INSERT INTO map_edges(src,dst,label,color) VALUES(?,?,?,?)",
               (body.src, body.dst, body.label, body.color))
    edge = db.q1("SELECT * FROM map_edges WHERE id=?", (eid,))
    hub_events.publish({"type": "map_changed", "edge": edge})
    return edge


@router.patch("/map/edges/{edge_id}")
def patch_edge(edge_id: int, patch: EdgePatch):
    edge = db.q1("SELECT * FROM map_edges WHERE id=?", (edge_id,))
    if not edge:
        raise HTTPException(status_code=404, detail="Связь не найдена")
    updates, params = [], []
    if patch.label is not None:
        updates.append("label=?")
        params.append(patch.label)
    if patch.color is not None:
        updates.append("color=?")
        params.append(patch.color)
    if updates:
        params.append(edge_id)
        db.x(f"UPDATE map_edges SET {', '.join(updates)} WHERE id=?", params)
    return db.q1("SELECT * FROM map_edges WHERE id=?", (edge_id,))


@router.delete("/map/edges/{edge_id}")
def delete_edge(edge_id: int):
    db.x("DELETE FROM map_edges WHERE id=?", (edge_id,))
    return {"ok": True}


@router.post("/map/positions")
def save_positions(body: PositionsIn):
    for n in body.nodes:
        if n.room_id is not None:
            db.x("UPDATE map_nodes SET x=?, y=?, room_id=? WHERE id=?",
                 (n.x, n.y, n.room_id, n.id))
        else:
            db.x("UPDATE map_nodes SET x=?, y=? WHERE id=?", (n.x, n.y, n.id))
    return {"ok": True, "saved": len(body.nodes)}


@router.post("/map/rooms")
def create_room(body: RoomCreate):
    rid = db.x("INSERT INTO rooms(name,x,y,w,h,color) VALUES(?,?,?,?,?,?)",
               (body.name, body.x, body.y, body.w, body.h, body.color))
    return db.q1("SELECT * FROM rooms WHERE id=?", (rid,))


@router.patch("/map/rooms/{room_id}")
def patch_room(room_id: int, patch: RoomPatch):
    room = db.q1("SELECT * FROM rooms WHERE id=?", (room_id,))
    if not room:
        raise HTTPException(status_code=404, detail="Комната не найдена")
    updates, params = [], []
    for field in ("name", "x", "y", "w", "h", "color"):
        val = getattr(patch, field)
        if val is not None:
            updates.append(f"{field}=?")
            params.append(val)
    if updates:
        params.append(room_id)
        db.x(f"UPDATE rooms SET {', '.join(updates)} WHERE id=?", params)
    return db.q1("SELECT * FROM rooms WHERE id=?", (room_id,))


@router.delete("/map/rooms/{room_id}")
def delete_room(room_id: int):
    db.x("UPDATE map_nodes SET room_id=0 WHERE room_id=?", (room_id,))
    db.x("DELETE FROM rooms WHERE id=?", (room_id,))
    return {"ok": True}
