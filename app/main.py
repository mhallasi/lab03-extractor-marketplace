"""App web local (FastAPI)."""

from __future__ import annotations

import io
import logging
import os
import re
import zipfile
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import ai, db, pipeline, settings
from .extractor import WooClient, leer_excel

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

BASE = Path(__file__).parent
app = FastAPI(title="Extractor Marketplace")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

FB_CREATE_URL = "https://www.facebook.com/marketplace/create/item"
MAX_EXCEL_BYTES = 10 * 1024 * 1024


@app.on_event("startup")
def _startup() -> None:
    db.init()
    # Si la app se cerró en pleno proceso, lo que quedó a medias pasa a "cancelado".
    n = pipeline.cancelar_interrumpidos()
    if n:
        logging.getLogger(__name__).warning("%s producto(s) quedaron cancelados por un cierre previo", n)


# ------------------------------------------------------------------ utilidades

def _resumen(batch_id: int) -> dict:
    with db.conn() as c:
        rows = c.execute(
            "SELECT status, publicado, COUNT(*) n FROM products WHERE batch_id = ? GROUP BY status, publicado",
            (batch_id,),
        ).fetchall()
    r = {"total": 0, "pendiente": 0, "procesando": 0, "listo": 0, "revisar": 0,
         "no_encontrado": 0, "error": 0, "cancelado": 0, "publicado": 0}
    for row in rows:
        r["total"] += row["n"]
        r[row["status"]] = r.get(row["status"], 0) + row["n"]
        if row["publicado"]:
            r["publicado"] += row["n"]
    r["terminados"] = r["total"] - r["pendiente"] - r["procesando"]
    r["procesando_ahora"] = pipeline.procesando(batch_id)
    return r


def _producto_publico(p: dict) -> dict:
    p = dict(p)
    p["imagenes_url"] = [f"/imagenes/{p['id']}/{name}" for name in p.get("imagenes") or []]
    cfg = settings.get()
    desc = p.get("descripcion_fb") or ""
    if cfg.pie_descripcion and desc and cfg.pie_descripcion not in desc:
        desc = f"{desc}\n\n{cfg.pie_descripcion}"
    p["descripcion_final"] = desc
    p["condicion"] = cfg.condicion
    # Productos procesados antes de existir el campo: usar el SKU de la web o del Excel.
    p["sku_fb"] = p.get("sku_fb") or p.get("woo_sku") or p.get("codigo")
    p["etiquetas_fb"] = p.get("etiquetas_fb") or []
    return p


def _get_or_404(pid: int) -> dict:
    p = db.get_product(pid)
    if not p:
        raise HTTPException(404, "Producto no encontrado")
    return p


# ------------------------------------------------------------------ páginas

@app.get("/", response_class=HTMLResponse)
def inicio(request: Request):
    with db.conn() as c:
        lotes = [dict(r) for r in c.execute("SELECT * FROM batches ORDER BY id DESC")]
    for lote in lotes:
        lote["resumen"] = _resumen(lote["id"])
    cfg = settings.get()
    return templates.TemplateResponse(request, "inicio.html", {"lotes": lotes, "cfg": cfg})


