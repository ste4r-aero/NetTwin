"""Пассивный радар NetTwin.

ЖЕЛЕЗНОЕ ПРАВИЛО: модуль ничего не отправляет в сеть. Только слушает.
BPF-фильтр по умолчанию: arp or udp port 5353 or udp port 67 or ether proto 0x88cc
(ARP, mDNS, DHCP, LLDP). Никаких nmap/arp-scan/fping и вообще любых активных запросов.

Что делает модуль:
  * открывает по одному слушающему сокету на каждый проводной интерфейс (en*);
  * разбирает ARP / mDNS / DHCP / LLDP / CDP;
  * ведёт таблицу устройств в SQLite (MAC, IP, имя, производитель, тип);
  * во время «прослушивания точки» автоматически создаёт узлы и связи на карте;
  * публикует события в WebSocket-шину.
"""
import logging
import math
import re
import threading
import time

from scapy.all import ARP, BOOTP, DHCP, DNS, Ether, IP, UDP, AsyncSniffer

from .. import db
from ..config import BPF_FILTER, PROMISC
from . import lldp as lldp_mod
from .bus import hub_events
from .oui import lookup_oui

log = logging.getLogger("nettwin.sniffer")

PRINTER_MDNS = {"_ipp", "_ipps", "_printer", "_pdl-datastream", "_uscan",
                "_uscans", "_scanner", "_fax", "_ipps-companion"}
SERVICE_RE = re.compile(r"_([a-z0-9\-]+)\._(?:udp|tcp)", re.I)


