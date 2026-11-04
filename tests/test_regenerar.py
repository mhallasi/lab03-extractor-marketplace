from app import ai, db, pipeline
from app.extractor import FilaExcel


def test_regenerar_no_acumula_avisos_de_ia(tmp_path, monkeypatch):
    monkeypatch.setenv("MKT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_API_KEY", "k")
    db.init()
    batch = pipeline.crear_lote("x.xlsx", [FilaExcel(2, "A", "Prod")])
    pid = 1
    nota_match = "SKU parecido: Excel «A» vs web «UA». Confirma que es el producto correcto."
    db.update_product(pid, nombre_web="Prod", descripcion_web="desc", status="revisar", nota=nota_match)

    def falla(*a, **k):
        raise ai.AIError("La IA respondió con error (HTTP 401): invalid api key (2049)")

    monkeypatch.setattr(ai, "generar_anuncio", falla)
    for _ in range(3):
        p = pipeline.regenerar_texto(pid)
    assert p["nota"].count("IA no disponible") == 1
    assert p["nota"].startswith(nota_match)
    assert p["status"] == "revisar"

    # Cuando la IA vuelve a funcionar, el aviso desaparece y queda solo el del SKU.
    monkeypatch.setattr(ai, "generar_anuncio", lambda *a, **k: {
        "titulo": "Prod nuevo", "descripcion": "Descripción larga del producto.", "categoria": "C", "etiquetas": ["x"]})
    p = pipeline.regenerar_texto(pid)
    assert p["nota"] == nota_match and p["texto_origen"] == "ia" and p["etiquetas_fb"] == ["x"]

    # Sin otras advertencias, pasa a listo.
    db.update_product(pid, nota="IA no configurada; se usó el texto de la web.")
    p = pipeline.regenerar_texto(pid)
    assert p["status"] == "listo" and p["nota"] is None
    assert batch == 1
