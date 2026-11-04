"""Cliente de IA agnóstico (cualquier API compatible con OpenAI: MiniMax, OpenAI, Gemini, etc.)."""

from __future__ import annotations

import hashlib
import json
import re

import httpx

from . import db

PROMPT_VERSION = "v2"

SYSTEM_PROMPT = """Eres un redactor experto en anuncios de Facebook Marketplace en Perú.
Recibes los datos reales de un producto de una tienda y escribes el anuncio.

Reglas obligatorias:
- Escribe TODO en español neutro, claro y profesional, orientado a la intención de compra.
  Nunca mezcles otros idiomas (ni inglés ni chino) salvo marcas, modelos y siglas técnicas.
- NO uses emojis ni símbolos decorativos. NO escribas frases enteras en MAYÚSCULAS.
- NO incluyas enlaces, URLs, teléfonos, correos ni el nombre de la tienda.
- NO menciones el precio ni descuentos.
- NO inventes datos: usa solo la información recibida. Si un dato no está, no lo pongas.
- Conserva marca y modelo/código tal como vienen.

Título (máximo 100 caracteres, lo más importante en los primeros 40):
  Tipo de producto + característica clave + Marca + Modelo. Usa palabras que la gente busca.
  Ejemplo: "Linterna LED Táctica Recargable OPALUX 25W 2000 Lúmenes OP-8085"

Descripción (texto plano, viñetas con "- "):
  1) Una o dos líneas de beneficio: qué es, para qué sirve y para quién.
  2) Línea "Características:" y de 3 a 6 viñetas con lo más valioso para el comprador.
  3) Línea "Especificaciones:" y viñetas "Clave: Valor" con los datos técnicos disponibles.
  4) Cierre: "Producto nuevo y original. Escríbenos para consultar disponibilidad y entrega."
  Máximo 1500 caracteres.

Categoría: sugiere la categoría de Facebook Marketplace más adecuada, en español,
con formato "Categoría > Subcategoría" (por ejemplo "Hogar y jardín > Iluminación",
"Electrónica > Audio", "Herramientas", "Juguetes y juegos").

Responde ÚNICAMENTE con un objeto JSON válido, sin texto adicional:
{"titulo": "...", "descripcion": "...", "categoria": "..."}"""

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF\U00002B00-\U00002BFF\uFE0F\u200d]"
)
# Chino/japonés/coreano: algunos modelos filtran palabras sueltas de su razonamiento.
CJK_RE = re.compile("[\u3000-\u303F\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uAC00-\uD7AF\uFF00-\uFFEF]")
URL_RE = re.compile(r"(https?://\S+|www\.\S+)", re.I)


class AIError(Exception):
    pass


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def chat(base_url: str, api_key: str, model: str, messages: list[dict], max_tokens: int = 4000,
         timeout: float = 120, client: httpx.Client | None = None) -> str:
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.4}
    http = client or httpx.Client(timeout=timeout)
    try:
        resp = http.post(f"{base_url.rstrip('/')}/chat/completions", json=payload, headers=_headers(api_key))
    except httpx.HTTPError as exc:
        raise AIError(f"No se pudo conectar con la IA: {exc}") from exc
    finally:
        if client is None:
            http.close()
    try:
        data = resp.json()
    except ValueError as exc:
        raise AIError(f"Respuesta no válida de la IA (HTTP {resp.status_code})") from exc
    if resp.status_code >= 400 or "error" in data and not data.get("choices"):
        err = data.get("error") if isinstance(data, dict) else None
        msg = err.get("message") if isinstance(err, dict) else str(err or data)
        raise AIError(f"La IA respondió con error (HTTP {resp.status_code}): {msg}")
    base_resp = data.get("base_resp") or {}
    if base_resp.get("status_code") not in (None, 0):
        raise AIError(f"La IA respondió con error: {base_resp.get('status_msg')}")
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError("La IA no devolvió contenido") from exc


