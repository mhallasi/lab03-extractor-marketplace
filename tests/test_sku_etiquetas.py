import json
import sqlite3

import httpx
import pytest

from app import ai, db


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("MKT_DATA_DIR", str(tmp_path))
    db.init()
    return tmp_path


def test_limpiar_etiquetas():
    r = ai.limpiar_etiquetas(["#Linterna LED", "linterna led", "  OPALUX ", "", "需要", "x" * 60, "camping 🔥"])
    assert r == ["linterna led", "opalux", "camping"]
    assert ai.limpiar_etiquetas("a, b;c\nd") == ["a", "b", "c", "d"]
    assert len(ai.limpiar_etiquetas([f"t{i}" for i in range(30)])) == ai.MAX_ETIQUETAS
    assert len(ai.limpiar_etiquetas([f"t{i}" for i in range(30)], limite=20)) == 20
    assert ai.limpiar_etiquetas(None) == []
    # El "#" dentro de un SKU se conserva; el de hashtag no.
    assert ai.limpiar_etiquetas(["URT46F4U#01M4B2", "##cable", "cable #utp"]) == ["urt46f4u#01m4b2", "cable", "cable utp"]


def _client(content):
    return httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": content}}]})))


PROD = {"nombre_web": "Linterna", "codigo": "OP-8085", "woo_sku": "OP-8085",
        "categorias": ["Linternas leds", "Iluminación"], "etiquetas": [], "descripcion_web": "desc"}


def test_ia_devuelve_etiquetas():
    content = json.dumps({"titulo": "Linterna LED OPALUX OP-8085", "descripcion": "Linterna para camping y emergencias.",
                          "categoria": "Hogar", "etiquetas": ["Linterna LED", "#opalux", "op-8085"]})
    r = ai.generar_anuncio(PROD, "http://ia/v1", "k", "m", usar_cache=False, client=_client(content))
    assert r["etiquetas"] == ["linterna led", "opalux", "op-8085"]


def test_ia_sin_etiquetas_usa_las_de_la_web():
    content = json.dumps({"titulo": "Linterna LED OPALUX OP-8085", "descripcion": "Linterna para camping y emergencias.",
                          "categoria": "Hogar"})
    r = ai.generar_anuncio(PROD, "http://ia/v1", "k", "m", usar_cache=False, client=_client(content))
    assert r["etiquetas"] == ["linternas leds", "iluminación", "op-8085"]


def test_anuncio_sin_ia_incluye_etiquetas_web_y_sku():
    r = ai.anuncio_sin_ia({**PROD, "etiquetas": ["Ofertas"]})
    assert r["etiquetas"] == ["ofertas", "linternas leds", "iluminación", "op-8085"]


def test_migracion_agrega_columnas_a_db_existente(tmp_path, monkeypatch):
    """Una base creada con la versión anterior (sin sku_fb/etiquetas_fb) se actualiza sola."""
    viejo = tmp_path / "vieja"
    viejo.mkdir()
    con = sqlite3.connect(viejo / "app.db")
    con.executescript("""
        CREATE TABLE batches (id INTEGER PRIMARY KEY AUTOINCREMENT, archivo TEXT NOT NULL,
                              creado_en TEXT NOT NULL DEFAULT (datetime('now','localtime')));
        CREATE TABLE products (id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER NOT NULL, fila INTEGER NOT NULL,
            codigo TEXT NOT NULL, nombre_excel TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pendiente',
            match_tipo TEXT, match_confianza REAL, woo_id INTEGER, woo_sku TEXT, url TEXT, nombre_web TEXT,
            descripcion_web TEXT, categorias TEXT NOT NULL DEFAULT '[]', etiquetas TEXT NOT NULL DEFAULT '[]',
            precio_web TEXT, moneda TEXT, en_stock INTEGER, imagenes TEXT NOT NULL DEFAULT '[]', titulo_fb TEXT,
            descripcion_fb TEXT, categoria_fb TEXT, precio_fb INTEGER, texto_origen TEXT, nota TEXT,
            publicado INTEGER NOT NULL DEFAULT 0, publicado_en TEXT,
            actualizado_en TEXT NOT NULL DEFAULT (datetime('now','localtime')));
        INSERT INTO batches(archivo) VALUES ('viejo.xlsx');
        INSERT INTO products(batch_id, fila, codigo, woo_sku, titulo_fb) VALUES (1, 2, 'OP-4030', 'OP-4030', 'T');
    """)
    con.commit()
    con.close()

    monkeypatch.setenv("MKT_DATA_DIR", str(viejo))
    db.init()
    db.init()  # idempotente
    p = db.get_product(1)
    assert p["titulo_fb"] == "T"
    assert p["sku_fb"] is None and p["etiquetas_fb"] == []
    db.update_product(1, sku_fb="OP-4030", etiquetas_fb=["lampara"])
    assert db.get_product(1)["etiquetas_fb"] == ["lampara"]
