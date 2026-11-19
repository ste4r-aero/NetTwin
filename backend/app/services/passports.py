"""Импорт паспортов с агентских скриптов (Windows .bat / Linux .sh / JSON-файл)."""
import logging
import time

from fastapi import HTTPException

from .. import db
from .bus import hub_events
from .oui import lookup_oui
from .sniffer import normalize_mac

log = logging.getLogger("nettwin.passports")


def ingest_payload(data: dict) -> dict:
    """Общий путь: агент (или ручной JSON) -> таблица hosts."""
    macs = []
    for m in data.get("macs", []) or []:
        mac = normalize_mac(m.get("mac", "") if isinstance(m, dict) else str(m))
        if mac and mac not in macs:
            macs.append(mac)
    if not macs:
        raise HTTPException(status_code=400,
                            detail="В данных нет ни одного корректного MAC-адреса")

    ram = data.get("ram", "")
    if not ram and data.get("ram_gb") not in (None, ""):
        try:
            ram = f"{round(float(data['ram_gb']), 1)} ГБ"
        except (TypeError, ValueError):
            ram = str(data.get("ram_gb"))

    common = {
        "hostname": (data.get("hostname") or "").strip(),
        "model": (data.get("model") or "").strip(),
        "serial": (data.get("serial") or "").strip(),
        "cpu": (data.get("cpu") or "").strip(),
        "ram": ram,
        "os": (data.get("os") or "").strip(),
        "username": (data.get("username") or "").strip(),
    }
    source = f"agent:{data.get('agent', 'manual')}"
    first_id = None
    now = time.time()

    for mac in macs:
        row = db.q1("SELECT * FROM hosts WHERE mac=?", (mac,))
        if row is None:
            vendor, vtype = lookup_oui(mac)
            new_id = db.x(
                "INSERT INTO hosts(mac,ip,hostname,vendor,device_type,cpu,ram,model,serial,"
                "os,username,first_seen,last_seen,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mac, data.get("ip", ""), common["hostname"], vendor,
                 "pc" if vtype in ("unknown", "") else vtype,
                 common["cpu"], common["ram"], common["model"], common["serial"],
                 common["os"], common["username"], now, now, source))
            if first_id is None:
                first_id = new_id
        else:
            if first_id is None:
                first_id = row["id"]
            dtype = row["device_type"]
            if dtype in ("unknown", ""):
                dtype = "pc"
            db.x(
                "UPDATE hosts SET hostname=?, cpu=?, ram=?, model=?, serial=?, os=?, "
                "username=?, last_seen=?, device_type=?, source=? WHERE id=?",
                (common["hostname"] or row["hostname"], common["cpu"] or row["cpu"],
                 common["ram"] or row["ram"], common["model"] or row["model"],
                 common["serial"] or row["serial"], common["os"] or row["os"],
                 common["username"] or row["username"], now, dtype,
                 source if not row["source"].startswith("agent") else row["source"], row["id"]),
            )

    host = db.q1("SELECT * FROM hosts WHERE id=?", (first_id,))
    if host:
        hub_events.publish({"type": "discovery", "iface": "agent", "host": host})
    log.info("Импорт паспорта: %s (%d MAC)", common["hostname"] or macs[0], len(macs))
    return {"ok": True, "host_id": first_id, "macs": len(macs)}
