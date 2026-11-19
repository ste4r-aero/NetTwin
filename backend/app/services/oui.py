"""Встроенная (офлайн) база OUI: производитель и тип устройства по первым 3 октетам MAC.

База компактная и покрывает типовой корпоративный зоопарк (принтеры, ПК,
сетевое оборудование, виртуалки). Её можно расширить, положив в /data/oui_extra.txt
официальный файл IEEE oui.txt или свой список (формат — см. load_extra).
"""
import logging
import re

log = logging.getLogger("nettwin.oui")

# prefix -> (производитель, тип)
OUI_DB: dict[str, tuple[str, str]] = {
    # ---- Сетевое оборудование ----
    "00000c": ("Cisco", "network"), "001b54": ("Cisco", "network"),
    "002699": ("Cisco", "network"), "001e14": ("Cisco", "network"),
    "0022bd": ("Cisco", "network"), "002545": ("Cisco", "network"),
    "f866f2": ("Cisco", "network"), "001795": ("Cisco", "network"),
    "000c42": ("MikroTik (RouterBOARD)", "network"), "2cc81b": ("MikroTik", "network"),
    "488f5a": ("MikroTik", "network"), "744d28": ("MikroTik", "network"),
    "50c7bf": ("TP-Link", "network"), "14cc20": ("TP-Link", "network"),
    "98dac4": ("TP-Link", "network"),
    "00055d": ("D-Link", "network"), "001e58": ("D-Link", "network"),
    "14d64d": ("D-Link", "network"),
    "5cf4ab": ("ZyXEL", "network"),
    "0418d6": ("Ubiquiti", "network"), "24a43c": ("Ubiquiti", "network"),
    "68d712": ("Ubiquiti", "network"), "f09fc2": ("Ubiquiti", "network"),
    "74acb9": ("Ubiquiti", "network"),
    "000585": ("Juniper", "network"), "28c0da": ("Juniper", "network"),
    "000b86": ("Aruba (HPE)", "network"),
    "00095b": ("Netgear", "network"),
    "000496": ("Extreme Networks", "network"),
    "001882": ("Huawei", "network"), "286ed4": ("Huawei", "network"),
    # ---- Принтеры и МФУ ----
    "001e8f": ("Canon", "printer"), "000e08": ("Canon", "printer"),
    "001777": ("Kyocera", "printer"),
    "001b25": ("Epson", "printer"), "ac1826": ("Epson", "printer"),
    "001ba9": ("Brother", "printer"), "30055c": ("Brother", "printer"),
    "0000aa": ("Xerox", "printer"),
    "001632": ("Samsung (принтеры)", "printer"),
    # ---- ПК, материнки, ноутбуки, серверы ----
    "001aa1": ("HP", "pc"), "001e0b": ("HP", "pc"), "001f29": ("HP", "pc"),
    "001871": ("HP", "pc"), "3cd92b": ("HP Inc.", "pc"),
    "001422": ("Dell", "pc"), "f8db88": ("Dell", "pc"),
    "180373": ("Dell", "pc"), "a4badb": ("Dell", "pc"),
    "60d9c7": ("Lenovo", "pc"),
    "08606e": ("ASUSTek", "pc"), "04d4c4": ("ASUSTek", "pc"),
    "10bf48": ("ASUSTek", "pc"), "ac220b": ("ASUSTek", "pc"), "002354": ("ASUSTek", "pc"),
    "000fea": ("Gigabyte", "pc"),
    "d8bbc1": ("MSI (Micro-Star)", "pc"), "00016c": ("MSI (Micro-Star)", "pc"),
    "002590": ("Supermicro", "pc"), "0cc47a": ("Supermicro", "pc"),
    "0002b3": ("Intel", "pc"), "a0369f": ("Intel", "pc"), "6805ca": ("Intel", "pc"),
    "88532e": ("Intel", "pc"), "e86a64": ("Intel", "pc"),
    "00e04c": ("Realtek", "pc"),
    "001018": ("Broadcom", "pc"),
    "005043": ("Marvell", "pc"),
    "00037f": ("Atheros", "pc"),
    "000393": ("Apple", "pc"), "acde48": ("Apple", "pc"), "f01898": ("Apple", "pc"),
    "9801a7": ("Apple", "pc"), "a483e7": ("Apple", "pc"),
    "00014a": ("Sony", "pc"),
    "ccc50a": ("Logitech", "pc"),
    # ---- Телефоны, камеры, прочая периферия ----
    "0004f2": ("Polycom", "phone"),
    "4419b6": ("Hikvision", "camera"),
    "00408c": ("Axis (камеры)", "camera"),
    "640980": ("Xiaomi", "iot"),
    # ---- Одноплатники ----
    "b827eb": ("Raspberry Pi", "pc"), "dca632": ("Raspberry Pi", "pc"),
    "e45f01": ("Raspberry Pi", "pc"),
    # ---- Виртуальные машины (полезно исключать из «живых») ----
    "005056": ("VMware (виртуальная)", "vm"), "000c29": ("VMware (виртуальная)", "vm"),
    "000569": ("VMware (виртуальная)", "vm"),
    "080027": ("VirtualBox (виртуальная)", "vm"),
    "525400": ("QEMU/KVM (виртуальная)", "vm"),
    "00155d": ("Microsoft Hyper-V (виртуальная)", "vm"),
    "00163e": ("Xen (виртуальная)", "vm"),
}