def quitar_razonamiento(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    text = re.sub(r"^.*?</think>", "", text, flags=re.S | re.I)  # <think> sin apertura
    return text.strip()


def extraer_json(text: str) -> dict:
    text = quitar_razonamiento(text)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise AIError("La IA no devolvió un JSON válido")


def limpiar_texto(text: str) -> str:
    text = EMOJI_RE.sub("", text or "")
    text = URL_RE.sub("", text)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"^\s*[•*·▪►✓✔]\s*", "- ", text, flags=re.M)
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def recortar_titulo(titulo: str, limite: int = 100) -> str:
    titulo = re.sub(r"\s+", " ", limpiar_texto(titulo)).strip(" -|,.")
    if len(titulo) <= limite:
        return titulo
    corte = titulo[:limite].rsplit(" ", 1)[0]
    return corte.strip(" -|,.")


def validar(data: dict) -> dict:
    if not isinstance(data, dict):
        raise AIError("Formato inesperado de la IA")
    titulo = recortar_titulo(str(data.get("titulo") or ""))
    descripcion = limpiar_texto(str(data.get("descripcion") or ""))
    categoria = re.sub(r"\s+", " ", limpiar_texto(str(data.get("categoria") or ""))).strip()
    if CJK_RE.search(titulo + descripcion + categoria):
        raise AIError("La IA mezcló otro idioma en el texto")
    if len(titulo) < 5:
        raise AIError("La IA devolvió un título vacío")
    if len(descripcion) < 20:
        raise AIError("La IA devolvió una descripción vacía")
    return {"titulo": titulo, "descripcion": descripcion[:5000], "categoria": categoria[:120]}


def construir_mensaje(producto: dict) -> str:
    partes = [
        f"Nombre en la tienda: {producto.get('nombre_web') or producto.get('nombre_excel')}",
        f"Código/SKU: {producto.get('woo_sku') or producto.get('codigo')}",
        f"Categorías en la tienda: {', '.join(producto.get('categorias') or []) or '-'}",
    ]
    if producto.get("etiquetas"):
        partes.append(f"Etiquetas: {', '.join(producto['etiquetas'])}")
    partes.append("Descripción de la tienda:\n" + (producto.get("descripcion_web") or "(sin descripción)")[:6000])
    return "\n".join(partes)


def _cache_key(model: str, mensaje: str) -> str:
    return hashlib.sha256(f"{PROMPT_VERSION}|{model}|{mensaje}".encode()).hexdigest()


def generar_anuncio(producto: dict, base_url: str, api_key: str, model: str, usar_cache: bool = True,
                    client: httpx.Client | None = None) -> dict:
    mensaje = construir_mensaje(producto)
    key = _cache_key(model, mensaje)
    if usar_cache:
        with db.conn() as c:
            row = c.execute("SELECT respuesta FROM ai_cache WHERE clave = ?", (key,)).fetchone()
        if row:
            return json.loads(row["respuesta"])

    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": mensaje}]
    ultimo: Exception | None = None
    for _ in range(3):
        try:
            raw = chat(base_url, api_key, model, messages, client=client)
            result = validar(extraer_json(raw))
            break
        except AIError as exc:
            ultimo = exc
            if "HTTP 4" in str(exc) and "429" not in str(exc):
                raise
    else:
        raise ultimo or AIError("Fallo desconocido de la IA")

    with db.conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO ai_cache(clave, respuesta) VALUES(?, ?)",
            (key, json.dumps(result, ensure_ascii=False)),
        )
    return result


def anuncio_sin_ia(producto: dict) -> dict:
    """Respaldo cuando la IA falla o no está configurada: texto de la web limpio."""
    titulo = recortar_titulo(producto.get("nombre_web") or producto.get("nombre_excel") or producto.get("codigo"))
    titulo = titulo.replace("«", "").replace("»", "")
    descripcion = (producto.get("descripcion_web") or "").strip()
    cierre = "Producto nuevo y original. Escríbenos para consultar disponibilidad y entrega."
    descripcion = f"{descripcion}\n\n{cierre}" if descripcion else cierre
    categoria = " > ".join((producto.get("categorias") or [])[:1])
    return {"titulo": titulo, "descripcion": limpiar_texto(descripcion), "categoria": categoria}


def probar_conexion(base_url: str, api_key: str, model: str) -> str:
    raw = chat(base_url, api_key, model,
               [{"role": "user", "content": 'Responde solo con este JSON: {"ok": true}'}],
               max_tokens=300, timeout=60)
    return quitar_razonamiento(raw)[:200]
