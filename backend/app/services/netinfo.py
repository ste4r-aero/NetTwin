"""Сбор информации о сетевых интерфейсах через iproute2 + ethtool + sysfs.

Работает в контейнере с network_mode: host, поэтому видит интерфейсы хоста
(USB-Ethernet адаптеры en*) напрямую. Ничего не отправляет в сеть.
"""
import json
import logging
import os
import re
import subprocess
import time

from .. import config, db

log = logging.getLogger("nettwin.netinfo")

WIRED_RE = re.compile(config.IFACE_PATTERN)
EXCLUDE_PREFIXES = ("lo", "wl", "ww", "docker", "veth", "br", "virbr", "tap",
                    "tun", "zt", "tailscale", "vboxnet", "vmnet", "wg")

# Известные USB-Ethernet чипы (VID:PID) — чтобы пользователь видел, что воткнуто.
USB_MAP = {
    "0bda:8156": "Realtek RTL8156 — USB 2.5GbE",
    "0bda:8153": "Realtek RTL8153 — USB 3.0 Gigabit",
    "0bda:8152": "Realtek RTL8152 — USB Fast/Gigabit",
    "0b95:1790": "ASIX AX88179A — USB 3.0 Gigabit",
    "0b95:178a": "ASIX AX88179 — USB 3.0 Gigabit",
    "0b95:772a": "ASIX AX88772A — USB Fast Ethernet",
    "0b95:7720": "ASIX AX88772 — USB Fast Ethernet",
    "2357:0601": "TP-Link UE300 — USB Gigabit",
}

_prev_counters: dict[str, tuple[float, int, int]] = {}
_resolv_cache: tuple[float, list] = (0.0, [])


