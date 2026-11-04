"""Extracción determinista desde la WooCommerce Store API (sin IA)."""

from __future__ import annotations

import io
import math
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag
from openpyxl import load_workbook
from PIL import Image

USER_AGENT = "Mozilla/5.0 (marketplace-extractor/0.1)"
STORE_PATH = "/wp-json/wc/store/v1/products"


# ---------------------------------------------------------------- Excel

@dataclass
class FilaExcel:
    fila: int
    codigo: str
    nombre: str


def _norm_header(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return text.strip().lower()


def _cell_to_code(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def leer_excel(path_or_bytes: Path | bytes) -> list[FilaExcel]:
    """Lee la primera hoja. Busca columnas CÓDIGO/SKU y NOMBRE; si no hay cabecera usa A y B."""
    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes
    ws = load_workbook(src, read_only=True, data_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    headers = [_norm_header(h) for h in rows[0]]
    col_code = next((i for i, h in enumerate(headers) if h in ("codigo", "sku", "cod", "code")), None)
    col_name = next((i for i, h in enumerate(headers) if h in ("nombre", "name", "producto", "descripcion")), None)
    start = 1
    if col_code is None:
        col_code, col_name, start = 0, 1, 0

    result: list[FilaExcel] = []
    seen: set[str] = set()
    for idx, row in enumerate(rows[start:], start=start + 1):
        code = _cell_to_code(row[col_code] if col_code < len(row) else None)
        name = ""
        if col_name is not None and col_name < len(row) and row[col_name] is not None:
            name = str(row[col_name]).strip()
        if not code or code.upper() in seen:
            continue
        seen.add(code.upper())
        result.append(FilaExcel(fila=idx, codigo=code, nombre=name))
    return result


# ---------------------------------------------------------------- Precio y texto

def precio_entero(raw_price: str | int | None, minor_unit: int = 2) -> int | None:
    """La Store API da el precio en unidades menores (6190 = 61.90). Redondea hacia arriba."""
    if raw_price in (None, ""):
        return None
    value = Decimal(str(raw_price)) / (Decimal(10) ** int(minor_unit or 0))
    return int(math.ceil(value))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def html_a_texto(html: str) -> str:
    """Convierte la descripción HTML de WooCommerce en texto plano legible.

    Tablas de 2 columnas -> "- Clave: Valor"; listas -> "- item"; títulos y párrafos en líneas.
    """
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for bad in soup(["script", "style", "img", "iframe"]):
        bad.decompose()

    lines: list[str] = []

    def walk(node):
        for child in node.children:
            if isinstance(child, NavigableString):
                text = _clean(str(child))
                if text:
                    lines.append(text)
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name
            if name == "table":
                for tr in child.find_all("tr"):
                    cells = [_clean(c.get_text(" ")) for c in tr.find_all(["td", "th"])]
                    cells = [c for c in cells if c]
                    if not cells or tr.find("th") and not tr.find("td"):
                        continue  # fila de cabecera
                    if len(cells) >= 2:
                        lines.append(f"- {cells[0]}: {' '.join(cells[1:])}")
                    else:
                        lines.append(f"- {cells[0]}")
                lines.append("")
            elif name in ("ul", "ol"):
                for li in child.find_all("li", recursive=False):
                    text = _clean(li.get_text(" "))
                    if text:
                        lines.append(f"- {text}")
                lines.append("")
            elif name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                text = _clean(child.get_text(" "))
                if text:
                    lines.extend(["", f"{text}:"])
            elif name in ("p", "br"):
                text = _clean(child.get_text(" "))
                if text:
                    lines.extend([text, ""])
            else:
                walk(child)

    walk(soup)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalizar(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", text)


# ---------------------------------------------------------------- Woo client

@dataclass
class Match:
    tipo: str  # exacto | dudoso | ninguno
    confianza: float
    producto: dict | None = None
    nota: str = ""


class Throttle:
    """Espaciado mínimo entre peticiones a un mismo host, compartido entre hilos.

    Tras un 429 se pausa todo el host (no solo el hilo que lo recibió)."""

    def __init__(self, intervalo: float):
        self.intervalo = intervalo
        self._lock = threading.Lock()
        self._siguiente = 0.0

    def esperar(self) -> None:
        with self._lock:
            ahora = time.monotonic()
            turno = max(ahora, self._siguiente)
            self._siguiente = turno + self.intervalo
        if turno > ahora:
            time.sleep(turno - ahora)

    def pausar(self, segundos: float) -> None:
        with self._lock:
            self._siguiente = max(self._siguiente, time.monotonic() + segundos)


_throttles: dict[str, Throttle] = {}
_throttles_lock = threading.Lock()


def throttle_para(url: str, intervalo: float) -> Throttle:
    host = urlparse(url).netloc.lower()
    with _throttles_lock:
        if host not in _throttles:
            _throttles[host] = Throttle(intervalo)
        return _throttles[host]


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return min(120.0, max(1.0, float(resp.headers.get("retry-after", ""))))
    except ValueError:
        return None


class WooClient:
    """Cliente educado: espacia las peticiones y respeta los límites (429) de Cloudflare."""

    def __init__(self, tienda_url: str, client: httpx.Client | None = None, retries: int = 6,
                 intervalo: float = 0.8, espera_base: float = 5.0):
        self.base = tienda_url.rstrip("/")
        self.client = client or httpx.Client(
            timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
        self.retries = retries
        self.intervalo = intervalo
        self.espera_base = espera_base

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        throttle = throttle_para(url, self.intervalo)
        last = ""
        for attempt in range(self.retries):
            throttle.esperar()
            try:
                resp = self.client.get(url, params=params)
            except httpx.TransportError as exc:
                last = str(exc) or exc.__class__.__name__
                time.sleep(min(60, self.espera_base * 2 ** attempt))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"HTTP {resp.status_code}"
                espera = _retry_after(resp) or min(60, self.espera_base * 2 ** attempt)
                if resp.status_code == 429:
                    throttle.pausar(espera)  # frena a todos los hilos
                time.sleep(espera)
                continue
            resp.raise_for_status()
            return resp
        if last == "HTTP 429":
            raise RuntimeError("La tienda está limitando las peticiones (HTTP 429). Reintenta en unos minutos.")
        raise RuntimeError(f"No se pudo conectar con la tienda ({last})")

    def buscar(self, termino: str, per_page: int = 20) -> list[dict]:
        resp = self._get(f"{self.base}{STORE_PATH}", {"search": termino, "per_page": per_page})
        data = resp.json()
        return data if isinstance(data, list) else []

    def probar(self) -> int:
        """Devuelve el total de productos publicados (para validar que es WooCommerce)."""
        resp = self._get(f"{self.base}{STORE_PATH}", {"per_page": 1})
        if not isinstance(resp.json(), list):
            raise RuntimeError("La URL no responde como una tienda WooCommerce")
        return int(resp.headers.get("x-wp-total", "0") or 0)

    def descargar(self, url: str) -> bytes:
        return self._get(url).content

    def encontrar(self, codigo: str, nombre: str = "") -> Match:
        return elegir_match(codigo, nombre, self.buscar(codigo), lambda: self.buscar(nombre) if nombre else [])


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, normalizar(a), normalizar(b)).ratio()


def elegir_match(codigo: str, nombre: str, por_codigo: list[dict], buscar_nombre) -> Match:
    """Regla: SKU exacto -> 'exacto'. Si no, el candidato más parecido -> 'dudoso'."""
    code_n = normalizar(codigo)
    for p in por_codigo:
        if normalizar(p.get("sku") or "") == code_n and code_n:
            return Match("exacto", 1.0, p)

    candidatos: list[tuple[float, dict, str]] = []
    for p in por_codigo:
        sku_n = normalizar(p.get("sku") or "")
        if sku_n and code_n and (code_n in sku_n or sku_n in code_n):
            score = 0.6 + 0.4 * _sim(codigo, p.get("sku") or "")
            candidatos.append((score, p, f"SKU parecido: Excel «{codigo}» vs web «{p.get('sku')}»"))
        elif nombre:
            candidatos.append((0.8 * _sim(nombre, p.get("name") or ""), p, "Coincidencia por nombre"))

    if not candidatos and nombre:
        for p in buscar_nombre() or []:
            candidatos.append((0.8 * _sim(nombre, p.get("name") or ""), p, "Encontrado buscando por nombre"))

    candidatos = [c for c in candidatos if c[0] >= 0.5]
    if not candidatos:
        return Match("ninguno", 0.0, None, f"El código «{codigo}» no existe en la tienda")
    score, prod, nota = max(candidatos, key=lambda c: c[0])
    return Match("dudoso", round(score, 2), prod, nota)


def datos_de_producto(p: dict) -> dict:
    """Campos de la Store API que guardamos."""
    prices = p.get("prices") or {}
    raw = prices.get("price")
    if (not raw or raw == "0") and prices.get("price_range"):
        raw = (prices["price_range"] or {}).get("min_amount")
    minor = prices.get("currency_minor_unit", 2)
    precio_dec = None
    if raw not in (None, ""):
        valor = Decimal(str(raw)) / (Decimal(10) ** int(minor or 0))
        precio_dec = f"{valor:.{int(minor or 0)}f}"
    descripcion = html_a_texto(p.get("description") or "") or html_a_texto(p.get("short_description") or "")
    return {
        "woo_id": p.get("id"),
        "woo_sku": p.get("sku") or "",
        "url": p.get("permalink") or "",
        "nombre_web": BeautifulSoup(p.get("name") or "", "html.parser").get_text(),
        "descripcion_web": descripcion,
        "categorias": [c.get("name") for c in p.get("categories") or [] if c.get("name")],
        "etiquetas": [t.get("name") for t in p.get("tags") or [] if t.get("name")],
        "precio_web": precio_dec,
        "precio_fb": precio_entero(raw, minor),
        "moneda": prices.get("currency_code") or "",
        "en_stock": 1 if p.get("is_in_stock") else 0,
        "_imagenes_url": [img.get("src") for img in p.get("images") or [] if img.get("src")],
    }


# ---------------------------------------------------------------- Imágenes

def a_jpg(data: bytes, max_lado: int) -> bytes:
    """Convierte a JPG RGB (fondo blanco si hay transparencia) y limita el lado mayor."""
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        fondo = Image.new("RGB", img.size, (255, 255, 255))
        fondo.paste(img, mask=img.split()[-1])
        img = fondo
    else:
        img = img.convert("RGB")
    img.thumbnail((max_lado, max_lado), Image.LANCZOS)
    quality = 90
    while True:
        out = io.BytesIO()
        img.save(out, "JPEG", quality=quality, optimize=True)
        if out.tell() <= 8 * 1024 * 1024 or quality <= 50:
            return out.getvalue()
        quality -= 10


@dataclass
class ResultadoImagenes:
    archivos: list[str] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)


def descargar_imagenes(woo: WooClient, urls: list[str], destino: Path, max_imagenes: int,
                       max_lado: int) -> ResultadoImagenes:
    destino.mkdir(parents=True, exist_ok=True)
    for viejo in destino.glob("*.jpg"):
        viejo.unlink()
    res = ResultadoImagenes()
    unicas = list(dict.fromkeys(urls))[:max_imagenes]
    for i, url in enumerate(unicas, start=1):
        try:
            jpg = a_jpg(woo.descargar(url), max_lado)
            path = destino / f"{i:02d}.jpg"
            path.write_bytes(jpg)
            res.archivos.append(path.name)
        except Exception as exc:  # noqa: BLE001 - una imagen rota no detiene el lote
            res.errores.append(f"{url}: {exc}")
    return res
