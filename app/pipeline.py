"""Orquesta el procesamiento de un lote: búsqueda -> imágenes -> texto IA."""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import ai, db, settings
from .extractor import FilaExcel, WooClient, datos_de_producto, descargar_imagenes

log = logging.getLogger(__name__)

# Pocos hilos: la tienda está detrás de Cloudflare y limita ráfagas (429).
# El cuello de botella real es la IA, que corre en paralelo a la descarga.
WORKERS = 3
_activos: dict[int, threading.Thread] = {}
_activos_lock = threading.Lock()


def carpeta_producto(p: dict) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", p["codigo"]).strip("_") or str(p["id"])
    return db.images_dir() / f"lote{p['batch_id']}" / f"{p['id']}-{safe}"


def crear_lote(nombre_archivo: str, filas: list[FilaExcel]) -> int:
    with db.conn() as c:
        cur = c.execute("INSERT INTO batches(archivo) VALUES(?)", (nombre_archivo,))
        batch_id = cur.lastrowid
        c.executemany(
            "INSERT INTO products(batch_id, fila, codigo, nombre_excel) VALUES(?, ?, ?, ?)",
            [(batch_id, f.fila, f.codigo, f.nombre) for f in filas],
        )
    return batch_id


NOTA_CANCELADO = "Se canceló porque la app se cerró antes de terminar. Pulsa «Reanudar» para continuar."


def cancelar_interrumpidos() -> int:
    """Al arrancar no hay ningún hilo vivo: todo lo que quedó en cola o a medias se interrumpió."""
    with db.conn() as c:
        cur = c.execute(
            "UPDATE products SET status = 'cancelado', nota = ?, actualizado_en = datetime('now','localtime') "
            "WHERE status IN ('pendiente', 'procesando')",
            (NOTA_CANCELADO,),
        )
        return cur.rowcount


def reanudar(batch_id: int) -> int:
    """Vuelve a poner en cola los productos cancelados o con error y arranca el lote."""
    with db.conn() as c:
        cur = c.execute(
            "UPDATE products SET status = 'pendiente', nota = NULL "
            "WHERE batch_id = ? AND status IN ('cancelado', 'error')",
            (batch_id,),
        )
        n = cur.rowcount
    if n:
        iniciar_lote(batch_id)
    return n


def procesando(batch_id: int) -> bool:
    with _activos_lock:
        t = _activos.get(batch_id)
        return bool(t and t.is_alive())


def iniciar_lote(batch_id: int, solo_ids: list[int] | None = None) -> None:
    with _activos_lock:
        t = _activos.get(batch_id)
        if t and t.is_alive() and solo_ids is None:
            return
        t = threading.Thread(target=_procesar_lote, args=(batch_id, solo_ids), daemon=True)
        _activos[batch_id] = t
        t.start()


def _procesar_lote(batch_id: int, solo_ids: list[int] | None) -> None:
    with db.conn() as c:
        if solo_ids:
            q = f"SELECT id FROM products WHERE batch_id = ? AND id IN ({','.join('?' * len(solo_ids))})"
            ids = [r["id"] for r in c.execute(q, (batch_id, *solo_ids))]
        else:
            ids = [r["id"] for r in c.execute(
                "SELECT id FROM products WHERE batch_id = ? AND status IN ('pendiente','procesando','error') "
                "ORDER BY fila", (batch_id,))]
    if not ids:
        return
    woo = WooClient(settings.get().tienda_url)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(lambda pid: procesar_producto(pid, woo), ids))


def procesar_producto(pid: int, woo: WooClient | None = None, usar_cache: bool = True) -> None:
    cfg = settings.get()
    woo = woo or WooClient(cfg.tienda_url)
    p = db.get_product(pid)
    if not p:
        return
    db.update_product(pid, status="procesando", nota=None)
    try:
        match = woo.encontrar(p["codigo"], p["nombre_excel"])
        if match.tipo == "ninguno":
            db.update_product(pid, status="no_encontrado", match_tipo="ninguno", match_confianza=0,
                              nota=match.nota)
            return

        datos = datos_de_producto(match.producto)
        urls = datos.pop("_imagenes_url")
        # El SKU del anuncio es el de la web (el del Excel puede tener erratas). Editable en la tarjeta.
        datos["sku_fb"] = datos["woo_sku"] or p["codigo"]
        db.update_product(pid, match_tipo=match.tipo, match_confianza=match.confianza, **datos)

        notas: list[str] = []
        if match.tipo == "dudoso":
            notas.append(match.nota + ". Confirma que es el producto correcto.")

        img = descargar_imagenes(woo, urls, carpeta_producto(p), cfg.max_imagenes, cfg.max_lado_px)
        db.update_product(pid, imagenes=img.archivos)
        if not img.archivos:
            notas.append("Sin imágenes descargadas.")
        elif img.errores:
            notas.append(f"{len(img.errores)} imagen(es) no se pudieron descargar.")
        if datos["precio_fb"] is None:
            notas.append("El producto no tiene precio en la web.")
        if not datos["en_stock"]:
            notas.append("Sin stock en la web.")

        notas += _generar_texto(pid, usar_cache=usar_cache)

        db.update_product(
            pid,
            status="revisar" if notas else "listo",
            nota=" ".join(notas) or None,
        )
    except Exception as exc:  # noqa: BLE001 - se refleja en la UI
        log.exception("Error procesando producto %s", pid)
        db.update_product(pid, status="error", nota=f"Error: {exc}")


def _generar_texto(pid: int, usar_cache: bool = True) -> list[str]:
    """Genera título/descripción/categoría. Devuelve notas de advertencia."""
    cfg = settings.get()
    p = db.get_product(pid)
    if cfg.ai_configurada:
        try:
            r = ai.generar_anuncio(p, cfg.ai_base_url, cfg.ai_api_key, cfg.ai_model, usar_cache=usar_cache)
            db.update_product(pid, titulo_fb=r["titulo"], descripcion_fb=r["descripcion"],
                              categoria_fb=r["categoria"], etiquetas_fb=r.get("etiquetas") or [],
                              texto_origen="ia")
            return []
        except ai.AIError as exc:
            aviso = f"IA no disponible ({exc}); se usó el texto de la web."
    else:
        aviso = "IA no configurada; se usó el texto de la web."
    r = ai.anuncio_sin_ia(p)
    db.update_product(pid, titulo_fb=r["titulo"], descripcion_fb=r["descripcion"],
                      categoria_fb=r["categoria"], etiquetas_fb=r["etiquetas"], texto_origen="web")
    return [aviso]


_AVISO_IA_RE = re.compile(r"\s*IA no (?:disponible \(.*?\)|configurada); se usó el texto de la web\.")


def regenerar_texto(pid: int) -> dict:
    notas = _generar_texto(pid, usar_cache=False)
    p = db.get_product(pid)
    # Quita el aviso de IA anterior para no acumular uno por cada intento.
    base = _AVISO_IA_RE.sub("", p.get("nota") or "").strip()
    nota = " ".join(filter(None, [base, *notas])) or None
    if nota:
        db.update_product(pid, nota=nota, status="revisar")
    elif p["status"] == "revisar":
        db.update_product(pid, status="listo", nota=None)
    return db.get_product(pid)