def _run(cmd: list[str], timeout: float = 3.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _run_json(cmd: list[str]):
    out = _run(cmd)
    if not out:
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def list_link_interfaces() -> list[str]:
    data = _run_json(["ip", "-j", "link", "show"]) or []
    return [l.get("ifname", "") for l in data if l.get("ifname")]


def wired_interface_names() -> list[str]:
    """Проводные интерфейсы (en*/eth*), исключая виртуальные и Wi-Fi."""
    names = []
    for name in list_link_interfaces():
        if not WIRED_RE.match(name):
            continue
        if name.startswith(EXCLUDE_PREFIXES):
            continue
        names.append(name)
    return names


def ethtool_info(name: str) -> dict:
    """Speed / Duplex / Link detected через ethtool."""
    info = {"speed": "Unknown!", "duplex": "Unknown!", "link": False}
    out = _run(["ethtool", name], timeout=2.0)
    for line in out.splitlines():
        line = line.strip()
        low = line.lower()
        if low.startswith("speed:"):
            info["speed"] = line.split(":", 1)[1].strip()
        elif low.startswith("duplex:"):
            info["duplex"] = line.split(":", 1)[1].strip()
        elif low.startswith("link detected:"):
            info["link"] = "yes" in low
    m = re.match(r"(\d+)", info["speed"])
    info["speed_mbps"] = int(m.group(1)) if m else 0
    return info


def _resolv_servers() -> list[str]:
    global _resolv_cache
    now = time.time()
    if now - _resolv_cache[0] < 30:
        return _resolv_cache[1]
    servers = []
    try:
        with open("/etc/resolv.conf", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "nameserver" and parts[1] != "127.0.0.53":
                    servers.append(parts[1])
                if len(servers) >= 2:
                    break
    except Exception:
        pass
    _resolv_cache = (now, servers)
    return servers


def usb_info(name: str) -> tuple[str, str, str]:
    """(описание чипа, vendor_id, product_id) из sysfs USB-устройства."""
    base = f"/sys/class/net/{name}/device"
    for dev in (base, os.path.realpath(os.path.join(base, ".."))):
        try:
            vid = open(os.path.join(dev, "idVendor")).read().strip().lower()
            pid = open(os.path.join(dev, "idProduct")).read().strip().lower()
            if vid and pid:
                desc = USB_MAP.get(f"{vid}:{pid}", f"USB Ethernet ({vid}:{pid})")
                return desc, vid, pid
        except Exception:
            continue
    return "", "", ""


def _activity(name: str) -> dict:
    """Текущая скорость приёма/передачи (Кбит/с) по счётчикам sysfs."""
    now = time.time()
    try:
        rx = int(open(f"/sys/class/net/{name}/statistics/rx_bytes").read())
        tx = int(open(f"/sys/class/net/{name}/statistics/tx_bytes").read())
    except Exception:
        return {"rx_kbps": 0, "tx_kbps": 0}
    prev = _prev_counters.get(name)
    _prev_counters[name] = (now, rx, tx)
    if prev is None:
        return {"rx_kbps": 0, "tx_kbps": 0}
    dt = max(now - prev[0], 0.001)
    rx_kbps = int(max(0, rx - prev[1]) * 8 / 1000 / dt)
    tx_kbps = int(max(0, tx - prev[2]) * 8 / 1000 / dt)
    return {"rx_kbps": rx_kbps, "tx_kbps": tx_kbps}


def lldp_for_iface(name: str):
    row = db.q1(
        "SELECT * FROM lldp_neighbors WHERE iface=? ORDER BY last_seen DESC LIMIT 1", (name,)
    )
    return row


def get_interfaces_snapshot(dns_cache: dict | None = None,
                            gw_cache: dict | None = None) -> list[dict]:
    """Полный срез по всем проводным интерфейсам: L1 (линк/скорость/дуплекс) + L2/L3."""
    dns_cache = dns_cache or {}
    gw_cache = gw_cache or {}
    links = _run_json(["ip", "-j", "link", "show"]) or []
    addrs = _run_json(["ip", "-j", "addr", "show"]) or []
    routes = _run_json(["ip", "-j", "route", "show", "default"]) or []

    addr_by = {a.get("ifname"): a for a in addrs}
    gw_by: dict[str, str] = {}
    for r in routes:
        if r.get("dev") and r.get("gateway"):
            gw_by.setdefault(r["dev"], r["gateway"])

    wired = set(wired_interface_names())
    result = []
    for l in links:
        name = l.get("ifname", "")
        if name not in wired:
            continue
        a = addr_by.get(name, {})
        et = ethtool_info(name)
        link = et["link"] or l.get("operstate") == "UP"

        info_all = a.get("addr_info", []) or []
        ipv4 = [
            {"addr": i.get("local", ""), "prefix": i.get("prefixlen", 0)}
            for i in info_all
            if i.get("family") == "inet" and i.get("scope") == "global"
        ]
        if not ipv4:
            ipv4 = [
                {"addr": i.get("local", ""), "prefix": i.get("prefixlen", 0)}
                for i in info_all if i.get("family") == "inet"
            ]

        dns = dns_cache.get(name) or _resolv_servers()
        gw = gw_by.get(name) or gw_cache.get(name) or ""
        usb, vid, pid = usb_info(name)
        act = _activity(name)
        lldp_row = lldp_for_iface(name)
        lldp = None
        if lldp_row:
            lldp = {
                "sysname": lldp_row["sysname"], "sysdesc": lldp_row["sysdesc"],
                "port": lldp_row["port"], "portdesc": lldp_row["portdesc"],
                "chassis": lldp_row["chassis"], "mgmt": lldp_row["mgmt"],
                "proto": lldp_row["proto"], "age_sec": int(time.time() - (lldp_row["last_seen"] or 0)),
            }

        result.append({
            "name": name,
            "mac": a.get("address") or l.get("address") or "",
            "operstate": l.get("operstate", ""),
            "link": bool(link),
            "speed": et["speed"] if et["link"] else "",
            "speed_mbps": et["speed_mbps"] if et["link"] else 0,
            "duplex": et["duplex"] if et["link"] else "",
            "ipv4": ipv4,
            "gateway": gw,
            "dns": dns,
            "usb": usb, "vendor_id": vid, "product_id": pid,
            "lldp": lldp,
            "rx_kbps": act["rx_kbps"], "tx_kbps": act["tx_kbps"],
        })
    return result


def primary_lan_ip() -> str:
    """Первый «настоящий» IPv4 хоста (для генерации агентских скриптов)."""
    addrs = _run_json(["ip", "-j", "addr", "show"]) or []
    fallback = ""
    for a in addrs:
        name = a.get("ifname", "")
        if name.startswith(("lo", "docker", "veth", "br", "wg", "tailscale", "virbr")):
            continue
        for i in a.get("addr_info", []) or []:
            ip = i.get("local", "")
            if i.get("family") == "inet" and i.get("scope") == "global" and ip:
                if WIRED_RE.match(name):
                    return ip
                fallback = fallback or ip
    return fallback
