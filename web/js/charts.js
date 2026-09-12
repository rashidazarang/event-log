/* =================================================================
   charts.js — SVG a mano. Sin librerías, sin build.
   Cada gráfica se mide contra su contenedor y se redibuja al cambiar
   de tamaño, así que no hay distorsión por viewBox estirado.
   ================================================================= */
(function (global) {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';

  function el(tag, attrs, kids) {
    const n = document.createElementNS(NS, tag);
    for (const k in (attrs || {})) {
      if (attrs[k] == null) continue;
      n.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(c => n.appendChild(c));
    return n;
  }
  const fmtMx = n => '$' + Math.round(n).toLocaleString('es-MX');
  const compact = n => {
    const a = Math.abs(n);
    if (a >= 1e9) return (n / 1e9).toFixed(a >= 1e10 ? 0 : 1).replace('.0', '') + 'B';
    if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace('.0', '') + 'M';
    if (a >= 1e3) return (n / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace('.0', '') + 'k';
    return String(Math.round(n));
  };

  /* ---- tooltip compartido -------------------------------------- */
  let tip;
  function showTip(x, y, html) {
    if (!tip) { tip = document.createElement('div'); tip.className = 'tooltip'; document.body.appendChild(tip); }
    tip.innerHTML = html; tip.style.left = x + 'px'; tip.style.top = y + 'px'; tip.hidden = false;
  }
  function hideTip() { if (tip) tip.hidden = true; }

  /* ---- montaje responsivo -------------------------------------- */
  function mount(host, draw) {
    if (host.__ro) host.__ro.disconnect();
    // El SVG se posiciona en absoluto: si participa del flujo, su alto
    // realimenta al contenedor flexible, el ResizeObserver vuelve a medir mas
    // grande y la grafica crece sin fin (llego a 1975px de alto).
    if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
    const run = () => {
      const w = host.clientWidth, h = host.clientHeight;
      if (w < 8 || h < 8) return;
      if (host.__w === w && host.__h === h) return;   // nada que redibujar
      host.__w = w; host.__h = h;
      host.textContent = '';
      const node = draw(w, h);
      node.style.position = 'absolute';
      node.style.left = '0'; node.style.top = '0';
      host.appendChild(node);
    };
    const ro = new ResizeObserver(() => { clearTimeout(host.__t); host.__t = setTimeout(run, 60); });
    ro.observe(host); host.__ro = ro; run();
    // la primera medicion cae antes de que la fuente cambie las metricas del
    // panel; sin este segundo pase la grafica queda mas angosta que su caja
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(run);
  }

  /* ---- curva suave (Catmull-Rom → cúbica de Bézier) ------------- */
  function smooth(pts, tension) {
    if (pts.length < 2) return '';
    const t = tension == null ? 0.42 : tension;
    let d = `M${pts[0][0]},${pts[0][1]}`;
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
      d += ` C${p1[0] + (p2[0] - p0[0]) * t / 3},${p1[1] + (p2[1] - p0[1]) * t / 3}` +
           ` ${p2[0] - (p3[0] - p1[0]) * t / 3},${p2[1] - (p3[1] - p1[1]) * t / 3}` +
           ` ${p2[0]},${p2[1]}`;
    }
    return d;
  }

  /* ================================================== donut / gauge === */
  function gauge(host, pct, color) {
    const c = color || '#2ec5ff', R = 24, C = 2 * Math.PI * R;
    host.textContent = '';
    const s = el('svg', { viewBox: '0 0 58 58', width: 58, height: 58 });
    s.appendChild(el('circle', { cx: 29, cy: 29, r: R, fill: 'none', stroke: 'rgba(255,255,255,.09)', 'stroke-width': 4 }));
    const arc = el('circle', {
      cx: 29, cy: 29, r: R, fill: 'none', stroke: c, 'stroke-width': 4, 'stroke-linecap': 'round',
      transform: 'rotate(-90 29 29)', 'stroke-dasharray': C, 'stroke-dashoffset': C,
      style: 'filter:drop-shadow(0 0 6px ' + c + '66)'
    });
    s.appendChild(arc);
    host.appendChild(s);
    const span = document.createElement('span');
    span.style.color = c; span.textContent = Math.round(pct) + '%';
    host.appendChild(span);
    requestAnimationFrame(() => {
      arc.style.transition = 'stroke-dashoffset 1.1s cubic-bezier(.2,.8,.25,1)';
      arc.setAttribute('stroke-dashoffset', String(C * (1 - Math.max(0, Math.min(100, pct)) / 100)));
    });
  }

  /* ============================================ área con marcador ===== */
  function area(host, series, opts) {
    const o = opts || {};
    mount(host, (w, h) => {
      const padT = 26, padB = 8, padX = 24;
      // el eje se ajusta al rango observado: la variacion diaria es de
      // decenas sobre cientos, y con base en cero la linea seria plana.
      // Las etiquetas de los extremos dejan la escala a la vista.
      const vals = series.map(d => d.value);
      const hiV = Math.max(...vals), loV = Math.min(...vals);
      const pad = Math.max(1, (hiV - loV) * 0.35);
      const top = hiV + pad, bot = Math.max(0, loV - pad);
      const iw = w - padX * 2, ih = h - padT - padB;
      const X = i => padX + (series.length === 1 ? iw / 2 : (iw * i) / (series.length - 1));
      const Y = v => padT + ih - ((v - bot) / (top - bot || 1)) * ih * 0.9;
      const pts = series.map((d, i) => [X(i), Y(d.value)]);
      const line = smooth(pts);
      const uid = 'g' + Math.random().toString(36).slice(2, 8);

      const s = el('svg', { width: w, height: h, viewBox: `0 0 ${w} ${h}` });
      const defs = el('defs');
      const lg = el('linearGradient', { id: uid, x1: 0, y1: 0, x2: 0, y2: 1 });
      lg.appendChild(el('stop', { offset: '0%', 'stop-color': '#2ec5ff', 'stop-opacity': .34 }));
      lg.appendChild(el('stop', { offset: '100%', 'stop-color': '#2ec5ff', 'stop-opacity': 0 }));
      defs.appendChild(lg); s.appendChild(defs);

      for (let g = 0; g <= 3; g++) {
        const y = padT + (ih * g) / 3;
        s.appendChild(el('line', { x1: 0, x2: w, y1: y, y2: y, stroke: 'rgba(255,255,255,.045)' }));
      }
      [[hiV, Y(hiV)], [loV, Y(loV)]].forEach(([v, y]) => {
        const t = el('text', { x: 6, y: y - 5, fill: '#5d6b82', 'font-size': 9.5,
          'font-family': 'Archivo, sans-serif', 'letter-spacing': .6 });
        t.textContent = v; s.appendChild(t);
      });
      s.appendChild(el('path', {
        d: line + ` L${X(series.length - 1)},${h - padB} L${X(0)},${h - padB} Z`,
        fill: `url(#${uid})`
      }));
      const path = el('path', {
        d: line, fill: 'none', stroke: '#2ec5ff', 'stroke-width': 2.2, 'stroke-linecap': 'round',
        style: 'filter:drop-shadow(0 3px 10px rgba(46,197,255,.45))'
      });
      s.appendChild(path);
      const len = 2000;
      path.setAttribute('stroke-dasharray', len);
      path.setAttribute('stroke-dashoffset', len);
      requestAnimationFrame(() => {
        path.style.transition = 'stroke-dashoffset 1.4s cubic-bezier(.35,.8,.3,1)';
        path.setAttribute('stroke-dashoffset', '0');
      });

      // marcador fijo en el máximo (el "hoy" de la referencia)
      let hi = 0; series.forEach((d, i) => { if (d.value >= series[hi].value) hi = i; });
      const mark = el('g');
      mark.appendChild(el('line', { x1: X(hi), x2: X(hi), y1: Y(series[hi].value), y2: h - padB, stroke: 'rgba(46,197,255,.35)', 'stroke-dasharray': '3 3' }));
      mark.appendChild(el('circle', { cx: X(hi), cy: Y(series[hi].value), r: 9, fill: 'rgba(46,197,255,.18)' }));
      mark.appendChild(el('circle', { cx: X(hi), cy: Y(series[hi].value), r: 4, fill: '#fff', stroke: '#2ec5ff', 'stroke-width': 2 }));
      s.appendChild(mark);

      // capa de hover
      const hov = el('g');
      const vline = el('line', { y1: padT - 12, y2: h - padB, stroke: 'rgba(255,255,255,.25)', opacity: 0 });
      const hdot = el('circle', { r: 4.5, fill: '#2ec5ff', stroke: '#101722', 'stroke-width': 2, opacity: 0 });
      hov.appendChild(vline); hov.appendChild(hdot); s.appendChild(hov);
      const hit = el('rect', { x: 0, y: 0, width: w, height: h, fill: 'transparent' });
      s.appendChild(hit);
      hit.addEventListener('mousemove', ev => {
        const r = s.getBoundingClientRect();
        const i = Math.max(0, Math.min(series.length - 1,
          Math.round(((ev.clientX - r.left) - padX) / (iw / Math.max(1, series.length - 1)))));
        const d = series[i];
        vline.setAttribute('x1', X(i)); vline.setAttribute('x2', X(i)); vline.setAttribute('opacity', 1);
        hdot.setAttribute('cx', X(i)); hdot.setAttribute('cy', Y(d.value)); hdot.setAttribute('opacity', 1);
        showTip(r.left + X(i), r.top + Y(d.value),
          `<b>${d.value}</b> ${o.unit || 'actividades'}<br><span class="muted">${d.date || d.label}</span>`);
      });
      hit.addEventListener('mouseleave', () => { vline.setAttribute('opacity', 0); hdot.setAttribute('opacity', 0); hideTip(); });
      return s;
    });
  }

  /* ================================== barras + línea superpuesta ====== */
  function bars(host, data, opts) {
    const o = opts || {};
    mount(host, (w, h) => {
      const padB = 22, padT = 14, padX = 6;
      const max = Math.max(1, ...data.map(d => d.value));
      const maxL = Math.max(1, ...data.map(d => d.touched || 0));
      const iw = w - padX * 2, ih = h - padT - padB;
      const bw = Math.min(30, (iw / data.length) * 0.52);
      const X = i => padX + (iw * (i + 0.5)) / data.length;
      const s = el('svg', { width: w, height: h, viewBox: `0 0 ${w} ${h}` });

      for (let g = 0; g <= 3; g++) {
        const y = padT + (ih * g) / 3;
        s.appendChild(el('line', { x1: 0, x2: w, y1: y, y2: y, stroke: 'rgba(255,255,255,.04)' }));
      }
      data.forEach((d, i) => {
        const bh = Math.max(2, (d.value / max) * ih);
        const on = d.current;
        const r = el('rect', {
          x: X(i) - bw / 2, y: padT + ih - bh, width: bw, height: bh, rx: 3,
          fill: on ? '#2ec5ff' : 'rgba(255,255,255,.13)',
          style: on ? 'filter:drop-shadow(0 0 14px rgba(46,197,255,.55))' : ''
        });
        r.style.transformOrigin = `0 ${padT + ih}px`;
        r.style.animation = `growY .7s cubic-bezier(.2,.8,.3,1) ${i * 45}ms both`;
        r.addEventListener('mouseenter', ev => {
          const bb = ev.target.getBoundingClientRect();
          showTip(bb.left + bb.width / 2, bb.top,
            `<b>${fmtMx(d.value)}</b> ganado<br><span class="muted">${d.count} cierres · ${d.touched} cuentas tocadas</span>`);
        });
        r.addEventListener('mouseleave', hideTip);
        s.appendChild(r);
        s.appendChild(el('text', {
          x: X(i), y: h - 7, 'text-anchor': 'middle', fill: on ? '#e9eef7' : '#5d6b82',
          'font-size': 10, 'font-family': 'Archivo, sans-serif', 'letter-spacing': 1
        })).textContent = d.label;
      });
      // línea de cuentas trabajadas
      const pts = data.map((d, i) => [X(i), padT + ih - ((d.touched || 0) / maxL) * ih * .8]);
      const p = el('path', { d: smooth(pts), fill: 'none', stroke: '#56d9a3', 'stroke-width': 1.8, opacity: .9 });
      s.appendChild(p);
      pts.forEach(pt => s.appendChild(el('circle', { cx: pt[0], cy: pt[1], r: 2.4, fill: '#56d9a3' })));
      const L = 1200; p.setAttribute('stroke-dasharray', L); p.setAttribute('stroke-dashoffset', L);
      requestAnimationFrame(() => { p.style.transition = 'stroke-dashoffset 1.3s ease-out .3s'; p.setAttribute('stroke-dashoffset', 0); });
      return s;
    });
  }

  /* ======================================= dona segmentada (mix) ====== */
  function donut(host, slices, opts) {
    const o = opts || {};
    mount(host, (w, h) => {
      const size = Math.min(w, h), cx = w / 2, cy = h / 2;
      const R = size / 2 - 6, r = R * (o.inner || .62);
      const total = slices.reduce((a, s) => a + s.value, 0) || 1;
      const s = el('svg', { width: w, height: h, viewBox: `0 0 ${w} ${h}` });
      let a0 = -Math.PI / 2;
      slices.forEach((sl, i) => {
        const a1 = a0 + (sl.value / total) * Math.PI * 2;
        const big = a1 - a0 > Math.PI ? 1 : 0;
        const p = el('path', {
          d: `M${cx + R * Math.cos(a0)},${cy + R * Math.sin(a0)}` +
             `A${R},${R} 0 ${big} 1 ${cx + R * Math.cos(a1)},${cy + R * Math.sin(a1)}` +
             `L${cx + r * Math.cos(a1)},${cy + r * Math.sin(a1)}` +
             `A${r},${r} 0 ${big} 0 ${cx + r * Math.cos(a0)},${cy + r * Math.sin(a0)}Z`,
          fill: sl.color, opacity: .92
        });
        p.style.transition = 'opacity .15s';
        p.addEventListener('mouseenter', ev => {
          p.setAttribute('opacity', 1);
          const bb = host.getBoundingClientRect();
          showTip(bb.left + bb.width / 2, bb.top + bb.height / 2,
            `<b>${sl.label}</b><br><span class="muted">${compact(sl.value)} · ${(100 * sl.value / total).toFixed(1)}%</span>`);
        });
        p.addEventListener('mouseleave', () => { p.setAttribute('opacity', .92); hideTip(); });
        s.appendChild(p);
        a0 = a1;
      });
      if (o.center) {
        const t = el('text', { x: cx, y: cy - 2, 'text-anchor': 'middle', fill: '#e9eef7', 'font-size': 19, 'font-weight': 700, 'font-family': 'Archivo, sans-serif' });
        t.textContent = o.center; s.appendChild(t);
        if (o.centerSub) {
          const t2 = el('text', { x: cx, y: cy + 14, 'text-anchor': 'middle', fill: '#5d6b82', 'font-size': 10.5, 'font-family': 'Archivo, sans-serif', 'letter-spacing': 1.2 });
          t2.textContent = o.centerSub; s.appendChild(t2);
        }
      }
      return s;
    });
  }

  /* ================================================ embudo por etapa == */
  function funnel(host, stages) {
    mount(host, (w, h) => {
      const s = el('svg', { width: w, height: h, viewBox: `0 0 ${w} ${h}` });
      const open = stages.filter(x => x.stage !== 'Perdido');
      const max = Math.max(1, ...open.map(x => x.count));
      const rowH = h / open.length;
      const colors = ['#4b5b73', '#3d87b8', '#2ec5ff', '#a78bfa', '#56d9a3'];
      open.forEach((st, i) => {
        const bw = Math.max(30, (st.count / max) * (w - 108));
        const y = i * rowH + 4;
        const g = el('g');
        const rect = el('rect', { x: 100, y: y, width: bw, height: rowH - 9, rx: 4, fill: colors[i] || '#2ec5ff', opacity: .85 });
        rect.style.transformOrigin = '100px 0';
        rect.style.animation = `grow .6s cubic-bezier(.2,.8,.3,1) ${i * 70}ms both`;
        g.appendChild(rect);
        const lb = el('text', { x: 92, y: y + rowH / 2 - 1, 'text-anchor': 'end', fill: '#94a1b8', 'font-size': 12, 'font-family': 'Barlow, sans-serif' });
        lb.textContent = st.stage; g.appendChild(lb);
        const vl = el('text', { x: 108, y: y + rowH / 2 - 1, fill: '#08121b', 'font-size': 11.5, 'font-weight': 700, 'font-family': 'Archivo, sans-serif' });
        vl.textContent = st.count.toLocaleString('es-MX'); g.appendChild(vl);
        const vv = el('text', { x: 100 + bw + 8, y: y + rowH / 2 - 1, fill: '#5d6b82', 'font-size': 11, 'font-family': 'Archivo, sans-serif' });
        vv.textContent = compact(st.value); g.appendChild(vv);
        s.appendChild(g);
      });
      return s;
    });
  }

  /* ======================================= silueta urbana del hero ==== */
  function skyline(host) {
    mount(host, (w, h) => {
      const s = el('svg', { width: w, height: h, viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: 'none' });
      // pseudoaleatorio determinista: la misma skyline en cada carga
      let seed = 20260910;
      const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
      [[0.42, .10, '#20293a'], [0.62, .18, '#1a2231'], [0.86, .30, '#141b28']].forEach(([hf, op, col]) => {
        let x = -20;
        const g = el('g', { opacity: op * 3 });
        while (x < w + 20) {
          const bw = 16 + rnd() * 46;
          const bh = (0.25 + rnd() * 0.75) * h * hf;
          g.appendChild(el('rect', { x: x, y: h - bh, width: bw - 3, height: bh, fill: col }));
          // ventanas
          if (bw > 26 && rnd() > .35) {
            for (let wy = h - bh + 8; wy < h - 10; wy += 11) {
              for (let wx = x + 5; wx < x + bw - 9; wx += 9) {
                if (rnd() > .82) g.appendChild(el('rect', { x: wx, y: wy, width: 2.5, height: 3.5, fill: '#2ec5ff', opacity: .28 }));
              }
            }
          }
          x += bw;
        }
        s.appendChild(g);
      });
      return s;
    });
  }

  global.Charts = { gauge, area, bars, donut, funnel, skyline, compact, fmtMx, smooth, showTip, hideTip, mount };
})(window);
