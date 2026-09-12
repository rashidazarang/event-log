/* =================================================================
   Event Log — interfaz. Un solo vocabulario de filtro para la persona y
   para el bus: lo que ves aquí es lo que un monitor puede pedir.
   ================================================================= */
(function () {
'use strict';
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => [...(r || document).querySelectorAll(s)];
const app = $('#app');

const S = {
  view: 'bandeja', boot: null,
  f: { space: [], status: [], kind: [], assignee: [], labels_any: [], text: '', abiertos: false },
  feed: { cursor: 0, live: false, timer: null, events: [], filtro: {}, fam: 'entrada.*' },
  almacen: { domain: 'email', action: [], text: '' },
  drawerTab: 'ficha',
};

/* ------------------------------------------------------------ utils --- */
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const num = n => (n || 0).toLocaleString('es-MX');
const slug = s => String(s || '').replace(/\s+/g, '');
function toast(m) { const t = $('#toast'); t.textContent = m; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => t.hidden = true, 2400); }

function rel(iso) {
  if (!iso) return '—';
  const d = new Date(iso), s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return 'ahora';
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`;
  if (s < 86400 * 30) return `hace ${Math.floor(s / 86400)} d`;
  return d.toLocaleDateString('es-MX', { day: 'numeric', month: 'short' });
}
const actor = k => (S.boot.actors.find(a => a.key === k)) ||
  { key: k, name: k || 'sin asignar', initials: (k || '··').slice(0, 2).toUpperCase(), color: '#5d6b82' };
const space = k => (S.boot.spaces.find(s => s.key === k)) ||
  { key: k, name: k, color: '#5d6b82', schema_json: { campos: [] } };
function av(k, extra) {
  const a = actor(k);
  return `<span class="avatar" style="--a:${a.color};${extra || ''}" title="${esc(a.name)}">${esc(a.initials)}</span>`;
}
const pillEstado = s => `<span class="pill st-${slug(s)}">${esc(s)}</span>`;
const pillPrio = p => `<span class="pr ${p}">${p}</span>`;

function qs(extra) {
  const p = new URLSearchParams();
  ['space', 'status', 'kind', 'assignee', 'labels_any'].forEach(k =>
    S.f[k].forEach(v => p.append(k, v)));
  if (S.f.text) p.set('text', S.f.text);
  if (S.f.abiertos) p.set('abiertos', '1');
  for (const k in (extra || {})) if (extra[k] != null) p.set(k, extra[k]);
  return p.toString();
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  let j = {};
  try { j = await r.json(); } catch (e) { throw new Error('respuesta no-JSON (' + r.status + ')'); }
  if (j && j.error) { const e = new Error(j.error); e.body = j; e.status = r.status; throw e; }
  return j;
}
const post = (p, b) => api(p, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify(b || {}) });
const patch = (p, b) => api(p, { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                                 body: JSON.stringify(b || {}) });

function jsonHTML(o) {
  const s = JSON.stringify(o, null, 2) || '';
  return esc(s)
    .replace(/&quot;([^&]+)&quot;(\s*:)/g, '<span class="k">"$1"</span>$2')
    .replace(/: &quot;([^&]*)&quot;/g, ': <span class="s">"$1"</span>')
    .replace(/: (-?\d+\.?\d*)/g, ': <span class="n">$1</span>');
}

/* -------------------------------------------------------- spacebar --- */
function renderSpacebar() {
  const f = S.boot.facetas;
  const cnt = k => (f.space.find(x => x.value === k) || {}).n || 0;
  $('#spacebar').innerHTML = `
    <span class="fb-label">Espacio</span>
    ${S.boot.spaces.map(s => `
      <button class="sp ${S.f.space.includes(s.key) ? 'on' : ''}" style="--c:${s.color}"
        data-sp="${s.key}"><i></i>${esc(s.name)}<span class="n">${cnt(s.key)}</span></button>`).join('')}
    <div class="fb-more">
      <button class="chip ${S.f.abiertos ? 'on' : ''}" id="soloAbiertas">sólo abiertas</button>
      <select class="sel" id="selResp">
        <option value="">Cualquier responsable</option>
        <option value="__sin">Sin asignar</option>
        ${S.boot.actors.filter(a => a.kind !== 'sistema').map(a =>
          `<option value="${a.key}" ${S.f.assignee.includes(a.key) ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}
      </select>
      ${activos() ? `<span class="chip-clear" id="limpiar">✕ limpiar (${activos()})</span>` : ''}
    </div>`;
  $$('.sp[data-sp]').forEach(b => b.onclick = () => {
    const k = b.dataset.sp, i = S.f.space.indexOf(k);
    i < 0 ? S.f.space.push(k) : S.f.space.splice(i, 1);
    renderSpacebar(); render();
  });
  $('#soloAbiertas').onclick = () => { S.f.abiertos = !S.f.abiertos; renderSpacebar(); render(); };
  $('#selResp').onchange = e => {
    S.f.assignee = e.target.value ? [e.target.value] : [];
    render();
  };
  const l = $('#limpiar');
  if (l) l.onclick = () => {
    S.f = { space: [], status: [], kind: [], assignee: [], labels_any: [], text: '', abiertos: false };
    $('#q').value = ''; renderSpacebar(); render();
  };
}
const activos = () => S.f.space.length + S.f.status.length + S.f.kind.length +
  S.f.assignee.length + S.f.labels_any.length + (S.f.text ? 1 : 0) + (S.f.abiertos ? 1 : 0);

/* ========================================================= BANDEJA === */
async function viewBandeja() {
  const [d, st] = await Promise.all([api('/api/entries?' + qs({ per: 60 })),
                                     api('/api/stats?' + qs())]);
  app.innerHTML = `
    <div class="toolbar">
      <div class="seg" id="segTipo">
        ${[['', 'Todo'], ['ticket', 'Tickets'], ['nota', 'Notas']].map(([v, l]) =>
          `<button data-k="${v}" class="${(S.f.kind[0] || '') === v ? 'on' : ''}">${l}</button>`).join('')}
      </div>
      <div class="seg" id="segEstado">
        ${['', ...S.boot.status].map(v =>
          `<button data-s="${v}" class="${(S.f.status[0] || '') === v ? 'on' : ''}">${v || 'Cualquier estado'}</button>`).join('')}
      </div>
      <span class="spacer"></span>
      <span class="muted">${num(d.total)} entradas</span>
      <a class="btn" href="/api/export/facts.csv" title="señales normalizadas">↓ señales</a>
    </div>
    <div class="split">
      <div class="panel">
        <div class="panel-hd"><h2>Entradas <span class="count">(${num(d.total)})</span></h2>
          <div class="right"><span class="sub">orden: última actualización</span></div></div>
        <div class="stream">${d.items.map(filaEntrada).join('') ||
          '<div class="empty">Nada cumple estos filtros.</div>'}</div>
      </div>
      <div>
        ${panelKPI(st)}
        ${panelPendientes(d.items)}
      </div>
    </div>`;
  wireFilas();
  $$('#segTipo button').forEach(b => b.onclick = () => {
    S.f.kind = b.dataset.k ? [b.dataset.k] : []; render();
  });
  $$('#segEstado button').forEach(b => b.onclick = () => {
    S.f.status = b.dataset.s ? [b.dataset.s] : []; render();
  });
  Charts.area($('#actSerie'), (st.serie || []).map(x => ({
    label: x.d.slice(8), date: x.d, value: x.n })), { unit: 'eventos' });
}

function filaEntrada(e) {
  const sp = space(e.space);
  const g = e.kind === 'nota' ? 'NT' : (e.priority || 'P2');
  return `<div class="row-e" data-g="${e.genesis}">
    <span class="glyph" style="--c:${sp.color}">${g}</span>
    <div class="mid">
      <h3>${esc(e.title)}</h3>
      <div class="sub">
        ${pillEstado(e.status)}
        ${e.kind === 'ticket' ? pillPrio(e.priority) : '<span class="pill plain">nota</span>'}
        <span style="color:${sp.color}">${esc(sp.name)}</span>
        ${e.labels.map(l => `<span class="lab" data-lab="${esc(l)}">${esc(l)}</span>`).join('')}
        ${e.blocked_by ? `<span class="muted">· se espera a ${esc(e.blocked_by)}</span>` : ''}
        ${e.claimed_by ? `<span class="claimed">${esc(e.claimed_by)}</span>` : ''}
      </div>
    </div>
    <div class="end">
      <span class="gen" data-copy="${e.genesis}">${e.genesis}</span>
      ${av(e.assignee)}
      <span class="muted mono" style="width:64px;text-align:right">${rel(e.updated)}</span>
    </div>
  </div>`;
}
function wireFilas() {
  $$('.row-e').forEach(r => r.onclick = ev => {
    if (ev.target.dataset.copy) {
      navigator.clipboard && navigator.clipboard.writeText(ev.target.dataset.copy);
      ev.target.classList.add('copiado'); toast('Clave copiada');
      setTimeout(() => ev.target.classList.remove('copiado'), 900);
      return;
    }
    if (ev.target.dataset.lab) {
      S.f.labels_any = [ev.target.dataset.lab]; render(); return;
    }
    abrir(r.dataset.g);
  });
}
function panelKPI(st) {
  return `<div class="panel" style="margin-bottom:14px">
    <div class="panel-hd"><h2>Estado</h2></div>
    <div class="panel-bd">
      <div class="kpis" style="margin:0;grid-template-columns:1fr 1fr">
        <div><div class="l">Abiertas</div><div class="v">${num(st.abiertas)}</div></div>
        <div><div class="l">Sin asignar</div><div class="v" style="color:${st.sinAsignar ? 'var(--amber)' : 'inherit'}">${num(st.sinAsignar)}</div></div>
        <div><div class="l">Reclamadas</div><div class="v">${num(st.reclamadas)}</div></div>
        <div><div class="l">Eventos</div><div class="v">${num(st.eventos)}</div></div>
      </div>
      <div style="height:88px;margin-top:12px" id="actSerie"></div>
      <div class="muted" style="font-size:11px;text-align:center">actividad del log · 21 días</div>
    </div>
  </div>`;
}
function panelPendientes(items) {
  const sin = items.filter(i => !i.assignee && i.kind === 'ticket' &&
                                S.boot.abiertos.includes(i.status)).slice(0, 6);
  return `<div class="panel">
    <div class="panel-hd"><h2>Esperando dueño <span class="count">(${sin.length})</span></h2></div>
    ${sin.length ? sin.map(e => `<div class="row-e" data-g="${e.genesis}" style="padding:10px 14px">
      <div class="mid"><h3 style="font-size:12.5px">${esc(e.title)}</h3>
      <div class="sub">${pillPrio(e.priority)}<span>${esc(space(e.space).name)}</span>
      <span class="muted">pedido por ${esc(actor(e.requester).name)}</span></div></div>
    </div>`).join('') : '<div class="empty" style="padding:22px">Todo tiene dueño.</div>'}
  </div>`;
}

/* ========================================================= TABLERO === */
async function viewTablero() {
  const d = await api('/api/board?' + qs());
  app.innerHTML = `
    <div class="toolbar"><span class="muted">${num(d.total)} tickets · arrastra para cambiar de estado</span></div>
    <div class="board">${d.columns.map((c, i) => `
      <div class="bcol" data-status="${c.status}" style="animation-delay:${i * 40}ms">
        <div class="bcol-hd"><h3><span class="pill st-${slug(c.status)}" style="height:auto;padding:0;background:none"></span>
          ${esc(c.status)}<b>${num(c.count)}</b></h3></div>
        <div class="bcol-bd">
          ${c.items.map(e => `<div class="bcard" data-g="${e.genesis}">
            <h4>${esc(e.title)}</h4>
            <div class="meta">${pillPrio(e.priority)}${av(e.assignee)}
              <span style="color:${space(e.space).color}">${esc(space(e.space).name)}</span>
              ${e.claimed_by ? `<span class="claimed">${esc(e.claimed_by)}</span>` : ''}</div>
          </div>`).join('')}
          ${c.count > c.items.length ? `<div class="col-more">+ ${c.count - c.items.length} más</div>` : ''}
          ${c.count ? '' : '<div class="empty" style="padding:18px;font-size:12px">vacío</div>'}
        </div>
      </div>`).join('')}</div>
    <div class="provenance"><span class="dot-i"></span>
      <span>Mover una tarjeta escribe una revisión con el contenido anterior completo y emite
      <code>entrada.movida</code> al log. Todo monitor suscrito lo ve en su próximo ciclo.</span></div>`;
  wireDrag();
}

function wireDrag() {
  let st = null;
  $$('.bcard').forEach(card => {
    card.addEventListener('pointerdown', ev => {
      if (ev.button !== 0) return;
      st = { card, g: card.dataset.g, x0: ev.clientX, y0: ev.clientY, on: false, col: null };
      card.setPointerCapture(ev.pointerId);
    });
    card.addEventListener('pointermove', ev => {
      if (!st || st.card !== card) return;
      if (!st.on) {
        if (Math.hypot(ev.clientX - st.x0, ev.clientY - st.y0) < 4) return;
        st.on = true;
        const r = card.getBoundingClientRect();
        const g = card.cloneNode(true);
        g.className = 'bcard card-ghost';
        g.style.width = r.width + 'px'; g.style.left = r.left + 'px'; g.style.top = r.top + 'px';
        document.body.appendChild(g);
        st.ghost = g; st.ox = ev.clientX - r.left; st.oy = ev.clientY - r.top;
        card.classList.add('drag');
      }
      st.ghost.style.left = (ev.clientX - st.ox) + 'px';
      st.ghost.style.top = (ev.clientY - st.oy) + 'px';
      st.ghost.style.display = 'none';
      const u = document.elementFromPoint(ev.clientX, ev.clientY);
      st.ghost.style.display = '';
      const col = u && u.closest ? u.closest('.bcol') : null;
      if (col !== st.col) {
        if (st.col) st.col.classList.remove('over');
        if (col) col.classList.add('over');
        st.col = col;
      }
    });
    card.addEventListener('pointerup', async () => {
      if (!st || st.card !== card) return;
      const s = st; st = null;
      if (s.ghost) s.ghost.remove();
      card.classList.remove('drag');
      if (s.col) s.col.classList.remove('over');
      if (!s.on) { abrir(s.g); return; }
      const nuevo = s.col && s.col.dataset.status;
      if (!nuevo || nuevo === card.closest('.bcol').dataset.status) return;
      await patch('/api/entries/' + s.g, { status: nuevo, nota: 'movida desde el tablero' });
      toast('→ ' + nuevo); refrescarBadges(); render();
    });
    card.addEventListener('pointercancel', () => {
      if (st && st.ghost) st.ghost.remove();
      if (st && st.col) st.col.classList.remove('over');
      if (st) st.card.classList.remove('drag');
      st = null;
    });
  });
}

/* =========================================================== NOTAS === */
async function viewNotas() {
  const prev = S.f.kind; S.f.kind = ['nota'];
  const d = await api('/api/entries?' + qs({ per: 60 }));
  S.f.kind = prev;
  app.innerHTML = `
    <div class="toolbar">
      <span class="muted">${num(d.total)} notas · el centro de información</span>
      <span class="spacer"></span>
      <button class="btn btn-cy" id="nuevaNota">+ Nota</button>
    </div>
    <div class="notes">${d.items.map(n => `
      <div class="panel note" data-g="${n.genesis}">
        <h3>${esc(n.title)}</h3>
        <div class="cuerpo">${esc(n.body).slice(0, 460)}</div>
        <div class="pie">
          <span class="pill plain" style="color:${space(n.space).color}">${esc(space(n.space).name)}</span>
          ${n.labels.map(l => `<span class="lab">${esc(l)}</span>`).join('')}
          <span class="spacer"></span>
          <span class="gen">${n.genesis}</span>
        </div>
      </div>`).join('') || '<div class="empty">Sin notas en este filtro.</div>'}</div>`;
  $$('.note').forEach(n => n.onclick = () => abrir(n.dataset.g));
  $('#nuevaNota').onclick = () => formNuevo('nota');
}

/* ========================================================= ALMACÉN === */
async function viewAlmacen() {
  const p = new URLSearchParams();
  if (S.almacen.domain) p.set('domain', S.almacen.domain);
  S.almacen.action.forEach(a => p.append('action', a));
  if (S.almacen.text) p.set('text', S.almacen.text);
  p.set('limit', '80');
  const d = await api('/api/facts?' + p.toString());
  const total = d.total || 1;
  const PAL = ['#2ec5ff', '#56d9a3', '#a78bfa', '#f3b74e', '#fb7185', '#7cc6f5', '#5d6b82'];

  // la serie viene por (día, acción): se pliega a un total diario
  const porDia = {};
  (d.serie || []).forEach(r => { porDia[r.d] = (porDia[r.d] || 0) + r.n; });
  const serie = Object.keys(porDia).sort().map(k => ({ label: k.slice(8), date: k, value: porDia[k] }));

  app.innerHTML = `
    <div class="toolbar">
      <div class="seg" id="segDom">
        ${[['email', 'Correo'], ['', 'Todos los dominios']].concat(
          (d.porDominio || []).filter(x => x.value !== 'email').map(x => [x.value, x.value]))
          .map(([v, l]) => `<button data-d="${v}" class="${S.almacen.domain === v ? 'on' : ''}">${esc(l)}</button>`).join('')}
      </div>
      ${(d.porAccion || []).map(a => `<button class="chip ${S.almacen.action.includes(a.value) ? 'on' : ''}"
        data-act="${esc(a.value)}">${esc(a.value)}<span class="n">${num(a.n)}</span></button>`).join('')}
      <span class="spacer"></span>
      <a class="btn" href="/api/export/facts.csv?domain=${S.almacen.domain}">↓ CSV</a>
    </div>

    <div class="kpis">
      ${kpi('Hechos normalizados', num(d.total), `${d.porFuente.length} fuente(s)`)}
      ${kpi('Entregados', num((d.porAccion.find(a => a.value === 'delivered') || {}).n || 0),
            `de ${num((d.porAccion.find(a => a.value === 'sent') || {}).n || 0)} enviados`)}
      ${kpi('Rebotes', num((d.porAccion.find(a => a.value === 'bounced') || {}).n || 0),
            'abren entrada automática', 'var(--amber)')}
      ${kpi('Quejas', num((d.porAccion.find(a => a.value === 'complained') || {}).n || 0),
            'P0 y frena el volumen', 'var(--rose)')}
    </div>

    <div class="split">
      <div class="panel">
        <div class="panel-hd"><h2>Hechos</h2><span class="sub">crudo inmutable detrás de cada fila</span></div>
        <div class="tbl-wrap" style="max-height:none">
          <table class="tbl"><thead><tr>
            <th>Cuándo</th><th>Acción</th><th>Sujeto</th><th>De</th><th>Hacia</th><th>Fuente</th><th>Crudo</th>
          </tr></thead><tbody>
          ${d.items.map(f => `<tr data-raw="${f.raw_id}">
            <td class="muted mono">${rel(f.occurred_at)}</td>
            <td><span class="pill plain" style="color:${colorAccion(f.action)}">${esc(f.action)}</span></td>
            <td class="mono" style="font-size:11.5px">${esc(f.subject_id)}</td>
            <td class="muted">${esc(f.actor)}</td>
            <td class="muted">${esc(f.target)}</td>
            <td class="muted">${esc(f.source)}</td>
            <td class="mono muted" style="font-size:11px">#${f.raw_id}</td>
          </tr>`).join('')}
          </tbody></table>
          ${d.items.length ? '' : '<div class="empty">Sin señales con este filtro.</div>'}
        </div>
      </div>
      <div>
        <div class="panel" style="margin-bottom:14px">
          <div class="panel-hd"><h2>Volumen diario</h2></div>
          <div class="panel-bd"><div style="height:130px" id="serieAlm"></div></div>
        </div>
        <div class="panel" style="margin-bottom:14px">
          <div class="panel-hd"><h2>Mezcla por acción</h2></div>
          <div class="panel-bd row" style="gap:14px">
            <div style="width:120px;height:120px;flex:none" id="mixAlm"></div>
            <div style="flex:1;min-width:0">
              ${d.porAccion.map((a, i) => `<div class="hb" style="padding:4px 0">
                <span class="hb-l" style="width:auto;flex:1"><i style="display:inline-block;width:8px;height:8px;
                  border-radius:2px;background:${PAL[i % PAL.length]};margin-right:7px"></i>${esc(a.value)}</span>
                <span class="hb-v" style="width:52px">${num(a.n)}</span>
                <span class="hb-n">${(100 * a.n / total).toFixed(0)}%</span></div>`).join('')}
            </div>
          </div>
        </div>
        <div class="panel">
          <div class="panel-hd"><h2>Dominios emisores</h2></div>
          <div class="panel-bd">${(d.porEmisor || []).map(e => {
            const mx = Math.max(...d.porEmisor.map(x => x.n), 1);
            return `<div class="hb"><span class="hb-l">${esc(e.value)}</span>
              <span class="hb-t"><i style="width:${(100 * e.n / mx).toFixed(1)}%"></i></span>
              <span class="hb-n">${num(e.n)}</span></div>`; }).join('')}
          </div>
        </div>
      </div>
    </div>
    <div class="provenance"><span class="dot-i"></span>
      <span>Cada hecho conserva su <b>carga cruda</b> y su digest. El crudo no se reescribe nunca,
      ni cuando la firma falla: un rechazo también es evidencia. La normalización traduce el
      vocabulario de la fuente al del almacén sin tocar el original.</span></div>`;

  Charts.area($('#serieAlm'), serie, { unit: 'señales' });
  Charts.donut($('#mixAlm'), d.porAccion.map((a, i) => ({
    label: a.value, value: a.n, color: PAL[i % PAL.length] })), { center: num(d.total), centerSub: 'HECHOS' });
  $$('#segDom button').forEach(b => b.onclick = () => { S.almacen.domain = b.dataset.d; render(); });
  $$('.chip[data-act]').forEach(b => b.onclick = () => {
    const a = b.dataset.act, i = S.almacen.action.indexOf(a);
    i < 0 ? S.almacen.action.push(a) : S.almacen.action.splice(i, 1); render();
  });
  $$('tr[data-raw]').forEach(t => t.onclick = () => verCrudo(t.dataset.raw));
}
const colorAccion = a => ({ delivered: 'var(--green)', sent: 'var(--cyan)', opened: '#7cc6f5',
  clicked: 'var(--violet)', bounced: 'var(--amber)', complained: 'var(--rose)',
  received: 'var(--ink-2)' }[a] || 'var(--ink-2)');
function kpi(l, v, d, color) {
  return `<div class="panel kpi-b"><div class="l">${l}</div>
    <div class="v" ${color ? `style="color:${color}"` : ''}>${v}</div><div class="d">${d}</div></div>`;
}

async function verCrudo(id) {
  const r = await api('/api/raw/' + id);
  let cuerpo; try { cuerpo = JSON.parse(r.body); } catch (e) { cuerpo = r.body; }
  drawer(`
    <div class="dw-hd"><button class="icon-btn dw-close">✕</button>
      <h2>Carga cruda #${r.id}</h2>
      <div class="dw-meta"><span class="pill plain ${r.verified ? 'ok-b' : 'bad-b'}">
        ${r.verified ? 'firma verificada' : 'rechazada'}</span>
        <span>${esc(r.source)}</span><span>·</span><span>${rel(r.received_at)}</span></div></div>
    <div class="dw-sec"><h3>Digest del contenido</h3>
      <div class="mono" style="font-size:11px;word-break:break-all;color:var(--cyan)">${esc(r.digest)}</div>
      ${r.nota ? `<div class="muted" style="margin-top:8px;font-size:12px">${esc(r.nota)}</div>` : ''}</div>
    <div class="dw-sec"><h3>Cuerpo tal como llegó</h3>
      <pre class="json">${typeof cuerpo === 'string' ? esc(cuerpo) : jsonHTML(cuerpo)}</pre></div>
    <div class="dw-sec"><h3>Encabezados</h3><pre class="json">${jsonHTML(r.headers)}</pre></div>`);
}

/* ============================================================ FEED === */
async function viewFeed() {
  const p = new URLSearchParams(qs());
  if (S.feed.fam) p.append('event', S.feed.fam);
  p.set('since', '0'); p.set('limit', '80');
  const d = await api('/api/feed?' + p.toString());
  S.feed.cursor = d.head;
  S.feed.events = d.events.slice().reverse();

  app.innerHTML = `
    <div class="toolbar">
      <button class="btn ${S.feed.live ? 'btn-cy' : ''}" id="live">
        ${S.feed.live ? '<span class="livedot"></span> en vivo' : '▷ en vivo'}</button>
      <div class="seg" id="segFam">
        ${[['', 'Todo'], ['entrada.*', 'Entradas'], ['senal.ingerida', 'Señales'],
           ['entrada.reclamada', 'Reclamos'], ['monitor.registrado', 'Monitores']].map(([v, l]) =>
          `<button data-fam="${v}" class="${(S.feed.fam || '') === v ? 'on' : ''}">${l}</button>`).join('')}
      </div>
      <span class="muted">cursor <b class="mono" id="curLbl">${num(S.feed.cursor)}</b> · el mismo entero
        que lleva cada monitor</span>
      <span class="spacer"></span>
      <span class="muted" style="font-size:12px">filtro activo: <code>${esc(JSON.stringify(d.filtro))}</code></span>
    </div>
    <div class="panel">
      <div class="panel-hd"><h2>Log de eventos</h2>
        <div class="right"><span class="sub" id="feedN">${d.events.length} en pantalla</span></div></div>
      <div class="feed" id="feedList">${S.feed.events.map(filaEvento).join('')}</div>
    </div>
    <div class="provenance"><span class="dot-i"></span>
      <span>Este log es la fuente de verdad del bus. Un monitor guarda un entero —su cursor— y pide
      lo que pasó después. Con fechas, dos eventos del mismo segundo se pierden o se repiten;
      con un entero monotónico, no. El <b>head</b> siempre es el máximo real del log y no el del
      tramo filtrado, para que un filtro estrecho no deje al monitor atrás para siempre.</span></div>`;

  wireEventos();
  $('#live').onclick = () => { S.feed.live = !S.feed.live; S.feed.live ? arrancarLive() : pararLive(); render(); };
  $$('#segFam button').forEach(b => b.onclick = () => { S.feed.fam = b.dataset.fam; render(); });
  if (S.feed.live) arrancarLive();
}
function filaEvento(e) {
  const k = (e.kind || '').split('.').pop();
  const ent = e.entrada;
  let cont;
  if (e.kind === 'senal.ingerida') {
    const p = e.payload || {};
    cont = `<b>${esc(p.action || '')}</b> <span class="dim">${esc(p.dominio || p.fuente || '')}
      ${p.destino ? '→ ' + esc(p.destino) : ''}</span> <span class="dim mono">${esc(p.subject_id || '')}</span>`;
  } else if (ent) {
    const c = (e.payload || {}).cambios || {};
    const dif = Object.keys(c).map(f => `${f}: <span class="dim">${esc(String(c[f].de))}</span> → ${esc(String(c[f].a))}`).join(' · ');
    cont = `<b>${esc(ent.title)}</b>${dif ? ` <span class="dim">— ${dif}</span>` : ''}
      ${(e.payload || {}).nota ? `<span class="dim"> · ${esc(e.payload.nota)}</span>` : ''}
      ${(e.payload || {}).texto ? `<span class="dim"> · “${esc(e.payload.texto)}”</span>` : ''}`;
  } else {
    cont = `<span class="dim">${esc(JSON.stringify(e.payload || {}).slice(0, 130))}</span>`;
  }
  return `<div class="fev" ${ent ? `data-g="${ent.genesis}"` : ''} style="${ent ? 'cursor:pointer' : ''}">
    <span class="seq">${e.seq}</span>
    <span class="k ${k}">${esc(e.kind)}</span>
    <span class="cont">${cont}</span>
    <span class="dim" style="font-size:11px">${esc(actor(e.actor).name)}</span>
    <time>${rel(e.at)}</time>
  </div>`;
}
function wireEventos() {
  $$('.fev[data-g]').forEach(f => f.onclick = () => abrir(f.dataset.g));
}
function arrancarLive() {
  pararLive();
  S.feed.timer = setInterval(async () => {
    if (S.view !== 'feed') return pararLive();
    const p = new URLSearchParams(qs());
    if (S.feed.fam) p.append('event', S.feed.fam);
    p.set('since', S.feed.cursor); p.set('limit', '40');
    try {
      const d = await api('/api/feed?' + p.toString());
      if (d.events.length) {
        S.feed.cursor = d.head;
        const lista = $('#feedList');
        d.events.reverse().forEach(e => lista.insertAdjacentHTML('afterbegin', filaEvento(e)));
        $('#curLbl').textContent = num(S.feed.cursor);
        $('#feedN').textContent = lista.children.length + ' en pantalla';
        wireEventos();
      } else { S.feed.cursor = d.head; $('#curLbl').textContent = num(d.head); }
    } catch (e) { /* el ciclo sigue: un fallo de red no apaga el vivo */ }
  }, 2500);
}
function pararLive() { if (S.feed.timer) clearInterval(S.feed.timer); S.feed.timer = null; }

/* ======================================================= MONITORES === */
async function viewMonitores() {
  const d = await api('/api/monitors');
  app.innerHTML = `
    <div class="toolbar">
      <span class="muted">${d.items.length} inscritos · el bus reparte por filtro, no por difusión</span>
      <span class="spacer"></span>
      <a class="btn" href="/docs/PROTOCOLO.md" target="_blank">Protocolo</a>
      <button class="btn btn-cy" id="nuevoMon">+ Inscribir monitor</button>
    </div>
    <div class="cards">${d.items.map(m => {
      const pct = m.head ? Math.min(100, 100 * m.atraso / m.head) : 0;
      const cls = m.atraso > 400 ? 'critico' : m.atraso > 80 ? 'alto' : '';
      return `<div class="panel mcard">
        <div class="mcard-hd">
          ${av(m.owner, 'width:32px;height:32px')}
          <div style="flex:1;min-width:0">
            <h3>${esc(m.name)} ${m.active ? '' : '<span class="muted">(retirado)</span>'}</h3>
            <div class="who"><code>${esc(m.key)}</code> · ${esc(m.kind)} · de ${esc(actor(m.owner).name)}</div>
          </div>
        </div>
        <div class="mcard-bd">
          <div class="caps">${S.boot.capabilities.map(c =>
            `<span class="cap ${m.capabilities.includes(c) ? (c === 'actuar' ? 'act' : 'on') : ''}">${c}</span>`).join('')}</div>
          <div class="lag">
            <span class="muted" style="font-size:11.5px;width:56px">atraso</span>
            <span class="lag-bar"><i class="${cls}" style="width:${pct.toFixed(1)}%"></i></span>
            <span class="mono" style="font-size:12px">${num(m.atraso)}</span>
          </div>
          <dl class="kv2">
            <dt>cursor</dt><dd>${num(m.cursor)} / ${num(m.head)}</dd>
            <dt>arrendamiento</dt><dd>${m.lease_seconds}s</dd>
            <dt>reclamadas</dt><dd>${num(m.reclamadas)}</dd>
            <dt>visto</dt><dd>${rel(m.last_seen)}</dd>
          </dl>
          <div style="margin-top:11px"><div class="field"><label>Filtro</label>
            <pre class="json" style="max-height:120px">${jsonHTML(m.filter)}</pre></div></div>
        </div>
      </div>`; }).join('')}</div>
    <div class="provenance"><span class="dot-i"></span>
      <span>Un monitor se inscribe con un filtro y unas capacidades. <b>Observar</b> lee el feed;
      <b>reaccionar</b> comenta y etiqueta; <b>actuar</b> reclama con arrendamiento, reporta avance
      y cierra. Un reclamo vencido vuelve solo a la cola — sin eso, un monitor que muere se lleva
      el trabajo consigo.</span></div>`;
  $('#nuevoMon').onclick = formMonitor;
}

function formMonitor() {
  drawer(`
    <div class="dw-hd"><button class="icon-btn dw-close">✕</button>
      <h2>Inscribir un monitor</h2>
      <div class="dw-meta">El token se muestra una sola vez</div></div>
    <div class="dw-sec"><div class="form">
      <div class="frow">
        <div class="field"><label>Clave</label><input id="mKey" placeholder="mi-monitor"></div>
        <div class="field"><label>Tipo</label><select id="mKind">
          <option value="shell">shell</option><option value="harness">harness</option>
          <option value="agente">agente</option><option value="http">http</option></select></div>
      </div>
      <div class="field"><label>Nombre</label><input id="mName" placeholder="Para qué sirve"></div>
      <div class="field"><label>Capacidades</label>
        <div class="caps" id="mCaps">${S.boot.capabilities.map((c, i) =>
          `<span class="cap ${i === 0 ? 'on' : ''}" data-cap="${c}" style="cursor:pointer">${c}</span>`).join('')}</div>
        <span class="hint">Sin <b>actuar</b>, el bus rechaza reclamar y cerrar con un 403.</span></div>
      <div class="frow">
        <div class="field"><label>Espacios</label><select id="mSpace" multiple size="4"
          style="min-height:86px">${S.boot.spaces.map(s => `<option value="${s.key}">${esc(s.name)}</option>`).join('')}</select></div>
        <div class="field"><label>Eventos</label><select id="mEvent" multiple size="4"
          style="min-height:86px">${['entrada.*', ...S.boot.eventKinds].map(e => `<option value="${e}">${e}</option>`).join('')}</select></div>
      </div>
      <div class="frow">
        <div class="field"><label>Arrendamiento (s)</label><input id="mLease" type="number" value="900"></div>
        <div class="field"><label>Desde</label><select id="mDesde">
          <option value="ahora">sólo lo nuevo</option><option value="0">todo el historial</option></select></div>
      </div>
      <div class="field"><label>Sólo abiertas</label>
        <label class="row" style="font-size:13px"><input type="checkbox" id="mAbiertas" style="width:auto"> descartar cerradas y descartadas</label></div>
      <button class="btn btn-cy" id="mGo">Inscribir</button>
      <div id="mOut"></div>
    </div></div>`);
  $$('#mCaps .cap').forEach(c => c.onclick = () => c.classList.toggle('on'));
  $('#mGo').onclick = async () => {
    const sel = id => [...$(id).selectedOptions].map(o => o.value);
    const filtro = {};
    if (sel('#mSpace').length) filtro.space = sel('#mSpace');
    if (sel('#mEvent').length) filtro.event = sel('#mEvent');
    if ($('#mAbiertas').checked) filtro.abiertos = true;
    try {
      const r = await post('/api/monitors', {
        key: $('#mKey').value.trim(), name: $('#mName').value.trim() || undefined,
        kind: $('#mKind').value, filter: filtro,
        capabilities: $$('#mCaps .cap.on').map(c => c.dataset.cap),
        lease_seconds: +$('#mLease').value || 900,
        desde: $('#mDesde').value === 'ahora' ? 'ahora' : undefined,
      });
      $('#mOut').innerHTML = `<div class="warn" style="margin-top:12px">
        <div><b>Guarda este token ahora.</b> No se vuelve a mostrar.
        <pre class="json" style="margin-top:8px">${esc(r.token)}</pre>
        <div style="margin-top:8px">Arranca el monitor de referencia:</div>
        <pre class="json">EVLOG_TOKEN=${esc(r.token)} ./monitor.sh ${esc(r.key)}</pre></div></div>`;
      toast('Monitor ' + r.key + ' inscrito');
      refrescarBadges();
    } catch (e) {
      $('#mOut').innerHTML = `<div class="warn" style="margin-top:12px;border-color:rgba(249,107,125,.4);
        background:rgba(249,107,125,.08);color:#f9b0ba">${esc(e.message)}</div>`;
    }
  };
}

/* ========================================================= FUENTES === */
async function viewFuentes() {
  const [d, sync] = await Promise.all([api('/api/sources'), api('/api/sync')]);
  app.innerHTML = `
    <div class="toolbar"><span class="muted">${d.items.length} fuentes registradas · cada una con su contexto</span>
      <span class="spacer"></span>
      <button class="btn" id="drenar">↑ Drenar outbox</button></div>
    ${panelSync(sync)}
    <div class="cards">${d.items.map(s => `
      <div class="panel mcard">
        <div class="mcard-hd">
          <span class="glyph" style="--c:${space(s.space).color};width:32px;height:32px">${esc(s.kind.slice(0, 2).toUpperCase())}</span>
          <div style="flex:1;min-width:0">
            <h3>${esc(s.name)}</h3>
            <div class="who"><code>${esc(s.key)}</code> · espacio ${esc(s.space)}</div>
          </div>
          <span class="pill plain ${s.verify === 'ninguna' ? 'warn-b' : (s.secreto_presente ? 'ok-b' : 'bad-b')}">
            ${s.verify === 'ninguna' ? 'sin firma' : (s.secreto_presente ? s.verify : 'falta secreto')}</span>
        </div>
        <div class="mcard-bd">
          <div class="field"><label>Endpoint</label>
            <div class="mono-b" style="display:block;padding:7px 10px">POST ${esc(s.endpoint)}</div></div>
          ${s.verify !== 'ninguna' && !s.secreto_presente ? `<div class="warn mal" style="margin:10px 0">
            Declara verificación <b>${esc(s.verify)}</b> pero no hay secreto en
            <code>${esc(s.secreto_env)}</code>. Falla cerrado: hoy rechaza todo y conserva el crudo.</div>` : ''}
          <dl class="kv2">
            <dt>recibidos</dt><dd>${num(s.recibidos)}</dd>
            <dt>rechazados</dt><dd class="${s.rechazados ? 'bad-b' : ''}">${num(s.rechazados)}</dd>
            <dt>normalizador</dt><dd>${esc(s.normalizer)}</dd>
            <dt>abre entrada</dt><dd>${s.crea_entrada ? 'sí, cada señal' : 'sólo por regla'}</dd>
            <dt>último</dt><dd>${rel(s.ultimo)}</dd>
          </dl>
          <div style="margin-top:11px"><div class="field"><label>Contexto del registro</label>
            <pre class="json" style="max-height:150px">${jsonHTML(s.context)}</pre></div></div>
        </div>
      </div>`).join('')}</div>
    <div class="provenance"><span class="dot-i"></span>
      <span>Una fuente se registra <b>con su contexto</b>: para qué sirve, quién responde por ella,
      qué dominios abarca y qué se hace con lo que llega. Ese contexto viaja con cada señal, así que
      seis meses después se puede leer por qué entró un dato sin reconstruirlo de memoria.
      Los secretos viven en el entorno (<code>EVLOG_SECRET_*</code>), nunca en la base.</span></div>`;
  $('#drenar').onclick = async () => {
    try {
      const r = await post('/api/sync/drain', {});
      toast(`${r.enviados} enviados · ${r.fallidos} fallidos`);
    } catch (e) {
      toast(e.body && e.body.necesita ? 'Sin destino: falta ' + e.body.necesita.join(' y ') : e.message);
    }
    render();
  };
}

function panelSync(s) {
  const total = s.pendiente + s.enviado + s.error || 1;
  return `<div class="panel" style="margin-bottom:16px">
    <div class="panel-hd"><h2>Sincronización</h2>
      <span class="sub">${s.configurado ? 'destino: ' + s.destino
        : 'local únicamente — la cola se conserva en orden'}</span>
      <div class="right"><span class="pill plain ${s.configurado ? 'ok-b' : 'warn-b'}">
        ${s.configurado ? 'conectado' : 'sin destino'}</span></div></div>
    <div class="panel-bd">
      <div class="kpis" style="margin:0 0 12px;grid-template-columns:repeat(3,1fr)">
        <div><div class="l">Pendiente</div><div class="v">${num(s.pendiente)}</div></div>
        <div><div class="l">Enviado</div><div class="v ok-b">${num(s.enviado)}</div></div>
        <div><div class="l">Error</div><div class="v ${s.error ? 'bad-b' : ''}">${num(s.error)}</div></div>
      </div>
      ${!s.configurado ? `<div class="warn">
        El almacén local es completo y ordenado; falta el destino. Define
        <code>EVLOG_NEON_URL</code> y <code>EVLOG_NEON_CONN</code>, aplica
        <code>docs/almacen-neon.sql</code> y drena. El <code>digest</code> es la clave de
        idempotencia: reenviar la misma fila no duplica nada, así que un reintento tras una
        caída es seguro.</div>` : ''}
      <div style="margin-top:12px"><div class="field"><label>Cola (siguientes en salir)</label>
        <div class="tbl-wrap" style="max-height:200px"><table class="tbl">
          <thead><tr><th>#</th><th>Entidad</th><th>Op</th><th>Clave</th><th>Digest</th></tr></thead>
          <tbody>${s.muestra.map(m => `<tr><td class="mono muted">${m.id}</td>
            <td>${esc(m.entidad)}</td><td class="muted">${esc(m.op)}</td>
            <td class="mono" style="font-size:11px">${esc(m.clave)}</td>
            <td class="mono muted" style="font-size:10.5px">${esc((m.digest || '').slice(0, 14))}…</td>
          </tr>`).join('')}</tbody></table></div></div></div>
    </div>
  </div>`;
}

/* ========================================================== DRAWER === */
function drawer(html) {
  const dw = $('#drawer');
  dw.hidden = false; $('#scrim').hidden = false;
  dw.innerHTML = html;
  $$('.dw-close', dw).forEach(b => b.onclick = cerrar);
}
function cerrar() { $('#drawer').hidden = true; $('#scrim').hidden = true; }

async function abrir(g) {
  const e = await api('/api/entries/' + g);
  const sp = space(e.space);
  const campos = ((e.espacio || {}).schema_json || {}).campos || [];
  const T = S.drawerTab;
  drawer(`
    <div class="dw-hd">
      <button class="icon-btn dw-close">✕</button>
      <h2>${esc(e.title)}</h2>
      <div class="dw-meta">
        ${pillEstado(e.status)} ${e.kind === 'ticket' ? pillPrio(e.priority) : '<span class="pill plain">nota</span>'}
        <span style="color:${sp.color}">${esc(sp.name)}</span>
        <span class="gen">${e.genesis}</span>
        <span>v${e.version}</span>
        ${e.claimed_by ? `<span class="claimed">reclamada por ${esc(e.claimed_by)} · vence ${rel(e.claim_expires)}</span>` : ''}
      </div>
    </div>
    <div class="dw-tabs">
      ${[['ficha', 'Ficha'], ['bitacora', `Bitácora (${e.eventos.length})`],
         ['revisiones', `Revisiones (${e.revisiones.length})`]].map(([k, l]) =>
        `<div class="dw-tab ${T === k ? 'on' : ''}" data-tab="${k}">${l}</div>`).join('')}
    </div>
    <div id="dwBody">${T === 'ficha' ? tabFicha(e, campos) :
                       T === 'bitacora' ? tabBitacora(e) : tabRevisiones(e)}</div>`);
  $$('.dw-tab').forEach(t => t.onclick = () => { S.drawerTab = t.dataset.tab; abrir(g); });
  if (T === 'ficha') wireFicha(e);
  if (T === 'bitacora') wireComentario(e);
}

function tabFicha(e, campos) {
  return `
    ${e.body ? `<div class="dw-sec"><div class="nota-md">${esc(e.body)}</div></div>` : ''}
    <div class="dw-sec">
      <h3>Estado</h3>
      <div class="stage-pick">${S.boot.status.map(s =>
        `<button data-st="${s}" class="${s === e.status ? 'on' : ''}">${s}</button>`).join('')}</div>
      <h3 style="margin-top:16px">Responsable</h3>
      <div class="stage-pick">${[{ key: '', name: 'sin asignar' }].concat(
        S.boot.actors.filter(a => a.kind !== 'sistema' && a.kind !== 'externo')).map(a =>
        `<button data-as="${a.key}" class="${(e.assignee || '') === a.key ? 'on' : ''}">${esc(a.name)}</button>`).join('')}</div>
    </div>
    <div class="dw-sec">
      <h3>Identidad y procedencia</h3>
      <dl class="kv">
        <dt>Clave génesis</dt><dd class="mono" style="color:var(--cyan)">${e.genesis}</dd>
        <dt>Digest</dt><dd class="mono" style="font-size:11px;word-break:break-all">${esc(e.digest || '')}</dd>
        <dt>Origen</dt><dd>${esc(e.source)}</dd>
        <dt>Pedido por</dt><dd>${esc(actor(e.requester).name)}</dd>
        <dt>Creada</dt><dd>${new Date(e.created).toLocaleString('es-MX')} <span class="muted">(${rel(e.created)})</span></dd>
        <dt>Actualizada</dt><dd>${rel(e.updated)}</dd>
        ${e.closed_at ? `<dt>Cerrada</dt><dd>${rel(e.closed_at)}</dd>` : ''}
        ${e.blocked_by ? `<dt>Se espera a</dt><dd class="warn-b">${esc(e.blocked_by)}</dd>` : ''}
        <dt>Sincronización</dt><dd>${esc(e.sync_state)}</dd>
      </dl>
    </div>
    ${e.labels.length || e.refs.length ? `<div class="dw-sec">
      <h3>Etiquetas y referencias</h3>
      <div class="row" style="flex-wrap:wrap;gap:6px">
        ${e.labels.map(l => `<span class="lab">${esc(l)}</span>`).join('')}
        ${e.refs.map(r => `<span class="pill plain">${esc(r.tipo)}: ${esc(r.valor)}</span>`).join('')}
      </div></div>` : ''}
    ${campos.length ? `<div class="dw-sec">
      <h3>Metadata de ${esc(space(e.space).name)}</h3>
      <dl class="kv">${campos.map(c => `<dt>${esc(c.etiqueta)}</dt>
        <dd>${esc(e.meta[c.clave] != null ? e.meta[c.clave] : '—')}</dd>`).join('')}</dl>
      <span class="hint muted" style="font-size:11px">Estos campos los declara el espacio, no el
      producto: otro espacio pinta otros sin tocar el esquema.</span></div>` : ''}
    ${Object.keys(e.meta).length ? `<div class="dw-sec"><h3>Metadata completa</h3>
      <pre class="json">${jsonHTML(e.meta)}</pre></div>` : ''}`;
}
function wireFicha(e) {
  $$('[data-st]').forEach(b => b.onclick = async () => {
    await patch('/api/entries/' + e.genesis, { status: b.dataset.st });
    toast('→ ' + b.dataset.st); abrir(e.genesis); refrescarBadges(); render();
  });
  $$('[data-as]').forEach(b => b.onclick = async () => {
    await patch('/api/entries/' + e.genesis, { assignee: b.dataset.as || null });
    toast(b.dataset.as ? 'asignada a ' + actor(b.dataset.as).name : 'sin asignar');
    abrir(e.genesis); render();
  });
}
function tabBitacora(e) {
  return `<div class="dw-sec">
    <div class="note-box" style="margin:0 0 16px">
      <input id="cmt" placeholder="Comentar…" maxlength="400">
      <button class="btn btn-cy" id="cmtGo">Añadir</button>
    </div>
    <div class="tl">${e.eventos.map(ev => `
      <div class="tl-item k-${(ev.kind || '').split('.').pop()}">
        <h4>${esc(ev.kind)} <span class="muted" style="font-weight:400">· seq ${ev.seq}</span></h4>
        <p>${esc(actor(ev.actor).name)} · ${rel(ev.at)}
        ${ev.payload.texto ? `<br>“${esc(ev.payload.texto)}”` : ''}
        ${ev.payload.nota ? `<br>${esc(ev.payload.nota)}` : ''}
        ${ev.payload.cambios ? '<br>' + Object.keys(ev.payload.cambios).map(k =>
          `${k}: ${esc(String(ev.payload.cambios[k].de))} → ${esc(String(ev.payload.cambios[k].a))}`).join(' · ') : ''}
        </p>
      </div>`).join('')}</div>
    <div class="muted" style="font-size:11.5px;margin-top:12px">
      La bitácora es acumulativa: la última entrada corrige a la anterior, no la reescribe.</div>
  </div>`;
}
function wireComentario(e) {
  const go = async () => {
    const t = $('#cmt').value.trim(); if (!t) return;
    await post('/api/entries/' + e.genesis + '/comentario', { texto: t });
    toast('Comentario añadido'); abrir(e.genesis);
  };
  $('#cmtGo').onclick = go;
  $('#cmt').onkeydown = ev => { if (ev.key === 'Enter') go(); };
}
function tabRevisiones(e) {
  if (!e.revisiones.length) return '<div class="dw-sec"><div class="empty">Sin cambios todavía.</div></div>';
  return `<div class="dw-sec">
    ${e.revisiones.map(r => `<div class="rev">
      <h4>v${r.version} → v${r.version + 1} <span class="muted" style="font-weight:400">·
        ${esc(actor(r.actor).name)} · ${rel(r.at)}</span></h4>
      ${r.note ? `<div class="muted" style="font-size:12px;margin-top:3px">${esc(r.note)}</div>` : ''}
      <dl class="diff">${Object.keys(r.changed).map(k => `<dt>${esc(k)}</dt>
        <dd><span class="de">${esc(String(r.changed[k].de))}</span>
        <span class="a">${esc(String(r.changed[k].a))}</span></dd>`).join('')}</dl>
      <div class="mono muted" style="font-size:10.5px;margin-top:6px">digest anterior ${esc((r.digest || '').slice(0, 16))}…</div>
    </div>`).join('')}
    <div class="muted" style="font-size:11.5px">Cada revisión guarda el contenido anterior completo.
    La identidad se nombra por digest, no por etiqueta.</div>
  </div>`;
}

/* ======================================================= NUEVA ENTRADA */
function formNuevo(kind) {
  const k = kind || 'ticket';
  drawer(`
    <div class="dw-hd"><button class="icon-btn dw-close">✕</button>
      <h2>Nueva ${k === 'nota' ? 'nota' : 'entrada'}</h2>
      <div class="dw-meta">Entra como <b>entrante</b> hasta que alguien la acepte</div></div>
    <div class="dw-sec"><div class="form">
      <div class="field"><label>Título</label><input id="nTitle" placeholder="Qué hay que hacer, en una línea"></div>
      <div class="field"><label>Cuerpo</label><textarea id="nBody"
        placeholder="El mecanismo, no la etiqueta. Si el lector no puede explicar la causa después de leerlo, la sección falló."></textarea></div>
      <div class="frow">
        <div class="field"><label>Tipo</label><select id="nKind">
          <option value="ticket" ${k === 'ticket' ? 'selected' : ''}>ticket</option>
          <option value="nota" ${k === 'nota' ? 'selected' : ''}>nota</option></select></div>
        <div class="field"><label>Espacio</label><select id="nSpace">
          ${S.boot.spaces.map(s => `<option value="${s.key}" ${S.f.space[0] === s.key ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select></div>
        <div class="field"><label>Prioridad</label><select id="nPrio">
          ${S.boot.priority.map(p => `<option ${p === 'P2' ? 'selected' : ''}>${p}</option>`).join('')}</select></div>
      </div>
      <div class="frow">
        <div class="field"><label>Responsable</label><select id="nAsg">
          <option value="">sin asignar</option>
          ${S.boot.actors.filter(a => a.kind !== 'sistema').map(a => `<option value="${a.key}">${esc(a.name)}</option>`).join('')}</select></div>
        <div class="field"><label>Solicitante</label><select id="nReq">
          ${S.boot.actors.filter(a => a.kind !== 'sistema').map(a =>
            `<option value="${a.key}" ${a.key === 'dana' ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}</select></div>
      </div>
      <div class="field"><label>Etiquetas</label>
        <div class="chips-in" id="nLabsBox"><input id="nLabs" placeholder="escribe y Enter"></div></div>
      <div class="field" id="nMetaWrap"></div>
      <button class="btn btn-cy" id="nGo">Crear</button>
    </div></div>`);
  const labs = [];
  const pintar = () => {
    $$('.chip-x', $('#nLabsBox')).forEach(c => c.remove());
    labs.forEach((l, i) => $('#nLabsBox').insertAdjacentHTML('afterbegin',
      `<span class="chip-x">${esc(l)}<b data-i="${i}">✕</b></span>`));
    $$('#nLabsBox .chip-x b').forEach(b => b.onclick = () => { labs.splice(+b.dataset.i, 1); pintar(); });
  };
  $('#nLabs').onkeydown = ev => {
    if (ev.key === 'Enter' && ev.target.value.trim()) {
      labs.push(ev.target.value.trim()); ev.target.value = ''; pintar(); ev.preventDefault();
    }
  };
  const metaFields = () => {
    const sp = space($('#nSpace').value);
    const campos = (sp.schema_json || {}).campos || [];
    $('#nMetaWrap').innerHTML = campos.length ? `<label>Metadata de ${esc(sp.name)}</label>
      <div class="frow">${campos.map(c => c.tipo === 'opcion'
        ? `<div class="field"><label>${esc(c.etiqueta)}</label><select data-meta="${c.clave}">
             <option value="">—</option>${c.opciones.map(o => `<option>${esc(o)}</option>`).join('')}</select></div>`
        : `<div class="field"><label>${esc(c.etiqueta)}</label>
             <input data-meta="${c.clave}" type="${c.tipo === 'numero' ? 'number' : 'text'}"></div>`).join('')}</div>` : '';
  };
  $('#nSpace').onchange = metaFields; metaFields();
  $('#nGo').onclick = async () => {
    const t = $('#nTitle').value.trim();
    if (!t) return toast('El título es obligatorio');
    const meta = {};
    $$('[data-meta]').forEach(i => { if (i.value) meta[i.dataset.meta] = i.value; });
    const r = await post('/api/entries', {
      kind: $('#nKind').value, space: $('#nSpace').value, title: t,
      body: $('#nBody').value, priority: $('#nPrio').value,
      assignee: $('#nAsg').value || null, requester: $('#nReq').value,
      labels: labs, meta, actor: $('#nReq').value,
    });
    toast('Creada ' + r.genesis);
    refrescarBadges(); render(); abrir(r.genesis);
  };
}

/* =========================================================== router === */
const VIEWS = { bandeja: viewBandeja, tablero: viewTablero, notas: viewNotas,
                almacen: viewAlmacen, feed: viewFeed, monitores: viewMonitores,
                fuentes: viewFuentes };

async function render() {
  $$('#nav a').forEach(a => a.classList.toggle('on', a.dataset.view === S.view));
  try { await VIEWS[S.view](); }
  catch (e) {
    app.innerHTML = `<div class="empty">No se pudo cargar la vista.<br>
      <code>${esc(e.message)}</code></div>`;
  }
}
function route() {
  const v = (location.hash.replace(/^#\/?/, '') || 'bandeja').split('?')[0];
  if (v !== 'feed') pararLive();
  S.view = VIEWS[v] ? v : 'bandeja';
  cerrar();
  render();
}
async function refrescarBadges() {
  S.boot = await api('/api/bootstrap');
  $('#bAbiertas').textContent = num(S.boot.totales.abiertas);
  $('#bEventos').textContent = num(S.boot.totales.eventos);
  renderSpacebar();
}

function marcaDemo() {
  if (!S.boot.demo || $('#demoChip')) return;
  $('.brand').insertAdjacentHTML('afterend',
    `<span class="chip on" id="demoChip" title="Cada instancia arranca de una semilla; lo que escribas se pierde cuando se recicla"
      style="cursor:default;height:22px;font-size:11px;letter-spacing:.1em;font-family:var(--dis);font-weight:700">DEMO</span>`);
  document.body.insertAdjacentHTML('beforeend',
    `<div class="provenance" style="position:fixed;bottom:12px;right:14px;max-width:430px;z-index:70;
      background:rgba(20,25,36,.94);backdrop-filter:blur(8px)">
      <span class="dot-i"></span>
      <span>Demostración con datos sembrados. <b>Escribe lo que quieras</b>: el servidor es el real
      y las mutaciones funcionan de verdad, pero cada instancia parte de la misma semilla y su
      estado se pierde al reciclarse. El bus de monitores existe en el código; para verlo vivo hay
      que correrlo en local.</span>
      <span class="icon-btn" onclick="this.parentElement.remove()" style="flex:none">✕</span>
    </div>`);
}

(async function init() {
  S.boot = await api('/api/bootstrap');
  marcaDemo();
  $('#bAbiertas').textContent = num(S.boot.totales.abiertas);
  $('#bEventos').textContent = num(S.boot.totales.eventos);
  renderSpacebar();
  window.addEventListener('hashchange', route);
  $('#scrim').onclick = cerrar;
  $('#nuevo').onclick = () => formNuevo('ticket');
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') cerrar();
    if (e.key === '/' && document.activeElement !== $('#q')) { e.preventDefault(); $('#q').focus(); }
  });
  let t;
  $('#q').addEventListener('input', e => {
    clearTimeout(t);
    t = setTimeout(() => { S.f.text = e.target.value.trim(); renderSpacebar(); render(); }, 260);
  });
  route();
})();
})();
