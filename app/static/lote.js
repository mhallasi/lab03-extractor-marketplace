(() => {
  const lista = document.getElementById('lista');
  const loteId = lista.dataset.lote;
  const fbUrl = lista.dataset.fb;
  let filtro = 'pendientes';
  let productos = [];
  let editando = new Set();   // tarjetas con edición abierta: no re-renderizar
  let timer = null;

  const ESTADOS = {
    pendiente: 'En cola', procesando: 'Procesando…', listo: 'Listo', revisar: 'Revisar',
    no_encontrado: 'No encontrado', error: 'Error', cancelado: 'Cancelado',
  };

  const visible = p => {
    switch (filtro) {
      case 'pendientes': return !p.publicado && p.status !== 'no_encontrado';
      case 'revisar': return !p.publicado && ['revisar', 'error', 'cancelado'].includes(p.status);
      case 'no_encontrado': return p.status === 'no_encontrado';
      case 'publicados': return !!p.publicado;
      default: return true;
    }
  };

  function campo(p, key, etiqueta, valor, multiline = false) {
    const id = `${key}-${p.id}`;
    const input = multiline
      ? `<textarea id="${id}" data-key="${key}" rows="10" readonly>${esc(valor)}</textarea>`
      : `<input id="${id}" data-key="${key}" value="${esc(valor)}" readonly>`;
    let contador = '';
    if (key === 'titulo_fb') contador = `<span class="count">${(valor || '').length}/100</span>`;
    if (key === 'etiquetas_fb') contador = `<span class="count">${contarEtiquetas(valor)} etiquetas · separadas por coma</span>`;
    return `<div class="campo">
      <div class="campo-head"><label for="${id}">${etiqueta}</label>${contador}
        <button class="btn small copy" data-copy="${id}">Copiar</button></div>
      ${input}</div>`;
  }

  function tarjeta(p) {
    const procesando = p.status === 'pendiente' || p.status === 'procesando';
    const encontrado = !!p.nombre_web;
    const fotos = (p.imagenes_url || []).map((u, i) =>
      `<a href="${u}" target="_blank" title="Arrastra esta foto al formulario de Facebook">
         <img src="${u}" alt="Foto ${i + 1}" loading="lazy" draggable="true"></a>`).join('');
    const nota = p.nota ? `<div class="nota">${esc(p.nota)}</div>` : '';
    const header = `
      <div class="card-head">
        <div>
          <span class="badge ${p.status}">${ESTADOS[p.status] || p.status}</span>
          ${p.publicado ? '<span class="badge publicado">Publicado</span>' : ''}
          ${p.texto_origen === 'ia' ? '<span class="badge ia">Texto IA</span>' : ''}
          <span class="codigo">${esc(p.codigo)}</span>
          ${p.woo_sku && p.woo_sku !== p.codigo ? `<span class="muted">(web: ${esc(p.woo_sku)})</span>` : ''}
        </div>
        ${p.url ? `<a class="muted small" href="${esc(p.url)}" target="_blank" rel="noopener">Ver en la web ↗</a>` : ''}
      </div>`;

    if (procesando) {
      return `<article class="card" data-id="${p.id}">${header}
        <div class="muted">${esc(p.nombre_excel)}</div><div class="spinner"></div></article>`;
    }
    if (p.status === 'cancelado' && !encontrado) {
      return `<article class="card" data-id="${p.id}">${header}
        <div><b>${esc(p.nombre_excel)}</b></div>${nota}</article>`;
    }
    if (!encontrado) {
      return `<article class="card" data-id="${p.id}">${header}
        <div><b>${esc(p.nombre_excel)}</b></div>${nota}
        <div class="row">
          <input class="codigo-nuevo" placeholder="Código correcto (SKU de la web)" value="${esc(p.codigo)}">
          <button class="btn small" data-accion="reprocesar">Buscar de nuevo</button>
        </div></article>`;
    }

    const confirmar = p.status === 'revisar' && (p.match_tipo === 'dudoso')
      ? `<button class="btn small warn" data-accion="confirmar">Sí, es el producto correcto</button>` : '';

    return `<article class="card ${p.publicado ? 'hecho' : ''}" data-id="${p.id}">
      ${header}
      ${nota}
      <div class="card-body">
        <div class="fotos">
          <div class="galeria">${fotos || '<div class="muted">Sin fotos</div>'}</div>
          ${fotos ? `<a class="btn small" href="/imagenes/${p.id}.zip">Descargar fotos (${p.imagenes_url.length})</a>` : ''}
        </div>
        <div class="campos">
          ${campo(p, 'titulo_fb', 'Título', p.titulo_fb)}
          <div class="grid3">
            ${campo(p, 'precio_fb', 'Precio (S/)', p.precio_fb ?? '')}
            <div class="campo"><div class="campo-head"><label>Condición</label></div><input value="${esc(p.condicion)}" readonly></div>
            ${campo(p, 'categoria_fb', 'Categoría sugerida', p.categoria_fb)}
          </div>
          <div class="grid-sku">
            ${campo(p, 'sku_fb', 'SKU', p.sku_fb)}
            ${campo(p, 'etiquetas_fb', 'Etiquetas', (p.etiquetas_fb || []).join(', '))}
          </div>
          ${campo(p, 'descripcion_final', 'Descripción', p.descripcion_final, true)}
          <div class="muted small">Web: ${esc(p.nombre_web)} · S/ ${esc(p.precio_web)} ${p.en_stock ? '' : '· <b>sin stock</b>'}
            · ${esc((p.categorias || []).join(', '))}</div>
        </div>
      </div>
      <div class="card-actions">
        ${confirmar}
        <button class="btn small ghost" data-accion="editar">Editar</button>
        <button class="btn small ghost" data-accion="guardar" hidden>Guardar cambios</button>
        <button class="btn small ghost" data-accion="regenerar">Regenerar texto IA</button>
        <a class="btn small ghost" href="${fbUrl}" target="fb-marketplace" rel="noopener">Abrir Marketplace ↗</a>
        <button class="btn small ${p.publicado ? 'ghost' : 'primary'}" data-accion="publicado">
          ${p.publicado ? 'Desmarcar publicado' : '✓ Marcar publicado'}</button>
      </div>
    </article>`;
  }

  function render() {
    // Conserva las tarjetas que se están editando.
    const abiertas = {};
    editando.forEach(id => { const el = lista.querySelector(`.card[data-id="${id}"]`); if (el) abiertas[id] = el; });
    const vis = productos.filter(visible);
    if (!vis.length) {
      lista.innerHTML = `<div class="panel muted center">No hay productos en esta vista.</div>`;
      return;
    }
    lista.innerHTML = vis.map(p => abiertas[p.id] ? `<div data-keep="${p.id}"></div>` : tarjeta(p)).join('');
    lista.querySelectorAll('[data-keep]').forEach(ph => ph.replaceWith(abiertas[ph.dataset.keep]));
  }

  function resumen(r) {
    const pct = r.total ? Math.round(100 * r.terminados / r.total) : 0;
    document.getElementById('bar').style.width = pct + '%';
    const enCurso = r.terminados < r.total;
    document.getElementById('resumen').innerHTML =
      (enCurso ? `<b>Procesando ${r.terminados}/${r.total}…</b> · ` : `<b>${r.total} productos</b> · `) +
      `${r.listo} listos · ${r.revisar} por revisar · ${r.no_encontrado} no encontrados` +
      (r.error ? ` · ${r.error} con error` : '') +
      (r.cancelado ? ` · <b>${r.cancelado} cancelados</b> (la app se cerró antes de terminar)` : '') +
      ` · <b>${r.publicado} publicados</b>`;
    const pendientesDeReanudar = (r.error || 0) + (r.cancelado || 0);
    const btn = document.getElementById('btn-reanudar');
    btn.hidden = !pendientesDeReanudar || enCurso;
    btn.textContent = r.cancelado ? `Reanudar (${pendientesDeReanudar})` : `Reintentar errores (${r.error})`;
    return enCurso;
  }

  async function cargar() {
    try {
      const data = await api(`/api/lotes/${loteId}`);
      productos = data.productos;
      const enCurso = resumen(data.resumen);
      render();
      clearTimeout(timer);
      if (enCurso) timer = setTimeout(cargar, 2000);
    } catch (e) {
      toast('Error cargando el lote: ' + e.message, 'err');
      timer = setTimeout(cargar, 5000);
    }
  }

  function actualizar(p) {
    const i = productos.findIndex(x => x.id === p.id);
    if (i >= 0) productos[i] = p;
    editando.delete(p.id);
    render();
  }

  document.getElementById('filtros').addEventListener('click', ev => {
    const b = ev.target.closest('button[data-f]');
    if (!b) return;
    filtro = b.dataset.f;
    document.querySelectorAll('#filtros button').forEach(x => x.classList.toggle('activo', x === b));
    editando.clear();
    render();
  });

  document.getElementById('btn-reanudar').addEventListener('click', async ev => {
    ev.target.disabled = true;
    try {
      const r = await api(`/api/lotes/${loteId}/reanudar`, { method: 'POST' });
      toast(`Reanudando ${r.reanudados} producto(s)…`);
      cargar();
    } catch (e) { toast(e.message, 'err'); }
    ev.target.disabled = false;
  });

  lista.addEventListener('click', async ev => {
    const copy = ev.target.closest('[data-copy]');
    if (copy) {
      const el = document.getElementById(copy.dataset.copy);
      await copiarTexto(el.value);
      copy.textContent = '¡Copiado!'; copy.classList.add('copiado');
      setTimeout(() => { copy.textContent = 'Copiar'; copy.classList.remove('copiado'); }, 1500);
      return;
    }
    const btn = ev.target.closest('[data-accion]');
    if (!btn) return;
    const card = btn.closest('.card');
    const id = Number(card.dataset.id);
    const accion = btn.dataset.accion;
    btn.disabled = true;
    try {
      if (accion === 'editar') {
        editando.add(id);
        card.classList.add('editando');
        card.querySelectorAll('[data-key]').forEach(el => { el.readOnly = false; });
        card.querySelector('[data-accion="guardar"]').hidden = false;
        btn.hidden = true;
        const d = card.querySelector('[data-key="descripcion_final"]');
        const p = productos.find(x => x.id === id);
        if (d && p) d.value = p.descripcion_fb || '';  // se edita sin el pie fijo
      } else if (accion === 'guardar') {
        const body = {};
        card.querySelectorAll('[data-key]').forEach(el => {
          const k = el.dataset.key === 'descripcion_final' ? 'descripcion_fb' : el.dataset.key;
          body[k] = el.value;
        });
        actualizar(await api(`/api/productos/${id}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        }));
        toast('Cambios guardados');
      } else if (accion === 'confirmar') {
        actualizar(await api(`/api/productos/${id}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirmar: true }),
        }));
        toast('Producto confirmado');
      } else if (accion === 'regenerar') {
        btn.textContent = 'Generando…';
        actualizar(await api(`/api/productos/${id}/regenerar`, { method: 'POST' }));
        toast('Texto regenerado');
      } else if (accion === 'publicado') {
        const p = productos.find(x => x.id === id);
        actualizar(await api(`/api/productos/${id}/publicado`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ publicado: !p.publicado }),
        }));
        const r = await api(`/api/lotes/${loteId}`); resumen(r.resumen);
        toast(p.publicado ? 'Desmarcado' : '¡Publicado! Siguiente producto');
      } else if (accion === 'reprocesar') {
        const codigo = card.querySelector('.codigo-nuevo').value.trim();
        await api(`/api/productos/${id}/reprocesar`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ codigo }),
        });
        filtro = 'todos';
        document.querySelectorAll('#filtros button').forEach(x => x.classList.toggle('activo', x.dataset.f === 'todos'));
        toast('Buscando de nuevo…');
        setTimeout(cargar, 800);
      }
    } catch (e) {
      toast(e.message, 'err');
      btn.disabled = false;
      if (accion === 'regenerar') btn.textContent = 'Regenerar texto IA';
    }
  });

  function contarEtiquetas(texto) {
    return String(texto || '').split(',').map(s => s.trim()).filter(Boolean).length;
  }

  // Actualiza los contadores mientras se edita.
  lista.addEventListener('input', ev => {
    if (ev.target.dataset.key === 'etiquetas_fb') {
      const c = ev.target.closest('.campo').querySelector('.count');
      c.textContent = `${contarEtiquetas(ev.target.value)} etiquetas · separadas por coma`;
      return;
    }
    if (ev.target.dataset.key === 'titulo_fb') {
      const c = ev.target.closest('.campo').querySelector('.count');
      c.textContent = `${ev.target.value.length}/100`;
      c.classList.toggle('over', ev.target.value.length > 100);
    }
  });

  cargar();
})();
