"""Слой SQLite. Один коннект + глобальный локер (потокобезопасно)."""
import os
import sqlite3
import threading

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

TABLE_COLS = {
    "hosts": ["id", "mac", "ip", "hostname", "vendor", "device_type", "cpu", "ram",
              "model", "serial", "os", "username", "last_iface", "note",
              "first_seen", "last_seen", "source"],
    "map_nodes": ["id", "host_mac", "label", "kind", "x", "y", "color", "room_id",
                  "auto_label", "created_at"],
    "map_edges": ["id", "src", "dst", "label", "color"],
    "rooms": ["id", "name", "x", "y", "w", "h", "color"],
    "measurements": ["id", "ts", "iface", "speed", "duplex", "ip", "gw", "dns", "note"],
    "lldp_neighbors": ["id", "iface", "chassis", "port", "sysname", "sysdesc",
                       "portdesc", "mgmt", "proto", "last_seen"],
    "sessions": ["id", "label", "iface", "node_id", "started", "ends_at", "active", "found_count"],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS hosts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mac TEXT UNIQUE NOT NULL,
  ip TEXT DEFAULT '',
  hostname TEXT DEFAULT '',
  vendor TEXT DEFAULT '',
  device_type TEXT DEFAULT 'unknown',
  cpu TEXT DEFAULT '',
  ram TEXT DEFAULT '',
  model TEXT DEFAULT '',
  serial TEXT DEFAULT '',
  os TEXT DEFAULT '',
  username TEXT DEFAULT '',
  last_iface TEXT DEFAULT '',
  note TEXT DEFAULT '',
  first_seen REAL DEFAULT 0,
  last_seen REAL DEFAULT 0,
  source TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS map_nodes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  host_mac TEXT DEFAULT '',
  label TEXT NOT NULL,
  kind TEXT DEFAULT 'unknown',
  x REAL DEFAULT 0,
  y REAL DEFAULT 0,
  color TEXT DEFAULT '',
  room_id INTEGER DEFAULT 0,
  auto_label INTEGER DEFAULT 1,
  created_at REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS map_edges(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  src INTEGER NOT NULL,
  dst INTEGER NOT NULL,
  label TEXT DEFAULT '',
  color TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rooms(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT DEFAULT 'Комната',
  x REAL DEFAULT 0,
  y REAL DEFAULT 0,
  w REAL DEFAULT 460,
  h REAL DEFAULT 340,
  color TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS measurements(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL DEFAULT 0,
  iface TEXT DEFAULT '',
  speed TEXT DEFAULT '',
  duplex TEXT DEFAULT '',
  ip TEXT DEFAULT '',
  gw TEXT DEFAULT '',
  dns TEXT DEFAULT '',
  note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS lldp_neighbors(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  iface TEXT DEFAULT '',
  chassis TEXT DEFAULT '',
  port TEXT DEFAULT '',
  sysname TEXT DEFAULT '',
  sysdesc TEXT DEFAULT '',
  portdesc TEXT DEFAULT '',
  mgmt TEXT DEFAULT '',
  proto TEXT DEFAULT 'lldp',
  last_seen REAL DEFAULT 0,
  UNIQUE(iface, chassis, port)
);
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT DEFAULT '',
  iface TEXT DEFAULT '',
  node_id INTEGER DEFAULT 0,
  started REAL DEFAULT 0,
  ends_at REAL DEFAULT 0,
  active INTEGER DEFAULT 1,
  found_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_hosts_last_seen ON hosts(last_seen);
CREATE INDEX IF NOT EXISTS idx_nodes_mac ON map_nodes(host_mac);
CREATE INDEX IF NOT EXISTS idx_edges_src ON map_edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON map_edges(dst);
"""


def init() -> None:
    """Открыть базу и создать схему (идемпотентно)."""
    global _conn
    with _lock:
        if _conn is not None:
            return
        os.makedirs(config.DATA_DIR, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=15)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.executescript(SCHEMA)
        _conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version','1') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        _conn.commit()


def q(sql: str, params=()) -> list[dict]:
    """SELECT -> список словарей."""
    with _lock:
        cur = _conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def q1(sql: str, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None


def x(sql: str, params=()) -> int:
    """INSERT/UPDATE/DELETE + commit, возвращает lastrowid."""
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur.lastrowid


def insert_row(table: str, row: dict) -> int:
    """Вставка словаря. Отсутствующие колонки получают DEFAULT из схемы;
    колонка id (AUTOINCREMENT) всегда пропускается."""
    cols = [c for c in TABLE_COLS[table] if c != "id" and c in row]
    if not cols:
        raise ValueError("insert_row: нет известных колонок для %s" % table)
    vals = [row[c] for c in cols]
    sql = "INSERT INTO {t}({cols}) VALUES({ph})".format(
        t=table, cols=",".join(cols), ph=",".join(["?"] * len(cols))
    )
    return x(sql, vals)


def wipe_tables(tables: list[str]) -> None:
    with _lock:
        for t in tables:
            _conn.execute(f"DELETE FROM {t}")
            try:
                _conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (t,))
            except sqlite3.OperationalError:
                pass  # sqlite_sequence ещё не создан — не страшно
        _conn.commit()
