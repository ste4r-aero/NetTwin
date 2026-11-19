"""Ручной (не зависящий от сторонних библиотек) разбор LLDP и CDP.

LLDP — простой TLV-формат: 2-байтовый заголовок (7 бит тип + 9 бит длина).
CDP — кадр 802.3/LLC/SNAP на адрес 01:00:0c:cc:cc:cc, далее TLV (type/len по 2 байта).
"""


def _strip_subtype(val: bytes) -> tuple[int, bytes]:
    if not val:
        return 0, b""
    return val[0], val[1:]


def _chassis_value(subtype: int, val: bytes) -> tuple[str, bool]:
    """(значение, похоже-ли-на-MAC)."""
    if subtype == 4 and len(val) == 6:  # MAC-адрес
        return ":".join(f"{b:02x}" for b in val), True
    if subtype == 7 and len(val) == 7:  # локально назначенный (обычно MAC + тип)
        return ":".join(f"{b:02x}" for b in val[1:]), True
    try:
        text = val.decode("utf-8").strip()
        if text:
            return text, False
    except Exception:
        pass
    return ":".join(f"{b:02x}" for b in val), False


def _port_value(subtype: int, val: bytes) -> str:
    if subtype in (3, 6) and len(val) == 7:  # MAC / локальный
        return ":".join(f"{b:02x}" for b in val[1:])
    if subtype == 4 and len(val) == 6:  # MAC
        return ":".join(f"{b:02x}" for b in val)
    try:
        text = val.decode("utf-8").strip()
        if text:
            return text
    except Exception:
        pass
    return ":".join(f"{b:02x}" for b in val)


def parse_lldp(data: bytes) -> dict | None:
    """Разобрать полезную нагрузку Ethernet-кадра с ethertype 0x88cc."""
    out: dict = {}
    i = 0
    try:
        while i + 2 <= len(data):
            hdr = (data[i] << 8) | data[i + 1]
            t = (hdr >> 9) & 0x7F
            ln = hdr & 0x1FF
            i += 2
            val = data[i:i + ln]
            i += ln
            if t == 0:  # End of LLDPDU
                break
            if t == 1 and val:  # Chassis ID
                st, v = _strip_subtype(val)
                text, is_mac = _chassis_value(st, v)
                out["chassis"] = text
                out["chassis_is_mac"] = is_mac
            elif t == 2 and val:  # Port ID
                st, v = _strip_subtype(val)
                out["port"] = _port_value(st, v)
            elif t == 4 and val:  # Port Description
                out["portdesc"] = val.decode("utf-8", "replace").strip()
            elif t == 5 and val:  # System Name
                out["sysname"] = val.decode("utf-8", "replace").strip()
            elif t == 6 and val:  # System Description
                out["sysdesc"] = val.decode("utf-8", "replace").strip()
            elif t == 7 and len(val) >= 4:  # System Capabilities (доступные/включённые)
                out["caps"] = (val[0] << 8) | val[1]
                out["caps_enabled"] = (val[2] << 8) | val[3]
            elif t == 8 and len(val) >= 2:  # Management Address
                alen = val[0]
                addr = val[1:1 + alen]
                if len(addr) >= 5 and addr[0] == 1:  # subtype 1 = IPv4
                    try:
                        import socket
                        out["mgmt"] = socket.inet_ntoa(addr[1:5])
                    except Exception:
                        pass
            if i > len(data):
                break
    except Exception:
        return out or None
    if not out:
        return None
    # Определяем тип устройства по битам capabilities:
    # 0x2000 Bridge, 0x1000 WLAN AP, 0x0800 Router, 0x0400 Telephone
    caps = out.get("caps", 0)
    if caps & (0x2000 | 0x1000 | 0x0800):
        out["kind"] = "network"
    elif caps & 0x0400:
        out["kind"] = "phone"
    return out


def is_cdp_payload(payload: bytes) -> bool:
    """CDP: LLC AA AA 03 + SNAP OUI 00-00-0C + PID 2000."""
    return (
        len(payload) >= 8
        and payload[0:3] == b"\xaa\xaa\x03"
        and payload[3:6] == b"\x00\x00\x0c"
        and payload[6:8] == b"\x20\x00"
    )


def parse_cdp(payload: bytes) -> dict | None:
    """Разобрать CDP (полезная нагрузка Ethernet-кадра целиком, с LLC-заголовком)."""
    if not is_cdp_payload(payload):
        return None
    out: dict = {"chassis": "", "chassis_is_mac": False}
    data = payload[8:]
    i = 0
    while i + 4 <= len(data):
        t = (data[i] << 8) | data[i + 1]
        ln = (data[i + 2] << 8) | data[i + 3]
        if ln < 4:
            break
        val = data[i + 4:i + ln]
        i += ln
        if t == 0x0001:  # Device ID
            out["chassis"] = val.decode("utf-8", "replace").strip()
        elif t == 0x0003:  # Port ID
            out["port"] = val.decode("utf-8", "replace").strip()
        elif t == 0x0005:  # Software Version
            out["sysdesc"] = val.decode("utf-8", "replace").strip()
        elif t == 0x0006:  # Platform
            out["platform"] = val.decode("utf-8", "replace").strip()
    if not out.get("chassis") and not out.get("port"):
        return None
    out["kind"] = "network"
    return out
