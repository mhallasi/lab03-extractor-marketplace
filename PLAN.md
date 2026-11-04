# Extractor Marketplace — Plan

## Objetivo
Pasar de "copiar a mano desde mihaba.com a Facebook Marketplace" a "subir el Excel y publicar con tarjetas de copiado".

## Cómo se usa (una sola app web local)
1. **Configuración** (una vez): URL de la tienda, URL de la IA, API key, modelo → [Probar conexión].
2. **Subir el Excel** → la app busca cada código en la tienda, descarga las imágenes y la IA reescribe los textos.
3. **Publicar**: una tarjeta por producto con botones [Copiar] y fotos para arrastrar → [Marcar como publicado].

## Decisiones
| Tema | Decisión |
|---|---|
| Publicación | Manual asistida con tarjetas de copiado (Marketplace no tiene API de publicación) |
| Datos | WooCommerce Store API pública (`/wp-json/wc/store/v1/products?search=<SKU>`), sin IA |
| IA | Cualquier API compatible con OpenAI (base_url + api_key + modelo). Por defecto: MiniMax |
| Rol de la IA | Solo reescribe el título, la descripción y la categoría sugerida. Nunca toca el precio ni las imágenes |
| Precio | El de la web, **redondeado hacia arriba a entero** (61.90 → 62) |
| Condición | Siempre "Nuevo" |
| Estilo | Sin emojis, orientado a intención de compra (ver abajo) |
| Intermedio .zip | Descartado: todo ocurre dentro de la app |

## Guía de estilo para la IA
- **Título (≤100 caracteres, lo importante en los primeros ~40)**: Producto + Marca + Modelo + atributo clave, con las palabras que la gente busca.
  Ej.: `Linterna LED Táctica Recargable OPALUX 25W 2000 Lúmenes OP-8085`
- **Descripción en texto plano**:
  1. Una línea de beneficio (para qué sirve y para quién).
  2. Características clave en viñetas con "-" (4 a 6).
  3. Especificaciones técnicas (sacadas de la tabla de la web).
  4. "Producto nuevo y original." más un cierre que invite a escribir ("Escríbenos para consultar disponibilidad y entrega.").
- **Prohibido**: emojis, MAYÚSCULAS sostenidas, enlaces o URLs (Facebook puede rechazar el anuncio), inventar datos que no estén en la web, mencionar el precio en el texto.
- **Pie de texto fijo opcional** configurable (vacío por defecto).
- Salida en JSON validado; se eliminan bloques de razonamiento (`<think>`). Si falla → texto original limpio + marca "revisar".

## Arquitectura
- Backend: Python + FastAPI (con `uv`), SQLite para el estado (configuración, productos, publicado sí/no).
- Frontend: HTML + HTMX/Alpine (sin build), servido por FastAPI.
- La API key se guarda solo localmente, nunca se envía al navegador; las llamadas a la IA las hace el backend.
- Imágenes: se descargan en `data/imagenes/<SKU>/`, se convierten a JPG y se limitan a 10 por producto.

## Fases
1. **Base**: proyecto `uv`, modelos, configuración y SQLite, pantalla de Configuración con prueba de conexión.
2. **Extractor**: lectura del Excel (columnas CÓDIGO/NOMBRE), búsqueda exacta por SKU con respaldo aproximado (marcado "dudoso"), descarga de imágenes en paralelo con reintentos, precio redondeado. Tests con respuestas grabadas.
3. **IA**: cliente compatible con OpenAI, prompt con la guía de estilo, validación, caché por producto, botón "Regenerar".
4. **Tarjetas**: lista con filtros (pendiente/publicado/revisar), edición, botones copiar, galería arrastrable y descarga de fotos.
5. **Pulido**: reporte de no encontrados, progreso en vivo, documentación de uso.

## Datos verificados (2026-11-05)
- Store API pública activa; 2685 productos. Los precios vienen en céntimos (`6190` = S/ 61.90).
- Excel: 40 productos; 37 coinciden exacto por SKU.
- `55481`, `55470`: no existen en la web → "no encontrado".
- `RT46F4U#01M4B2` → en la web es `URT46F4U#01M4B2` → "dudoso", para confirmar en la tarjeta.
