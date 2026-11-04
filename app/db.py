"""Persistencia local en SQLite (configuración, lotes, productos, caché de IA)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    archivo    TEXT NOT NULL,
    creado_en  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    fila            INTEGER NOT NULL,
    codigo          TEXT NOT NULL,
    nombre_excel    TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pendiente',
    match_tipo      TEXT,
    match_confianza REAL,
    woo_id          INTEGER,
    woo_sku         TEXT,
    url             TEXT,
    nombre_web      TEXT,
    descripcion_web TEXT,
    categorias      TEXT NOT NULL DEFAULT '[]',
    etiquetas       TEXT NOT NULL DEFAULT '[]',
    precio_web      TEXT,
    moneda          TEXT,
    en_stock        INTEGER,
    imagenes        TEXT NOT NULL DEFAULT '[]',
    titulo_fb       TEXT,
    descripcion_fb  TEXT,
    categoria_fb    TEXT,
    sku_fb          TEXT,
    etiquetas_fb    TEXT NOT NULL DEFAULT '[]',
    precio_fb       INTEGER,
    texto_origen    TEXT,
    nota            TEXT,
    publicado       INTEGER NOT NULL DEFAULT 0,
    publicado_en    TEXT,
    actualizado_en  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_products_batch ON products(batch_id);
CREATE TABLE IF NOT EXISTS ai_cache (
    clave     TEXT PRIMARY KEY,
    respuesta TEXT NOT NULL
);
"""

JSON_FIELDS = ("categorias", "etiquetas", "imagenes", "etiquetas_fb")

# Columnas añadidas después de la primera versión: se agregan a bases de datos existentes.
MIGRACIONES = {
    "products": {
        "sku_fb": "TEXT",
        "etiquetas_fb": "TEXT NOT NULL DEFAULT '[]'",
    },
}


def data_dir() -> Path:
    path = Path(os.environ.get("MKT_DATA_DIR", Path.cwd() / "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def images_dir() -> Path:
    path = data_dir() / "imagenes"
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def conn():
    c = sqlite3.connect(data_dir() / "app.db", timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        with _lock:
            yield c
            c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript(SCHEMA)
        for tabla, columnas in MIGRACIONES.items():
            existentes = {r["name"] for r in c.execute(f"PRAGMA table_info({tabla})")}
            for nombre, tipo in columnas.items():
                if nombre not in existentes:
                    c.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo}")


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for f in JSON_FIELDS:
        if f in d and isinstance(d[f], str):
            d[f] = json.loads(d[f])
    return d


def update_product(pid: int, **fields) -> None:
    if not fields:
        return
    vals = []
    for k, v in fields.items():
        vals.append(json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS else v)
    cols = ", ".join(f"{k} = ?" for k in fields)
    with conn() as c:
        c.execute(
            f"UPDATE products SET {cols}, actualizado_en = datetime('now','localtime') WHERE id = ?",
            (*vals, pid),
        )


def get_product(pid: int) -> dict | None:
    with conn() as c:
        return row_to_dict(c.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone())
