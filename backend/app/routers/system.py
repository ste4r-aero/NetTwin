"""Служебные эндпоинты: здоровье и статистика."""
import os
import time

from fastapi import APIRouter

from .. import config, db
from ..runtime import runtime

router = APIRouter()
_started = time.time()


@router.get("/health")
def health():
    return {
        "status": "ok",
        "version": config.VERSION,
        "sniffer_active": bool(runtime.sniffer.sniffers),
        "packets": runtime.sniffer.packets_total,
    }


@router.get("/stats")
def stats():
    def one(sql, params=()):
        row = db.q1(sql, params)
        return row["c"] if row else 0

    return {
        "hosts_total": one("SELECT COUNT(*) AS c FROM hosts"),
        "with_passport": one("SELECT COUNT(*) AS c FROM hosts WHERE serial!='' OR model!='' OR cpu!=''"),
        "printers": one("SELECT COUNT(*) AS c FROM hosts WHERE device_type='printer'"),
        "pcs": one("SELECT COUNT(*) AS c FROM hosts WHERE device_type='pc'"),
        "network_devices": one("SELECT COUNT(*) AS c FROM hosts WHERE device_type='network'"),
        "map_nodes": one("SELECT COUNT(*) AS c FROM map_nodes"),
        "map_edges": one("SELECT COUNT(*) AS c FROM map_edges"),
        "rooms": one("SELECT COUNT(*) AS c FROM rooms"),
        "sessions_total": one("SELECT COUNT(*) AS c FROM sessions"),
        "measurements": one("SELECT COUNT(*) AS c FROM measurements"),
        "packets": runtime.sniffer.packets_total,
        "sniffer_active": bool(runtime.sniffer.sniffers),
        "db_size_bytes": os.path.getsize(config.DB_PATH) if os.path.exists(config.DB_PATH) else 0,
        "uptime_s": int(time.time() - _started),
    }
