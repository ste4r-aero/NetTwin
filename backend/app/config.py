"""Конфигурация NetTwin. Все параметры можно переопределить переменными окружения."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]  # /app в контейнере, backend/ при dev-запуске

VERSION = "1.0.0"

# Каталог статики: в контейнере — /app/static, при запуске без Docker — ../frontend
_STATIC_CANDIDATES = [
    BASE_DIR / "static",
    BASE_DIR.parent / "frontend",
]
STATIC_DIR = next((p for p in _STATIC_CANDIDATES if p.is_dir()), BASE_DIR / "static")

# База данных
_DB_DEFAULT = "/data/nettwin.db" if os.path.isdir("/data") else str(BASE_DIR / "data" / "nettwin.db")
DB_PATH = os.environ.get("NETTWIN_DB", _DB_DEFAULT)
DATA_DIR = os.path.dirname(DB_PATH)

# Пассивный BPF-фильтр (ТОЛЬКО прослушивание, никаких исходящих пакетов).
BPF_FILTER = os.environ.get(
    "NETTWIN_BPF_FILTER",
    "arp or udp port 5353 or udp port 67 or ether proto 0x88cc",
)

# Шаблон имён проводных интерфейсов (USB-Ethernet на Arch: enp0s20u1, enx..., а также eth*)
IFACE_PATTERN = os.environ.get("NETTWIN_IFACE_PATTERN", r"^(en|eth)")

# Неразборчивый режим выкл по умолчанию: broadcast/multicast трафик виден и без него,
# а на сетевой адаптер гарантированно не уходит ни одного пакета.
PROMISC = os.environ.get("NETTWIN_PROMISC", "0") == "1"

# Дополнительный файл OUI (необязательно): /data/oui_extra.txt
OUI_EXTRA = os.path.join(DATA_DIR, "oui_extra.txt")

# Порт
PORT = int(os.environ.get("NETTWIN_PORT", "8080"))
