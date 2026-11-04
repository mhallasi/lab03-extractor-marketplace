from app import db, pipeline
from app.extractor import FilaExcel


def _estados(batch_id):
    with db.conn() as c:
        return {r["codigo"]: (r["status"], r["nota"]) for r in
                c.execute("SELECT codigo, status, nota FROM products WHERE batch_id = ?", (batch_id,))}


def test_cierre_a_mitad_deja_cancelado_y_se_puede_reanudar(tmp_path, monkeypatch):
    monkeypatch.setenv("MKT_DATA_DIR", str(tmp_path))
    db.init()
    batch = pipeline.crear_lote("x.xlsx", [FilaExcel(2, "A", ""), FilaExcel(3, "B", ""),
                                           FilaExcel(4, "C", ""), FilaExcel(5, "D", "")])
    with db.conn() as c:
        c.execute("UPDATE products SET status = 'listo' WHERE codigo = 'A'")
        c.execute("UPDATE products SET status = 'procesando' WHERE codigo = 'B'")
        c.execute("UPDATE products SET status = 'error' WHERE codigo = 'D'")

    # Simula el arranque de la app tras cerrar la terminal.
    assert pipeline.cancelar_interrumpidos() == 2
    e = _estados(batch)
    assert e["A"][0] == "listo" and e["D"][0] == "error"
    assert e["B"][0] == e["C"][0] == "cancelado"
    assert "se cerró" in e["B"][1]

    lanzados = []
    monkeypatch.setattr(pipeline, "iniciar_lote", lambda b, solo_ids=None: lanzados.append(b))
    assert pipeline.reanudar(batch) == 3  # 2 cancelados + 1 error
    e = _estados(batch)
    assert {e[k][0] for k in "BCD"} == {"pendiente"} and e["B"][1] is None
    assert e["A"][0] == "listo"
    assert lanzados == [batch]

    # Nada que reanudar: no lanza otro hilo.
    with db.conn() as c:
        c.execute("UPDATE products SET status = 'listo'")
    assert pipeline.reanudar(batch) == 0 and lanzados == [batch]