def normalize_mac(raw) -> str | None:
    """Привести MAC любого вида к 'aa:bb:cc:dd:ee:ff'. None для мусора/широковещательных."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw[:6]).hex() if len(raw) >= 6 else bytes(raw).hex()
    h = re.sub(r"[^0-9A-Fa-f]", "", str(raw))
    if len(h) != 12:
        return None
    try:
        if int(h, 16) == 0 or int(h, 16) == 0xFFFFFFFFFFFF:
            return None
    except ValueError:
        return None
    mac = ":".join(h[i:i + 2] for i in range(0, 12, 2)).lower()
    if mac.startswith(("01:00:5e", "33:33")):  # multicast: не устройство
        return None
    return mac


def _ip_str(v) -> str:
    """IPv4 из scapy-поля (str/Net/bytes) -> 'a.b.c.d' или ''."""
    if isinstance(v, (bytes, bytearray)):
        if len(v) == 4:
            return ".".join(str(b) for b in v)
        return ""
    s = str(v) if v else ""
    return "" if s.startswith("0.") else s


def _b2s(v) -> str:
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("utf-8", "replace")
    return str(v)


def _parse_txt(rdata: bytes) -> dict:
    """mDNS TXT-record: последовательность [длина][данные]."""
    out = {}
    i = 0
    data = bytes(rdata)
    while i < len(data):
        ln = data[i]
        i += 1
        chunk = data[i:i + ln]
        i += ln
        s = chunk.decode("utf-8", "replace")
        if "=" in s:
            k, v = s.split("=", 1)
            out[k.lower()] = v
        elif s:
            out[s.lower()] = ""
    return out


class SnifferManager:
    def __init__(self):
        self.sniffers: dict[str, dict] = {}
        self.plain_mode: set[str] = set()  # интерфейсы, где BPF не скомпилировался
        self.lock = threading.Lock()
        self.packets_total = 0
        self.packets_by_iface: dict[str, int] = {}
        self.iface_dns: dict[str, list] = {}
        self.iface_gw: dict[str, str] = {}
        self.session: dict | None = None
        self._last_touch: dict[str, float] = {}
        self._last_evt: dict[str, tuple[tuple, float]] = {}
        self._started_at = time.time()

    # ---------------------------------------------------------------- lifecycle
    def start(self):
        db.x("UPDATE sessions SET active=0")  # после перезапуска сессии считаются закрытыми
        self.reconcile()
        log.info("Пассивный радар запущен. Фильтр: %s", BPF_FILTER)

    def stop(self):
        with self.lock:
            for name in list(self.sniffers):
                self._stop_sniffer(name)

    def reconcile(self):
        """Синхронизировать слушателей с текущим списком интерфейсов (USB можно втыкать на ходу)."""
        try:
            from .netinfo import wired_interface_names
            names = set(wired_interface_names())
        except Exception:
            names = set()
        with self.lock:
            for name in list(self.sniffers):
                if name not in names:
                    self._stop_sniffer(name)
            for name in sorted(names):
                entry = self.sniffers.get(name)
                thread = entry["sniffer"].thread if entry else None
                if entry is None or thread is None or not thread.is_alive():
                    if entry is not None and entry["filter"]:
                        # BPF, вероятно, не скомпилировался — переоткрываем без фильтра
                        log.warning("Сниффер %s упал (вероятно, BPF), перезапуск без фильтра", name)
                        self.plain_mode.add(name)
                    self._start_sniffer(name)

    def _stop_sniffer(self, name: str):
        entry = self.sniffers.pop(name, None)
        if not entry:
            return
        try:
            entry["sniffer"].stop()
        except Exception:
            pass
        log.info("Сниффер %s остановлен", name)

    def _start_sniffer(self, name: str):
        use_filter = name not in self.plain_mode

        def cb(pkt, _if=name):
            self._on_packet(_if, pkt)

        try:
            sniffer = AsyncSniffer(
                iface=name,
                prn=cb,
                store=False,
                promisc=PROMISC,
                filter=BPF_FILTER if use_filter else None,
            )
            sniffer.start()
            self.sniffers[name] = {"sniffer": sniffer, "filter": use_filter}
            log.info("Сниффер %s запущен (BPF=%s, promisc=%s)", name, use_filter, PROMISC)
        except Exception as e:
            if use_filter:
                log.warning("Не удалось запустить %s с BPF (%s), повторим без фильтра", name, e)
                self.plain_mode.add(name)
            else:
                log.error("Не удалось запустить сниффер на %s: %s", name, e)

    # ---------------------------------------------------------------- dispatch
    def _relevant(self, pkt) -> bool:
        """Дополнительная программная фильтрация (когда BPF недоступен)."""
        if ARP in pkt:
            return True
        try:
            u = pkt[UDP]
            if u.sport == 5353 or u.dport == 5353:
                return True
            if u.sport in (67, 68) or u.dport in (67, 68):
                return True
        except Exception:
            pass
        try:
            if int(pkt[Ether].type) == 0x88CC:
                return True
            if str(pkt[Ether].dst).lower().startswith("01:00:0c"):
                return True
        except Exception:
            pass
        return False

    def _on_packet(self, iface: str, pkt):
        self.packets_total += 1
        self.packets_by_iface[iface] = self.packets_by_iface.get(iface, 0) + 1
        if iface in self.plain_mode and not self._relevant(pkt):
            return
        try:
            if ARP in pkt:
                self._handle_arp(iface, pkt)
            elif UDP in pkt:
                u = pkt[UDP]
                if u.sport == 5353 or u.dport == 5353:
                    self._handle_mdns(iface, pkt)
                elif u.sport in (67, 68) or u.dport in (67, 68):
                    self._handle_dhcp(iface, pkt)
            elif Ether in pkt:
                if int(pkt[Ether].type) == 0x88CC:
                    self._handle_lldp(iface, pkt)
                elif str(pkt[Ether].dst).lower().startswith("01:00:0c"):
                    self._handle_cdp(iface, pkt)
        except Exception:
            log.debug("Ошибка разбора пакета", exc_info=True)

    # ---------------------------------------------------------------- ARP
    def _handle_arp(self, iface, pkt):
        a = pkt[ARP]
        mac = normalize_mac(a.hwsrc)
        ip = str(a.psrc)
        if mac and ip and not ip.startswith("0."):
            self.register_host(iface, mac, ip=ip, source="arp")

    # ---------------------------------------------------------------- DHCP
    def _handle_dhcp(self, iface, pkt):
        bootp = pkt[BOOTP]
        mac = normalize_mac(bytes(bootp.chaddr)[:6])
        if mac is None and Ether in pkt:
            mac = normalize_mac(pkt[Ether].src)

        msgtype = 0
        hostname = ""
        vendor = ""
        router = ""
        dns: list = []
        req = ""
        if DHCP in pkt:
            for opt in pkt[DHCP].options or []:
                if not isinstance(opt, tuple):
                    continue
                k, v = opt
                if k == "message-type":
                    msgtype = int(v)
                elif k == "hostname":
                    hostname = _b2s(v)
                elif k == "vendor_class":
                    vendor = _b2s(v[0] if isinstance(v, (list, tuple)) and v else v)
                elif k == "router" and v:
                    router = str(v[0])
                elif k == "name_server" and v:
                    dns = [str(x) for x in list(v)[:2]]
                elif k == "requested_addr":
                    req = str(v)

        yi = _ip_str(bootp.yiaddr)
        ci = _ip_str(bootp.ciaddr)
        src_ip = str(pkt[IP].src) if IP in pkt else ""

        if pkt[UDP].sport == 67:  # от DHCP-сервера (offer/ack) — узнаём шлюз и DNS
            if dns:
                self.iface_dns[iface] = dns
            if router:
                self.iface_gw[iface] = router
            if mac and (yi or msgtype in (2, 5)):
                self.register_host(iface, mac, ip=yi or ci, hostname=hostname,
                                   source="dhcp")
            # сам DHCP-сервер — сетевое устройство
            if IP in pkt and Ether in pkt:
                smac = normalize_mac(pkt[Ether].src)
                if smac and smac != mac:
                    self.register_host(iface, smac, ip=src_ip, source="dhcp-server",
                                       device_type="network")
        else:  # от клиента (discover/request)
            if mac:
                self.register_host(iface, mac, ip=req or ci or "", hostname=hostname,
                                   source="dhcp", extra_note=vendor)

    # ---------------------------------------------------------------- mDNS
    def _mdns_rr(self, rr, names: list, services: set, ctx: dict):
        name = _b2s(getattr(rr, "rrname", "")).rstrip(".")
        m = SERVICE_RE.search(name)
        if m:
            services.add("_" + m.group(1).lower())
        t = 0
        try:
            t = int(getattr(rr, "type", 0))
        except Exception:
            pass
        base = re.sub(r"\.local$", "", name, flags=re.I)
        cand = ""
        if t == 1:  # A
            cand = base
            try:
                ctx.setdefault("ips", []).append(str(rr.rdata))
            except Exception:
                pass
        elif t == 12:  # PTR
            tgt = _b2s(rr.rdata).rstrip(".")
            tgt_base = re.sub(r"\.local$", "", tgt, flags=re.I)
            if "." not in tgt_base and tgt_base and not tgt_base.startswith("_"):
                cand = tgt_base
        elif t == 33:  # SRV: 2 байта prio + 2 байта weight + 2 байта port + target
            raw = bytes(rr.rdata)
            if len(raw) > 6:
                tgt = raw[6:].decode("utf-8", "replace").rstrip(".")
                cand = re.sub(r"\.local$", "", tgt, flags=re.I)
        elif t == 16:  # TXT — модель устройства (у принтеров поле ty=)
            try:
                txt = _parse_txt(bytes(rr.rdata))
                for key in ("ty", "model", "md", "nm", "dn"):
                    if txt.get(key):
                        ctx["model"] = txt[key]
                        break
            except Exception:
                pass
        if cand and not cand.startswith("_") and "._" not in cand and cand.lower() != "localhost":
            if cand not in names:
                names.append(cand)

    def _handle_mdns(self, iface, pkt):
        mac = normalize_mac(pkt[Ether].src) if Ether in pkt else None
        if not mac:
            return
        host_ip = str(pkt[IP].src) if IP in pkt else ""
        names: list[str] = []
        services: set[str] = set()
        ctx: dict = {}
        dns = pkt[DNS]
        try:
            for section in ((dns.qd or []),):
                for qd in section:
                    m = SERVICE_RE.search(_b2s(getattr(qd, "qname", "")))
                    if m:
                        services.add("_" + m.group(1).lower())
        except Exception:
            pass
        for section_name in ("an", "ar", "ns"):
            section = getattr(dns, section_name, None) or []
            for rr in section:
                try:
                    self._mdns_rr(rr, names, services, ctx)
                except Exception:
                    continue
        hostname = names[0] if names else ""
        ips = [ip for ip in ctx.get("ips", []) if ip and not ip.startswith("0.")]
        ip = host_ip if host_ip and not host_ip.startswith("0.") else (ips[0] if ips else "")
        dtype = "printer" if services & PRINTER_MDNS else ""
        self.register_host(iface, mac, ip=ip, hostname=hostname, source="mdns",
                           device_type=dtype, model=ctx.get("model", ""))

    # ---------------------------------------------------------------- LLDP / CDP
    def _store_neighbor(self, iface: str, info: dict, proto: str):
        chassis = info.get("chassis", "")
        port = info.get("port", "")
        if not chassis:
            return
        sysname = info.get("sysname", "") or info.get("platform", "")
        if not sysname and proto == "cdp":
            sysname = chassis  # в CDP Device ID и есть имя устройства
        db.x(
            "INSERT INTO lldp_neighbors(iface,chassis,port,sysname,sysdesc,portdesc,mgmt,proto,last_seen) "
            "VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(iface,chassis,port) DO UPDATE SET "
            "sysname=excluded.sysname, sysdesc=excluded.sysdesc, portdesc=excluded.portdesc, "
            "mgmt=excluded.mgmt, proto=excluded.proto, last_seen=excluded.last_seen",
            (iface, chassis, port, sysname,
             info.get("sysdesc", ""), info.get("portdesc", ""), info.get("mgmt", ""),
             proto, time.time()),
        )
        hub_events.publish({"type": "lldp", "iface": iface, "data": info})

    def _handle_lldp(self, iface, pkt):
        info = lldp_mod.parse_lldp(bytes(pkt[Ether].payload))
        if not info:
            return
        self._store_neighbor(iface, info, "lldp")
        mac = None
        if info.get("chassis_is_mac"):
            mac = normalize_mac(info["chassis"])
        if mac is None and Ether in pkt:
            mac = normalize_mac(pkt[Ether].src)
        if mac:
            self.register_host(iface, mac, ip=info.get("mgmt", ""),
                               hostname=info.get("sysname", ""),
                               source="lldp", device_type=info.get("kind", "network"))

    def _handle_cdp(self, iface, pkt):
        info = lldp_mod.parse_cdp(bytes(pkt[Ether].payload))
        if not info:
            return
        self._store_neighbor(iface, info, "cdp")
        mac = normalize_mac(pkt[Ether].src) if Ether in pkt else None
        if mac:
            self.register_host(iface, mac, hostname=info.get("chassis", ""),
                               source="cdp", device_type="network")

    # ---------------------------------------------------------------- реестр устройств
    def register_host(self, iface: str, mac_raw, ip: str = "", hostname: str = "",
                      source: str = "", device_type: str = "", model: str = "",
                      extra_note: str = "") -> dict | None:
        mac = normalize_mac(mac_raw)
        if not mac:
            return None
        if not ip or ip.startswith("0."):
            ip = ""
        now = time.time()
        vendor, vtype = lookup_oui(mac)

        with self.lock:
            row = db.q1("SELECT * FROM hosts WHERE mac=?", (mac,))
            if row is None:
                dtype = device_type or vtype or "unknown"
                nid = db.x(
                    "INSERT INTO hosts(mac,ip,hostname,vendor,device_type,model,note,"
                    "first_seen,last_seen,last_iface,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (mac, ip, hostname, vendor, dtype, model, extra_note[:120],
                     now, now, iface, source),
                )
                host = {
                    "id": nid, "mac": mac, "ip": ip, "hostname": hostname,
                    "vendor": vendor, "device_type": dtype, "cpu": "", "ram": "",
                    "model": model, "serial": "", "os": "", "username": "",
                    "note": extra_note[:120], "first_seen": now, "last_seen": now,
                    "last_iface": iface, "source": source,
                }
                changed = True
            else:
                host = dict(row)
                dtype = device_type or (row["device_type"] if row["device_type"] != "unknown" else "") or vtype or "unknown"
                new_ip = ip or row["ip"]
                new_host = hostname or row["hostname"]
                new_model = model or row["model"]
                changed = (new_ip != row["ip"] or new_host != row["hostname"]
                           or new_model != row["model"] or dtype != row["device_type"])
                if now - self._last_touch.get(mac, 0) >= 5 or changed:
                    db.x(
                        "UPDATE hosts SET ip=?, hostname=?, device_type=?, model=?, "
                        "last_seen=?, last_iface=?, source=? WHERE id=?",
                        (new_ip, new_host, dtype, new_model, now, iface,
                         source or row["source"], row["id"]),
                    )
                    self._last_touch[mac] = now
                host.update({"ip": new_ip, "hostname": new_host, "device_type": dtype,
                             "model": new_model, "last_seen": now, "last_iface": iface})

        sig = (host["ip"], host["hostname"], host["device_type"], host["model"])
        last = self._last_evt.get(mac)
        if changed or last is None or last[0] != sig or time.time() - last[1] > 60:
            self._last_evt[mac] = (sig, time.time())
            hub_events.publish({"type": "discovery", "iface": iface, "host": host})
        # привязка к свитчу активной сессии — всегда (дубли рёбер исключены в _link_session),
        # иначе устройство, услышанное ранее на другой точке, не привяжется к новому свитчу
        self._link_session(iface, host)
        return host

    # ---------------------------------------------------------------- сессия «Я у точки»
    def _link_session(self, iface: str, host: dict):
        sess = self.session
        if not sess:
            return
        if sess.get("iface") and sess["iface"] != iface:
            return
        if time.time() > sess["ends_at"]:
            return
        sw = db.q1("SELECT * FROM map_nodes WHERE id=?", (sess["node_id"],))
        if not sw:
            return

        node = db.q1("SELECT * FROM map_nodes WHERE host_mac=?", (host["mac"],))
        if node is None:
            cnt = db.q1("SELECT COUNT(*) AS c FROM map_edges WHERE src=? OR dst=?",
                        (sess["node_id"], sess["node_id"]))
            i = cnt["c"] if cnt else 0
            angle = i * 2.399963229728653  # золотой угол — красивый разброс вокруг свитча
            radius = 210 + 35 * (i % 4)
            x = (sw["x"] or 0) + radius * math.cos(angle)
            y = (sw["y"] or 0) + radius * math.sin(angle)
            label = host["hostname"] or host["ip"] or host["mac"][:11]
            kind = host["device_type"] if host["device_type"] != "unknown" else "unknown"
            nid = db.x(
                "INSERT INTO map_nodes(host_mac,label,kind,x,y,auto_label,created_at) "
                "VALUES(?,?,?,?,?,1,?)", (host["mac"], label, kind, x, y, time.time()))
            node = db.q1("SELECT * FROM map_nodes WHERE id=?", (nid,))
            hub_events.publish({"type": "map_changed", "node": _node_public(node)})
        else:
            if node["auto_label"]:
                label = host["hostname"] or host["ip"] or node["label"]
                kind = node["kind"]
                new_kind = kind if kind not in ("", "unknown") else (
                    host["device_type"] if host["device_type"] != "unknown" else "unknown")
                if label != node["label"] or new_kind != kind:
                    db.x("UPDATE map_nodes SET label=?, kind=? WHERE id=?",
                         (label, new_kind, node["id"]))
                    node = db.q1("SELECT * FROM map_nodes WHERE id=?", (node["id"],))
                    hub_events.publish({"type": "map_changed", "node": _node_public(node)})

        edge = db.q1(
            "SELECT id FROM map_edges WHERE (src=? AND dst=?) OR (src=? AND dst=?)",
            (sess["node_id"], node["id"], node["id"], sess["node_id"]))
        if edge is None:
            eid = db.x("INSERT INTO map_edges(src,dst,label,color) VALUES(?,?,?,?)",
                       (sess["node_id"], node["id"], "", ""))
            db.x("UPDATE sessions SET found_count=found_count+1 WHERE id=?", (sess["id"],))
            hub_events.publish({"type": "map_changed", "edge": {
                "id": eid, "src": sess["node_id"], "dst": node["id"], "label": "", "color": ""}})
            if self.session:
                self.session["found_count"] += 1
                hub_events.publish({"type": "session", "action": "progress",
                                    "found": self.session["found_count"]})

    def start_session(self, label: str, iface: str, duration: int = 30) -> dict:
        self.stop_session(broadcast=False)
        label = (label or "").strip()
        if not label and iface:
            lr = db.q1("SELECT * FROM lldp_neighbors WHERE iface=? ORDER BY last_seen DESC",
                       (iface,))
            if lr and lr["sysname"]:
                label = lr["sysname"]
        if not label:
            label = "Точка / свитч"
        last_sw = db.q1("SELECT * FROM map_nodes WHERE kind='switch' ORDER BY id DESC")
        if last_sw:
            x, y = (last_sw["x"] or 0) + 340, (last_sw["y"] or 0)
        else:
            x, y = 0.0, 0.0
        now = time.time()
        nid = db.x(
            "INSERT INTO map_nodes(host_mac,label,kind,x,y,auto_label,created_at) "
            "VALUES('',?,'switch',?,?,0,?)", (label, x, y, now))
        sid = db.x(
            "INSERT INTO sessions(label,iface,node_id,started,ends_at,active,found_count) "
            "VALUES(?,?,?,?,?,1,0)", (label, iface, nid, now, now + duration))
        self.session = {"id": sid, "label": label, "iface": iface, "node_id": nid,
                        "started": now, "ends_at": now + duration, "found_count": 0}
        node = db.q1("SELECT * FROM map_nodes WHERE id=?", (nid,))
        hub_events.publish({"type": "session", "action": "started",
                            "session": self.session, "node": _node_public(node)})
        log.info("Прослушивание точки «%s» (%s, %d c) начато", label, iface or "все интерфейсы", duration)
        return self.session

    def stop_session(self, broadcast: bool = True, finished: bool = False):
        sess = self.session
        if not sess:
            return None
        db.x("UPDATE sessions SET active=0 WHERE id=?", (sess["id"],))
        row = db.q1("SELECT found_count FROM sessions WHERE id=?", (sess["id"],))
        found = row["found_count"] if row else 0
        self.session = None
        result = {"id": sess["id"], "label": sess["label"], "iface": sess["iface"],
                  "node_id": sess["node_id"], "started": sess["started"],
                  "ends_at": sess["ends_at"], "found_count": found}
        if broadcast:
            hub_events.publish({"type": "session", "action": "finished" if finished else "stopped",
                                "session": result, "found": found})
        return result

    def check_expiry(self):
        if self.session and time.time() > self.session["ends_at"]:
            log.info("Прослушивание точки «%s» завершено по таймеру", self.session["label"])
            self.stop_session(broadcast=True, finished=True)

    def stats(self) -> dict:
        return {
            "packets": self.packets_total,
            "by_iface": dict(self.packets_by_iface),
            "interfaces": sorted(self.sniffers.keys()),
            "plain_mode": sorted(self.plain_mode),
            "uptime_s": int(time.time() - self._started_at),
        }


def _node_public(node: dict) -> dict:
    """Узел карты + данные связанного устройства (для WebSocket-событий)."""
    out = dict(node)
    if node.get("host_mac"):
        h = db.q1("SELECT ip,hostname,device_type,vendor,last_seen FROM hosts WHERE mac=?",
                  (node["host_mac"],))
        out["host"] = h
    return out