@app.post("/lotes")
async def subir_excel(archivo: UploadFile = File(...)):
    if not (archivo.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Sube un archivo Excel (.xlsx)")
    data = await archivo.read()
    if len(data) > MAX_EXCEL_BYTES:
        raise HTTPException(400, "El archivo es demasiado grande")
    try:
        filas = leer_excel(data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"No se pudo leer el Excel: {exc}") from exc
    if not filas:
        raise HTTPException(400, "El Excel no tiene códigos de producto")
    batch_id = pipeline.crear_lote(archivo.filename, filas)
    pipeline.iniciar_lote(batch_id)
    return RedirectResponse(f"/lotes/{batch_id}", status_code=303)


@app.get("/lotes/{batch_id}", response_class=HTMLResponse)
def ver_lote(request: Request, batch_id: int):
    with db.conn() as c:
        lote = c.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if not lote:
        raise HTTPException(404, "Lote no encontrado")
    return templates.TemplateResponse(
        request, "lote.html", {"lote": dict(lote), "fb_url": FB_CREATE_URL}
    )


@app.post("/lotes/{batch_id}/eliminar")
def eliminar_lote(batch_id: int):
    import shutil

    with db.conn() as c:
        c.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
    shutil.rmtree(db.images_dir() / f"lote{batch_id}", ignore_errors=True)
    return RedirectResponse("/", status_code=303)


@app.get("/configuracion", response_class=HTMLResponse)
def ver_config(request: Request, guardado: int = 0):
    raw = settings.load_raw()
    key = raw.get("ai_api_key", "")
    mascara = f"{key[:6]}…{key[-4:]}" if len(key) > 12 else ("configurada" if key else "")
    return templates.TemplateResponse(
        request, "configuracion.html", {"cfg": raw, "mascara": mascara, "guardado": guardado}
    )


@app.post("/configuracion")
def guardar_config(
    tienda_url: str = Form(""), ai_base_url: str = Form(""), ai_model: str = Form(""),
    ai_api_key: str = Form(""), max_imagenes: str = Form("10"), max_lado_px: str = Form("2048"),
    condicion: str = Form("Nuevo"), pie_descripcion: str = Form(""),
):
    settings.save(locals())
    return RedirectResponse("/configuracion?guardado=1", status_code=303)


# ------------------------------------------------------------------ API JSON

@app.post("/api/probar/tienda")
def probar_tienda(tienda_url: str = Form("")):
    url = (tienda_url or settings.get().tienda_url).strip().rstrip("/")
    try:
        total = WooClient(url, retries=2, espera_base=2).probar()
        return {"ok": True, "mensaje": f"Tienda WooCommerce OK: {total} productos publicados"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "mensaje": f"No se pudo leer la tienda: {exc}"}


@app.post("/api/probar/ia")
def probar_ia(ai_base_url: str = Form(""), ai_model: str = Form(""), ai_api_key: str = Form("")):
    cfg = settings.get()
    try:
        respuesta = ai.probar_conexion(ai_base_url or cfg.ai_base_url, ai_api_key or cfg.ai_api_key,
                                       ai_model or cfg.ai_model)
        return {"ok": True, "mensaje": f"Conexión OK. Respuesta: {respuesta}"}
    except ai.AIError as exc:
        return {"ok": False, "mensaje": str(exc)}


@app.get("/api/lotes/{batch_id}")
def api_lote(batch_id: int):
    with db.conn() as c:
        rows = c.execute("SELECT * FROM products WHERE batch_id = ? ORDER BY fila", (batch_id,)).fetchall()
    return {"resumen": _resumen(batch_id), "productos": [_producto_publico(db.row_to_dict(r)) for r in rows]}


@app.post("/api/lotes/{batch_id}/reanudar")
def reanudar_lote(batch_id: int):
    """Reanuda los productos cancelados (cierre de la app) y reintenta los que dieron error."""
    return {"ok": True, "reanudados": pipeline.reanudar(batch_id)}


@app.get("/api/productos/{pid}")
def api_producto(pid: int):
    return _producto_publico(_get_or_404(pid))


@app.post("/api/productos/{pid}")
async def editar_producto(pid: int, request: Request):
    _get_or_404(pid)
    body = await request.json()
    cambios = {}
    if "titulo_fb" in body:
        cambios["titulo_fb"] = str(body["titulo_fb"])[:100]
    if "descripcion_fb" in body:
        cambios["descripcion_fb"] = str(body["descripcion_fb"])[:5000]
    if "categoria_fb" in body:
        cambios["categoria_fb"] = str(body["categoria_fb"])[:120]
    if "sku_fb" in body:
        cambios["sku_fb"] = str(body["sku_fb"]).strip()[:100]
    if "etiquetas_fb" in body:
        # Edición manual: se permiten más etiquetas que las que genera la IA (Facebook admite hasta 20).
        cambios["etiquetas_fb"] = ai.limpiar_etiquetas(body["etiquetas_fb"], limite=20)
    if "precio_fb" in body:
        try:
            cambios["precio_fb"] = max(0, int(body["precio_fb"]))
        except (TypeError, ValueError):
            raise HTTPException(400, "El precio debe ser un número entero")
    if body.get("confirmar"):
        cambios.update(status="listo", nota=None, match_tipo="confirmado")
    db.update_product(pid, **cambios)
    return _producto_publico(db.get_product(pid))


@app.post("/api/productos/{pid}/publicado")
async def marcar_publicado(pid: int, request: Request):
    _get_or_404(pid)
    body = await request.json()
    valor = 1 if body.get("publicado") else 0
    with db.conn() as c:
        c.execute(
            "UPDATE products SET publicado = ?, publicado_en = CASE WHEN ? = 1 THEN datetime('now','localtime') "
            "ELSE NULL END WHERE id = ?",
            (valor, valor, pid),
        )
    return _producto_publico(db.get_product(pid))


@app.post("/api/productos/{pid}/regenerar")
def regenerar(pid: int):
    p = _get_or_404(pid)
    if not p.get("nombre_web"):
        raise HTTPException(400, "Primero hay que encontrar el producto en la tienda")
    return _producto_publico(pipeline.regenerar_texto(pid))


@app.post("/api/productos/{pid}/reprocesar")
async def reprocesar(pid: int, request: Request):
    p = _get_or_404(pid)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    codigo = str(body.get("codigo") or "").strip()
    if codigo:
        db.update_product(pid, codigo=codigo)
    db.update_product(pid, status="pendiente")
    pipeline.iniciar_lote(p["batch_id"], solo_ids=[pid])
    return {"ok": True}


# ------------------------------------------------------------------ imágenes

_NOMBRE_IMG = re.compile(r"^\d{2}\.jpg$")


@app.get("/imagenes/{pid}/{nombre}")
def imagen(pid: int, nombre: str):
    if not _NOMBRE_IMG.match(nombre):
        raise HTTPException(404)
    path = pipeline.carpeta_producto(_get_or_404(pid)) / nombre
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


def _nombre_archivo(p: dict) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", p.get("woo_sku") or p["codigo"]).strip("_") or str(p["id"])


@app.get("/imagenes/{pid}.zip")
def imagenes_zip(pid: int):
    p = _get_or_404(pid)
    carpeta = pipeline.carpeta_producto(p)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for nombre in p.get("imagenes") or []:
            path = carpeta / nombre
            if path.is_file():
                z.write(path, f"{_nombre_archivo(p)}-{nombre}")
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{_nombre_archivo(p)}-fotos.zip"'},
    )


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/") or request.headers.get("accept", "").startswith("application/json"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(
        request, "error.html", {"mensaje": exc.detail}, status_code=exc.status_code
    )


def run() -> None:
    import uvicorn

    host = os.environ.get("MKT_HOST", "127.0.0.1")
    port = int(os.environ.get("MKT_PORT", "8000"))
    print(f"\n  Abre en tu navegador: http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}\n")
    uvicorn.run("app.main:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()
