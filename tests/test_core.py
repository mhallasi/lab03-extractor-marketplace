import io
import json

import httpx
import pytest
from openpyxl import Workbook
from PIL import Image

from app import ai, db
from app.extractor import (
    a_jpg, datos_de_producto, elegir_match, html_a_texto, leer_excel, precio_entero,
)


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("MKT_DATA_DIR", str(tmp_path))
    db.init()


def test_precio_redondea_hacia_arriba():
    assert precio_entero("6190") == 62
    assert precio_entero("6100") == 61
    assert precio_entero("6101") == 62
    assert precio_entero("10690") == 107
    assert precio_entero(None) is None


def _excel(rows):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_leer_excel_con_cabecera_y_codigos_numericos():
    data = _excel([["CÓDIGO", "NOMBRE"], ["OP-4030", "Lámpara"], [55481, "Juguete"], [None, "x"], ["op-4030", "dup"]])
    filas = leer_excel(data)
    assert [(f.codigo, f.nombre) for f in filas] == [("OP-4030", "Lámpara"), ("55481", "Juguete")]


def test_html_a_texto_tablas_y_listas():
    html = ("<p>Intro</p><h2>Especificaciones</h2><table><tr><th>Esp</th><th>Detalle</th></tr>"
            "<tr><td>Marca</td><td>SOUNDKING</td></tr></table><ul><li><div><p>Uno</p></div></li></ul>")
    txt = html_a_texto(html)
    assert "Intro" in txt
    assert "Especificaciones:" in txt
    assert "- Marca: SOUNDKING" in txt
    assert "- Uno" in txt
    assert "Esp" not in txt.replace("Especificaciones", "")


def test_match_exacto_y_dudoso():
    prods = [{"sku": "URT46F4U#01M4B2", "name": "Cable Patch Cord CAT 6 Rojo DIXON"}]
    m = elegir_match("RT46F4U#01M4B2", "Cable Patch Cord CAT 6 LSZH 1 Metro Rojo DIXON", prods, lambda: [])
    assert m.tipo == "dudoso" and m.producto["sku"] == "URT46F4U#01M4B2"
    m = elegir_match("urt46f4u#01m4b2", "", prods, lambda: [])
    assert m.tipo == "exacto"
    m = elegir_match("55481", "Juguete Carro", [], lambda: [])
    assert m.tipo == "ninguno"


def test_datos_de_producto():
    p = {"id": 1, "sku": "A", "name": "Foo &amp; Bar", "permalink": "u", "description": "<p>Hola</p>",
         "categories": [{"name": "Cat"}], "tags": [], "is_in_stock": True,
         "prices": {"price": "6190", "currency_code": "PEN", "currency_minor_unit": 2},
         "images": [{"src": "http://x/1.jpg"}, {"src": "http://x/2.jpg"}]}
    d = datos_de_producto(p)
    assert d["precio_fb"] == 62 and d["precio_web"] == "61.90"
    assert d["nombre_web"] == "Foo & Bar"
    assert d["_imagenes_url"] == ["http://x/1.jpg", "http://x/2.jpg"]


def test_a_jpg_png_transparente_y_redimension():
    img = Image.new("RGBA", (3000, 1500), (255, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    out = Image.open(io.BytesIO(a_jpg(buf.getvalue(), 1000)))
    assert out.format == "JPEG" and max(out.size) == 1000 and out.mode == "RGB"


def test_extraer_json_con_think_y_fences():
    raw = '<think>pensando {no json}</think>\n```json\n{"titulo": "T", "descripcion": "D", "categoria": "C"}\n```'
    assert ai.extraer_json(raw)["titulo"] == "T"
    raw2 = 'Aquí está: {"titulo": "con } llave", "descripcion": "x"} fin'
    assert ai.extraer_json(raw2)["titulo"] == "con } llave"


def test_validar_limpia_emojis_urls_y_recorta():
    data = {"titulo": "🔥 " + "Lámpara LED " * 20, "descripcion": "Gran producto ✅ visita https://mihaba.com\n• uno\n**dos**",
            "categoria": "Hogar > Iluminación"}
    r = ai.validar(data)
    assert len(r["titulo"]) <= 100 and "🔥" not in r["titulo"]
    assert "http" not in r["descripcion"] and "✅" not in r["descripcion"]
    assert "- uno" in r["descripcion"] and "**" not in r["descripcion"]


def test_validar_rechaza_caracteres_chinos():
    with pytest.raises(ai.AIError, match="otro idioma"):
        ai.validar({"titulo": "Cable de red DIXON", "descripcion": "Ideal para、需要 conectar equipos en oficinas"})


def test_generar_anuncio_reintenta_si_mezcla_idiomas():
    respuestas = iter([
        '{"titulo": "Cable DIXON", "descripcion": "Ideal 需要 para redes de oficina", "categoria": "X"}',
        '{"titulo": "Cable DIXON", "descripcion": "Ideal para redes de oficina y hogar", "categoria": "X"}',
    ])

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": next(respuestas)}}]})

    r = ai.generar_anuncio({"nombre_web": "Cable", "codigo": "C"}, "http://ia/v1", "k", "m",
                           usar_cache=False, client=_mock_client(handler))
    assert "需要" not in r["descripcion"]


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_generar_anuncio_cache_y_errores():
    llamadas = []

    def ok(request):
        llamadas.append(json.loads(request.content))
        content = '<think>x</think>{"titulo": "Lámpara Camping LED 10W OPALUX OP-4030", "descripcion": "Ideal para camping y emergencias.\\n- Recargable", "categoria": "Deportes > Camping"}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    prod = {"nombre_web": "Lámpara", "codigo": "OP-4030", "categorias": ["Iluminación"], "descripcion_web": "desc"}
    r1 = ai.generar_anuncio(prod, "http://ia/v1", "k", "m", client=_mock_client(ok))
    r2 = ai.generar_anuncio(prod, "http://ia/v1", "k", "m", client=_mock_client(ok))
    assert r1 == r2 and len(llamadas) == 1
    assert llamadas[0]["messages"][0]["role"] == "system"

    def unauthorized(request):
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    with pytest.raises(ai.AIError, match="invalid api key"):
        ai.generar_anuncio(prod, "http://ia/v1", "k", "m", usar_cache=False, client=_mock_client(unauthorized))


def test_anuncio_sin_ia():
    r = ai.anuncio_sin_ia({"nombre_web": "Linterna «OPALUX» 25W", "descripcion_web": "Texto", "categorias": ["Iluminación"]})
    assert r["titulo"] == "Linterna OPALUX 25W"
    assert r["descripcion"].endswith("disponibilidad y entrega.")
    assert r["categoria"] == "Iluminación"