_IEEE_RE = re.compile(r"^\s*([0-9A-Fa-f]{2})-([0-9A-Fa-f]{2})-([0-9A-Fa-f]{2})\s+\(hex\)\s+(.+?)\s*$")
_PIPE_RE = re.compile(r"^([0-9A-Fa-f]{2}[:\s-]?[0-9A-Fa-f]{2}[:\s-]?[0-9A-Fa-f]{2})\s*\|\s*([^|]+?)\s*(?:\|\s*(\S+)\s*)?$")
_WS_RE = re.compile(r"^([0-9A-Fa-f]{2}[:\s-]?[0-9A-Fa-f]{2}[:\s-]?[0-9A-Fa-f]{2})\s{2,}(\S.*?)\s*$")

_TYPE_KEYWORDS = [
    (("printer", "laser", "mfp", "kyocera", "canon", "epson", "brother", "ricoh",
      "xerox", "sharp", "zebra", "hp laser", "samsung printer"), "printer"),
    (("cisco", "mikrotik", "tp-link", "d-link", "zyxel", "juniper", "aruba", "extreme",
      "netgear", "ubiquiti", "routerboard", "huawei", "h3c", "enterasys", "moxa",
      "planet", "tenda", "switch", "router"), "network"),
    (("vmware", "virtualbox", "qemu", "xen", "parallels", "hyper-v"), "vm"),
    (("polycom", "yealink", "fanvil", "grandstream", "snom"), "phone"),
    (("axis", "hikvision", "dahua", "bosch", "uniview"), "camera"),
    (("apple", "ipad", "iphone", "macbook"), "pc"),
    (("raspberry", "arduino", "espressif", "esp32"), "iot"),
]


def _guess_type(vendor: str) -> str:
    v = vendor.lower()
    for keys, cat in _TYPE_KEYWORDS:
        if any(k in v for k in keys):
            return cat
    return "unknown"


def lookup_oui(mac: str) -> tuple[str, str]:
    """Вернуть (производитель, тип) по MAC. Неизвестно -> ("", "unknown")."""
    prefix = re.sub(r"[^0-9A-Fa-f]", "", mac)[:6].lower()
    hit = OUI_DB.get(prefix)
    if hit:
        return hit
    return ("", "unknown")


def load_extra(path: str) -> int:
    """Загрузить /data/oui_extra.txt.

    Поддерживаемые форматы строк:
      1) официальный IEEE oui.txt:   "00-1B-A1  (hex)  HEWLETT-PACKARD"
      2) pipes:                      "00:1B:A1|HP|pc"
      3) пробелы:                    "00:1B:A1  HEWLETT-PACKARD"
    """
    loaded = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                prefix, vendor, cat = "", "", ""
                m = _IEEE_RE.match(line)
                if m:
                    prefix = "".join(m.group(1, 2, 3)).lower()
                    vendor, cat = m.group(4).strip(), ""
                else:
                    m = _PIPE_RE.match(line)
                    if m:
                        prefix = re.sub(r"[^0-9A-Fa-f]", "", m.group(1))[:6].lower()
                        vendor, cat = m.group(2), (m.group(3) or "").lower()
                    else:
                        m = _WS_RE.match(line)
                        if m:
                            prefix = re.sub(r"[^0-9A-Fa-f]", "", m.group(1))[:6].lower()
                            vendor, cat = m.group(2), ""
                if prefix and vendor:
                    OUI_DB[prefix] = (vendor, cat if cat in
                                      {"pc", "printer", "network", "ap", "camera",
                                       "phone", "vm", "iot", "unknown"} else _guess_type(vendor))
                    loaded += 1
        if loaded:
            log.info("Загружено дополнительных OUI-записей: %d", loaded)
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("Не удалось прочитать %s", path)
    return loaded
