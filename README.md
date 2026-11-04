# Extractor Marketplace

Pasa productos de una tienda **WooCommerce** (por ejemplo mihaba.com) a **Facebook Marketplace** sin copiar a mano.

Subes un Excel con códigos y la app:
1. Busca cada código en la tienda (API pública de WooCommerce).
2. Descarga las fotos (JPG, máximo 10 por producto).
3. Calcula el precio: el de la web **redondeado hacia arriba** a entero (S/ 61.90 → 62).
4. Con IA (MiniMax, ChatGPT, Gemini…) escribe un título y una descripción listos para Marketplace.
5. Sugiere **etiquetas** de búsqueda (5 a 10) y usa el **SKU** de la web.
6. Te muestra una tarjeta por producto con botones **Copiar** y las fotos para subir.

## Requisitos
- [uv](https://docs.astral.sh/uv/) (instala Python y las dependencias solo).

## Arrancar
```bash
uv run marketplace
```
Abre **http://localhost:8000** en el navegador.

Opcional: `MKT_PORT=8080` cambia el puerto y `MKT_DATA_DIR=/ruta` cambia dónde se guardan la base de datos y las fotos (por defecto `./data`).

## Primer uso: Configuración
En **Configuración**:

| Campo | Ejemplo |
|---|---|
| URL de la tienda | `https://mihaba.com` |
| URL de la API de IA | `https://api.minimax.io/v1` |
| Modelo | `MiniMax-M2.7` |
| API key | tu clave |

Pulsa **Probar tienda** y **Probar conexión**, y luego **Guardar**.

Otros proveedores compatibles con OpenAI:

| Proveedor | URL de la API | Modelo (ejemplo) |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.5-flash` |
| OpenRouter | `https://openrouter.ai/api/v1` | `openai/gpt-4o-mini` |

La API key se guarda solo en tu PC (`data/app.db`) y nunca se envía al navegador. También puedes ponerla en un archivo `.env` como `AI_API_KEY=...`.
Si la IA no está configurada o falla, se usa el texto de la web limpio y el producto queda en **Revisar**.

## Uso diario
1. En **Lotes**, arrastra tu Excel (columnas `CÓDIGO` y `NOMBRE`) y pulsa **Procesar**.
   Un lote de 40 productos tarda unos 4–5 minutos; puedes ir publicando mientras termina.
2. Pulsa **Abrir Marketplace** (se abre en otra pestaña).
3. En cada tarjeta:
   - **Copiar** título, precio, categoría, SKU, etiquetas y descripción, y pégalos en Facebook.
     Las etiquetas se copian separadas por coma; en Facebook pega o escribe cada una y pulsa Enter.
   - **Descargar fotos** (zip) y súbelas, o arrastra las fotos de la tarjeta al formulario.
   - **Marcar publicado**: la tarjeta pasa a la pestaña *Publicados*.
4. Pestañas:
   - **Revisar**: el código del Excel no coincidía exacto con la web (por ejemplo `RT46F4U…` → `URT46F4U…`), faltan fotos o la IA falló. Confirma con **Sí, es el producto correcto** o corrige con **Editar**.
   - **No encontrados**: el código no existe en la web. Escribe el código correcto y pulsa **Buscar de nuevo**.

Otros botones: **Editar** (cambia título, precio, categoría, SKU, etiquetas o descripción) y **Regenerar texto IA** (nueva versión del texto).

## Estilo del texto
Sin emojis, sin enlaces y sin precio en el texto. Título de 100 caracteres como máximo, con lo importante al inicio. Descripción con estas partes: beneficio, *Características*, *Especificaciones* y cierre "Producto nuevo y original…". En Configuración puedes añadir un texto fijo al final (zona de entrega, horario, etc.).

## Desarrollo
```bash
uv run pytest
```
Estructura:
- `app/extractor.py`: Excel, WooCommerce Store API (con límite de velocidad, porque Cloudflare devuelve 429), precio, fotos.
- `app/ai.py`: cliente compatible con OpenAI, prompt, limpieza y validación (quita `<think>`, emojis, URLs y otros idiomas).
- `app/pipeline.py`: procesamiento en segundo plano del lote.
- `app/main.py`, `app/templates/`, `app/static/`: la app web.
