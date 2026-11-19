"""Runtime: фоновые службы (монитор интерфейсов каждые 2 сек + менеджер снифферов)."""
import logging
import threading
import time

from .services import netinfo
from .services.bus import hub_events, hub_ifaces
from .services.sniffer import SnifferManager

log = logging.getLogger("nettwin.runtime")


class Runtime:
    def __init__(self):
        self.sniffer = SnifferManager()
        self.thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_snapshot: dict | None = None
        self._iter = 0

    def start(self):
        self._stop.clear()
        self.sniffer.start()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="nettwin-monitor")
        self.thread.start()
        log.info("Монитор интерфейсов запущен (интервал 2 с)")

    def stop(self):
        self._stop.set()
        self.sniffer.stop()

    def _loop(self):
        while not self._stop.is_set():
            try:
                interfaces = netinfo.get_interfaces_snapshot(
                    dns_cache=self.sniffer.iface_dns,
                    gw_cache=self.sniffer.iface_gw,
                )
                msg = {"type": "interfaces", "ts": time.time(),
                       "interfaces": interfaces, "stats": self.sniffer.stats()}
                self.last_snapshot = msg
                hub_ifaces.publish(msg)
            except Exception:
                log.exception("Ошибка съёма состояния интерфейсов")
            try:
                self.sniffer.check_expiry()
            except Exception:
                log.exception("Ошибка проверки сессии")
            self._iter += 1
            if self._iter % 3 == 0:  # раз в ~6 секунд: новые/пропавшие USB-адаптеры
                try:
                    self.sniffer.reconcile()
                except Exception:
                    log.exception("Ошибка reconcile снифферов")
            self._stop.wait(2.0)


runtime = Runtime()
