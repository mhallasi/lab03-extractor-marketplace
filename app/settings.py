"""Configuración editable desde la app (guardada en SQLite)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import db

DEFAULTS: dict[str, str] = {
    "tienda_url": "https://mihaba.com",
    "ai_base_url": "https://api.minimax.io/v1",
    "ai_model": "MiniMax-M2.7",
    "ai_api_key": "",
    "max_imagenes": "10",
    "max_lado_px": "2048",
    "condicion": "Nuevo",
    "pie_descripcion": "",
}


@dataclass
class Settings:
    tienda_url: str
    ai_base_url: str
    ai_model: str
    ai_api_key: str
    max_imagenes: int
    max_lado_px: int
    condicion: str
    pie_descripcion: str

    @property
    def ai_configurada(self) -> bool:
        return bool(self.ai_base_url and self.ai_model and self.ai_api_key)


def load_raw() -> dict[str, str]:
    values = dict(DEFAULTS)
    with db.conn() as c:
        for row in c.execute("SELECT key, value FROM settings"):
            if row["key"] in values:
                values[row["key"]] = row["value"]
    if not values["ai_api_key"]:
        values["ai_api_key"] = os.environ.get("AI_API_KEY", "")
    return values


def get() -> Settings:
    v = load_raw()

    def as_int(key: str) -> int:
        try:
            return max(1, int(v[key]))
        except (TypeError, ValueError):
            return int(DEFAULTS[key])

    return Settings(
        tienda_url=v["tienda_url"].strip().rstrip("/"),
        ai_base_url=v["ai_base_url"].strip().rstrip("/"),
        ai_model=v["ai_model"].strip(),
        ai_api_key=v["ai_api_key"].strip(),
        max_imagenes=as_int("max_imagenes"),
        max_lado_px=as_int("max_lado_px"),
        condicion=v["condicion"].strip() or "Nuevo",
        pie_descripcion=v["pie_descripcion"].strip(),
    )


def save(values: dict[str, str]) -> None:
    with db.conn() as c:
        for key, value in values.items():
            if key not in DEFAULTS:
                continue
            # Una API key vacía en el formulario significa "no cambiar".
            if key == "ai_api_key" and not value.strip():
                continue
            c.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value.strip()),
            )
