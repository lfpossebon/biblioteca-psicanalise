/* Figuras do chat — SVG desenhado à mão, sem dependências.
 *
 * Viz.figure(spec)          → <figure> de uma especificação do servidor (psilib/visuals.py)
 * Viz.sources(citacoes, n)  → cronologia das fontes citadas numa resposta (ou null)
 * Viz.python(evento)        → cartão de uma análise em Python (gráficos, downloads, código)
 *
 * Regras (skill de visualização): paleta categórica validada (3 cores, todos os pares, claro e
 * escuro), um eixo só, linhas de 2px, barras <= 24px com ponta arredondada, grade hairline,
 * tooltip com crosshair (também por teclado), legenda para >= 2 séries e tabela "ver dados" em
 * toda figura. Rótulos vêm de dados: sempre textContent, nunca innerHTML.
 */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const num = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });
  const YEAR = 365.25 * 864e5;
  const GROUPS = [
    { key: "lacan", nome: "Lacan", cor: "var(--viz-s1)" },
    { key: "freud", nome: "Freud", cor: "var(--viz-s2)" },
    { key: "outros", nome: "Comentadores e escolas", cor: "var(--viz-s3)" },
  ];
  const SERIES = ["var(--viz-s1)", "var(--viz-s2)", "var(--viz-s3)"];

  const CSS = `
.viz-root { color-scheme: light; --viz-surface:#fcfcfb; --viz-ink:#0b0b0b; --viz-ink2:#52514e; --viz-muted:#898781;
  --viz-grid:#e1e0d9; --viz-axis:#c3c2b7; --viz-ring:rgba(11,11,11,.10); --viz-s1:#2a78d6; --viz-s2:#eb6834; --viz-s3:#1baf7a;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
@media (prefers-color-scheme: dark) { :root:where(:not([data-theme="light"])) .viz-root { color-scheme: dark;
  --viz-surface:#1a1a19; --viz-ink:#f0efec; --viz-ink2:#c3c2b7; --viz-muted:#898781; --viz-grid:#2c2c2a; --viz-axis:#383835;
  --viz-ring:rgba(255,255,255,.10); --viz-s1:#3987e5; --viz-s2:#d95926; --viz-s3:#199e70; } }
:root[data-theme="dark"] .viz-root { color-scheme: dark; --viz-surface:#1a1a19; --viz-ink:#f0efec; --viz-ink2:#c3c2b7;
  --viz-muted:#898781; --viz-grid:#2c2c2a; --viz-axis:#383835; --viz-ring:rgba(255,255,255,.10);
  --viz-s1:#3987e5; --viz-s2:#d95926; --viz-s3:#199e70; }
.viz-fig { background: var(--viz-surface); color: var(--viz-ink); border: 1px solid var(--viz-ring); border-radius: 12px;
  padding: 14px 16px 12px; margin: 14px 0; line-height: 1.35; }
.viz-kicker { font-size: .7rem; color: var(--viz-muted); text-transform: uppercase; letter-spacing: .05em; }
.viz-title { font-size: 1rem; font-weight: 600; margin: 2px 0 1px; }
.viz-sub { font-size: .78rem; color: var(--viz-ink2); }
.viz-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; margin: 12px 0 4px; }
.viz-tile { border: 1px solid var(--viz-ring); border-radius: 10px; padding: 8px 10px; min-width: 0; }
.viz-tile .l { font-size: .72rem; color: var(--viz-ink2); }
.viz-tile .v { font-size: 1.1rem; font-weight: 600; margin: 1px 0; overflow-wrap: anywhere; }
.viz-tile .d { font-size: .7rem; color: var(--viz-muted); overflow-wrap: anywhere; }
.viz-legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: .78rem; color: var(--viz-ink2); margin: 10px 0 2px; }
.viz-legend .k { display: inline-block; width: 14px; height: 2px; border-radius: 1px; vertical-align: middle; margin-right: 6px; }
.viz-legend .k.dot { width: 8px; height: 8px; border-radius: 50%; }
.viz-plot { position: relative; margin-top: 6px; }
.viz-plot svg { display: block; width: 100%; overflow: visible; }
.viz-plot svg:focus { outline: none; }
.viz-plot svg:focus-visible { outline: 2px solid var(--viz-s1); outline-offset: 2px; border-radius: 4px; }
.viz-axis { font-size: 11px; fill: var(--viz-muted); font-variant-numeric: tabular-nums; }
.viz-lbl { font-size: 11px; fill: var(--viz-ink2); }
.viz-tip { position: absolute; display: none; pointer-events: none; z-index: 5; background: var(--viz-surface); color: var(--viz-ink);
  border: 1px solid var(--viz-ring); border-radius: 8px; box-shadow: 0 6px 18px rgba(0,0,0,.14); padding: 6px 9px; font-size: .76rem;
  max-width: 280px; }
.viz-tip .h { color: var(--viz-ink2); margin-bottom: 3px; }
.viz-tip .r { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
.viz-tip .r .key { width: 12px; height: 2px; border-radius: 1px; flex: none; }
.viz-tip .r .key.dot { width: 8px; height: 8px; border-radius: 50%; }
.viz-tip b { font-weight: 600; }
.viz-tip .s { color: var(--viz-ink2); white-space: normal; }
.viz-bars { margin-top: 10px; }
.viz-bars .row { display: grid; grid-template-columns: minmax(84px, 36%) 1fr; gap: 10px; align-items: center; padding: 2px 0;
  font-size: .78rem; border-radius: 4px; }
.viz-bars .row:focus { outline: 2px solid var(--viz-s1); outline-offset: 1px; }
.viz-bars .name { color: var(--viz-ink2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.viz-bars .track { display: flex; align-items: center; min-width: 0; }
.viz-bars .bar { height: 14px; background: var(--viz-s1); border-radius: 0 4px 4px 0; flex: none; transition: filter .15s; }
.viz-bars .row:hover .bar, .viz-bars .row:focus .bar { filter: brightness(1.15); }
.viz-bars .val { font-size: .72rem; color: var(--viz-ink2); margin-left: 6px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.viz-foot { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-top: 10px; }
.viz-note { font-size: .7rem; color: var(--viz-muted); }
.viz-btn { font: inherit; font-size: .72rem; color: var(--viz-ink2); background: none; border: 1px solid var(--viz-ring);
  border-radius: 6px; padding: 2px 8px; cursor: pointer; white-space: nowrap; }
.viz-btn:hover { color: var(--viz-ink); }
.viz-tablewrap { max-height: 260px; overflow: auto; margin-top: 8px; }
.viz-table { width: 100%; border-collapse: collapse; font-size: .76rem; font-variant-numeric: tabular-nums; }
.viz-table th { text-align: left; font-weight: 600; color: var(--viz-ink2); position: sticky; top: 0; background: var(--viz-surface); }
.viz-table th, .viz-table td { padding: 3px 6px; border-bottom: 1px solid var(--viz-grid); vertical-align: top; }
.viz-table td.num { text-align: right; }
.viz-pyimg { background: #fcfcfb; border-radius: 8px; padding: 6px; margin-top: 10px; }
.viz-pyimg img { display: block; width: 100%; height: auto; }
.viz-links { display: flex; flex-wrap: wrap; gap: 6px; }
.viz-links a.viz-btn { text-decoration: none; }
.viz-codewrap { margin-top: 8px; }
.viz-codehead { font-size: .7rem; color: var(--viz-muted); margin: 10px 0 4px; text-transform: uppercase; letter-spacing: .04em; }
.viz-code { font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: var(--viz-ink);
  background: var(--viz-ring); border-radius: 8px; padding: 10px 12px; margin: 0; max-height: 360px; overflow: auto;
  white-space: pre; tab-size: 4; }
`;

  // ---------------------------------------------------------------- utilitários

  function ensureStyles() {
    if (document.getElementById("viz-styles")) return;
    const st = document.createElement("style");
    st.id = "viz-styles";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function s(tag, attrs, text) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    if (text != null) e.textContent = text;
    return e;
  }

  function parseDate(iso) {
    const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
    return Date.UTC(y, (m || 1) - 1, d || 1);
  }

  function niceMax(v) {
    if (!(v > 0)) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }

  function yearTicks(t0, t1, width) {
    const y0 = new Date(t0).getUTCFullYear(), y1 = new Date(t1).getUTCFullYear();
    const maxTicks = Math.max(2, Math.floor(width / 64));
    const step = [1, 2, 5, 10, 20, 25, 50].find((st) => (y1 - y0) / st <= maxTicks) || 100;
    const out = [];
    for (let y = Math.ceil(y0 / step) * step; y <= y1; y += step) {
      const t = Date.UTC(y, 0, 1);
      if (t >= t0 && t <= t1) out.push({ t, label: String(y) });
    }
    return out;
  }

  function fmtDate(iso, precision) {
    const [y, m, d] = iso.slice(0, 10).split("-");
    return precision === "dia" ? `${d}/${m}/${y}` : y;
  }

  function onResize(node, draw) {
    let frame = 0, last = 0;
    const run = () => {
      frame = 0;
      const w = Math.round(node.clientWidth);
      if (w && w !== last) { last = w; draw(w); }
    };
    if ("ResizeObserver" in window) new ResizeObserver(() => { if (!frame) frame = requestAnimationFrame(run); }).observe(node);
    requestAnimationFrame(run);
  }

  function tipRow(color, value, label, dot) {
    const r = el("div", "r");
    const k = el("span", dot ? "key dot" : "key");
    k.style.background = color;
    r.append(k, el("b", null, value), el("span", "s", label));
    return r;
  }

  function placeTip(tip, plot, x, y) {
    tip.style.display = "block";
    const w = tip.offsetWidth, h = tip.offsetHeight, W = plot.clientWidth;
    let left = x + 12;
    if (left + w > W) left = Math.max(0, x - w - 12);
    tip.style.left = `${left}px`;
    tip.style.top = `${Math.max(0, y - h - 8)}px`;
  }

  function legend(items, dot) {
    const lg = el("div", "viz-legend");
    for (const it of items) {
      const span = el("span");
      const k = el("span", dot ? "k dot" : "k");
      k.style.background = it.cor;
      span.append(k, document.createTextNode(it.nome));
      lg.appendChild(span);
    }
    return lg;
  }

  function table(head, rows, numericCols) {
    const wrap = el("div", "viz-tablewrap");
    const t = el("table", "viz-table");
    const tr = el("tr");
    head.forEach((h) => tr.appendChild(el("th", null, h)));
    t.appendChild(el("thead")).appendChild(tr);
    const body = el("tbody");
    for (const r of rows) {
      const row = el("tr");
      r.forEach((c, i) => row.appendChild(el("td", numericCols.includes(i) ? "num" : null, c)));
      body.appendChild(row);
    }
    t.appendChild(body);
    wrap.appendChild(t);
    wrap.hidden = true;
    return wrap;
  }

  function footer(fig, note, tbl) {
    const foot = el("div", "viz-foot");
    foot.appendChild(el("div", "viz-note", note || ""));
    const btn = el("button", "viz-btn", "Ver dados");
    btn.type = "button";
    btn.setAttribute("aria-expanded", "false");
    btn.addEventListener("click", () => {
      tbl.hidden = !tbl.hidden;
      btn.textContent = tbl.hidden ? "Ver dados" : "Ocultar dados";
      btn.setAttribute("aria-expanded", String(!tbl.hidden));
    });
    foot.appendChild(btn);
    fig.append(foot, tbl);
  }

  // ---------------------------------------------------------------- linha do tempo

  function drawLine(plot, tip, spec, W) {
    plot.querySelector("svg")?.remove();
    const series = spec.series.map((sr, i) => ({
      ...sr,
      cor: SERIES[i],
      pts: sr.pontos.map((p) => ({ ...p, t: parseDate(p.x) })).sort((a, b) => a.t - b.t),
    }));
    const multi = series.length > 1;
    const H = 230, m = { t: 14, r: 14, b: 26, l: 40 };
    const yMax = niceMax(Math.max(...series.flatMap((sr) => sr.pts.map((p) => p.y))));
    const ph = H - m.t - m.b;
    const Y = (v) => m.t + (1 - v / yMax) * ph;
    // rótulos no fim das linhas só quando cabem sem colidir; senão a legenda basta (sem margem vazia)
    const endYs = series.map((sr) => Y(sr.pts[sr.pts.length - 1].y)).sort((a, b) => a - b);
    const endLabels = multi && W >= 520 && !endYs.some((y, i) => i && y - endYs[i - 1] < 13);
    if (endLabels) m.r = 104;
    let t0 = Math.min(...series.map((sr) => sr.pts[0].t));
    let t1 = Math.max(...series.map((sr) => sr.pts[sr.pts.length - 1].t));
    if (t1 - t0 < YEAR) { t0 -= YEAR / 2; t1 += YEAR / 2; }
    const pw = W - m.l - m.r;
    const X = (t) => m.l + ((t - t0) / (t1 - t0)) * pw;
    const gap = spec.granularidade === "sessao" ? 150 * 864e5 : 3 * YEAR; // quebra a linha entre seminários/lacunas

    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", tabindex: "0",
      "aria-label": `${spec.titulo}. Use as setas para percorrer os pontos.` });  // prettier-ignore
    const grid = s("g", { class: "viz-axis" });
    for (let i = 0; i <= 4; i++) {
      const v = (yMax / 4) * i, y = Y(v);
      grid.appendChild(s("line", { x1: m.l, x2: W - m.r, y1: y, y2: y, stroke: i ? "var(--viz-grid)" : "var(--viz-axis)", "stroke-width": 1 }));
      grid.appendChild(s("text", { x: m.l - 6, y: y + 3.5, "text-anchor": "end" }, num.format(v)));
    }
    for (const tk of yearTicks(t0, t1, pw)) {
      grid.appendChild(s("line", { x1: X(tk.t), x2: X(tk.t), y1: H - m.b, y2: H - m.b + 4, stroke: "var(--viz-axis)" }));
      grid.appendChild(s("text", { x: X(tk.t), y: H - m.b + 16, "text-anchor": "middle" }, tk.label));
    }
    svg.appendChild(grid);

    for (const sr of series) {
      const segs = [[]];
      sr.pts.forEach((p, i) => {
        if (i && p.t - sr.pts[i - 1].t > gap) segs.push([]);
        segs[segs.length - 1].push(p);
      });
      for (const seg of segs) {
        if (seg.length === 1) {
          svg.appendChild(s("circle", { cx: X(seg[0].t), cy: Y(seg[0].y), r: 3, fill: sr.cor }));
          continue;
        }
        const d = seg.map((p, i) => `${i ? "L" : "M"}${X(p.t).toFixed(1)},${Y(p.y).toFixed(1)}`).join("");
        if (!multi) {
          const area = `${d}L${X(seg[seg.length - 1].t).toFixed(1)},${Y(0)}L${X(seg[0].t).toFixed(1)},${Y(0)}Z`;
          svg.appendChild(s("path", { d: area, fill: sr.cor, "fill-opacity": 0.1 }));
        }
        svg.appendChild(s("path", { d, fill: "none", stroke: sr.cor, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      }
      // pico: o único rótulo de dado no gráfico (rotulagem seletiva)
      const peak = sr.pts.reduce((a, b) => (b.y > a.y ? b : a));
      const px = X(peak.t), py = Y(peak.y);
      svg.appendChild(s("circle", { cx: px, cy: py, r: 4, fill: sr.cor, stroke: "var(--viz-surface)", "stroke-width": 2 }));
      if (!multi) {
        const anchor = px > m.l + pw * 0.7 ? "end" : px < m.l + pw * 0.3 ? "start" : "middle";
        svg.appendChild(s("text", { class: "viz-lbl", x: px, y: Math.max(11, py - 9), "text-anchor": anchor }, `pico: ${peak.rotulo}`));
      }
    }
    if (endLabels) {
      for (const sr of series) {
        const last = sr.pts[sr.pts.length - 1];
        svg.appendChild(s("text", { class: "viz-lbl", x: X(last.t) + 8, y: Y(last.y) + 4 }, sr.nome));
      }
    }

    // crosshair + tooltip (pointer e teclado)
    const xs = [...new Set(series.flatMap((sr) => sr.pts.map((p) => p.t)))].sort((a, b) => a - b);
    const cross = s("line", { y1: m.t, y2: H - m.b, stroke: "var(--viz-axis)", "stroke-width": 1, visibility: "hidden" });
    const marks = series.map((sr) => s("circle", { r: 4, fill: sr.cor, stroke: "var(--viz-surface)", "stroke-width": 2, visibility: "hidden" }));
    svg.append(cross, ...marks);
    const hit = s("rect", { x: m.l, y: m.t, width: pw, height: ph, fill: "transparent" });
    svg.appendChild(hit);
    let idx = -1;
    const nearest = (pts, t) => pts.reduce((a, b) => (Math.abs(b.t - t) < Math.abs(a.t - t) ? b : a));
    function show(t) {
      const x = X(t);
      cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
      tip.replaceChildren();
      let header = null;
      series.forEach((sr, i) => {
        const p = nearest(sr.pts, t);
        const close = Math.abs(p.t - t) <= gap / 2;
        marks[i].setAttribute("visibility", close ? "visible" : "hidden");
        if (!close) return;
        marks[i].setAttribute("cx", X(p.t)); marks[i].setAttribute("cy", Y(p.y));
        if (!header || p.t === t) header = p.rotulo;
        tip.appendChild(tipRow(sr.cor, num.format(p.y), multi ? `${sr.nome} · ${p.mencoes} menções` : `por 10 mil palavras · ${p.mencoes} menções`, false));
      });
      tip.prepend(el("div", "h", header || ""));
      placeTip(tip, plot, x, m.t + 10);
    }
    function hide() {
      cross.setAttribute("visibility", "hidden");
      marks.forEach((mk) => mk.setAttribute("visibility", "hidden"));
      tip.style.display = "none";
    }
    hit.addEventListener("pointermove", (ev) => {
      const r = svg.getBoundingClientRect();
      const t = t0 + ((ev.clientX - r.left) * (W / r.width) - m.l) / pw * (t1 - t0);
      idx = xs.indexOf(xs.reduce((a, b) => (Math.abs(b - t) < Math.abs(a - t) ? b : a)));
      show(xs[idx]);
    });
    hit.addEventListener("pointerleave", hide);
    svg.addEventListener("blur", hide);
    svg.addEventListener("keydown", (ev) => {
      if (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft") return;
      ev.preventDefault();
      idx = Math.min(xs.length - 1, Math.max(0, idx + (ev.key === "ArrowRight" ? 1 : -1)));
      show(xs[idx]);
    });
    plot.prepend(svg);
  }

  function lineFigure(fig, spec) {
    if (spec.fichas?.length) {
      const tiles = el("div", "viz-tiles");
      for (const f of spec.fichas) {
        const t = el("div", "viz-tile");
        t.append(el("div", "l", f.rotulo), el("div", "v", f.valor), el("div", "d", f.detalhe || ""));
        tiles.appendChild(t);
      }
      fig.appendChild(tiles);
    }
    if (spec.series.length > 1) fig.appendChild(legend(spec.series.map((sr, i) => ({ nome: sr.nome, cor: SERIES[i] }))));
    const plot = el("div", "viz-plot");
    const tip = el("div", "viz-tip");
    plot.appendChild(tip);
    fig.appendChild(plot);
    onResize(plot, (w) => drawLine(plot, tip, spec, w));

    // tabela: uma linha por período, uma coluna por conceito
    const keys = [...new Set(spec.series.flatMap((sr) => sr.pontos.map((p) => p.x)))].sort();
    const label = new Map(spec.series.flatMap((sr) => sr.pontos.map((p) => [p.x, p.rotulo])));
    const byKey = spec.series.map((sr) => new Map(sr.pontos.map((p) => [p.x, p])));
    const rows = keys.map((k) => [label.get(k), ...byKey.map((mp) => (mp.has(k) ? num.format(mp.get(k).y) : "—"))]);
    footer(fig, spec.nota, table(["Período", ...spec.series.map((sr) => `${sr.nome} (por 10 mil)`)], rows, spec.series.map((_, i) => i + 1)));
  }

  // ---------------------------------------------------------------- barras horizontais

  function barsFigure(fig, spec) {
    const box = el("div", "viz-bars");
    const max = Math.max(...spec.barras.map((b) => b.valor)) || 1;
    const plot = el("div", "viz-plot");
    const tip = el("div", "viz-tip");
    for (const b of spec.barras) {
      const row = el("div", "row");
      row.tabIndex = 0;
      row.setAttribute("aria-label", `${b.rotulo}: ${num.format(b.valor)} ${spec.unidade}. ${b.detalhe || ""}`);
      const name = el("div", "name", b.rotulo);
      const track = el("div", "track");
      const bar = el("div", "bar");
      bar.style.width = `calc(${(Math.max(b.valor, 0) / max) * 100}% - ${(b.valor / max) * 48}px)`;
      track.append(bar, el("span", "val", num.format(b.valor)));
      row.append(name, track);
      const show = () => {
        tip.replaceChildren(el("div", "h", b.rotulo), tipRow("var(--viz-s1)", num.format(b.valor), spec.unidade, true));
        if (b.detalhe) tip.appendChild(el("div", "s", b.detalhe));
        placeTip(tip, plot, row.offsetLeft + name.offsetWidth + bar.offsetWidth, row.offsetTop);
      };
      row.addEventListener("pointerenter", show);
      row.addEventListener("focus", show);
      row.addEventListener("pointerleave", () => (tip.style.display = "none"));
      row.addEventListener("blur", () => (tip.style.display = "none"));
      box.appendChild(row);
    }
    plot.append(box, tip);
    fig.appendChild(plot);
    const rows = spec.barras.map((b) => [b.rotulo, num.format(b.valor), b.detalhe || ""]);
    footer(fig, spec.nota, table(["Item", spec.unidade, "Detalhe"], rows, [1]));
  }

  // ---------------------------------------------------------------- figura

  function figure(spec) {
    ensureStyles();
    const fig = el("figure", "viz-root viz-fig");
    fig.style.margin = "14px 0";
    fig.appendChild(el("div", "viz-kicker", `Figura ${spec.figura}`));
    fig.appendChild(el("div", "viz-title", spec.titulo));
    if (spec.subtitulo) fig.appendChild(el("div", "viz-sub", spec.subtitulo));
    if (spec.tipo === "linha") lineFigure(fig, spec);
    else barsFigure(fig, spec);
    return fig;
  }

  // ---------------------------------------------------------------- cronologia das fontes

  function groupOf(linha) {
    return linha === "lacan" ? GROUPS[0] : linha === "freud" ? GROUPS[1] : GROUPS[2];
  }

  function drawSources(plot, tip, items, seq, W) {
    plot.querySelector("svg")?.remove();
    const m = { l: 14, r: 14, b: 24 };
    let t0 = Math.min(...items.map((i) => i.t)), t1 = Math.max(...items.map((i) => i.t));
    const pad = Math.max(YEAR, (t1 - t0) * 0.04);
    t0 -= pad; t1 += pad;
    if (t1 - t0 < 6 * YEAR) { const c = (t0 + t1) / 2; t0 = c - 3 * YEAR; t1 = c + 3 * YEAR; }
    const pw = W - m.l - m.r;
    const X = (t) => m.l + ((t - t0) / (t1 - t0)) * pw;
    // empilha pontos próximos em faixas (sem sobreposição), até 7 faixas
    const lanes = [];
    const placed = items.slice().sort((a, b) => a.t - b.t).map((it) => {
      const x = X(it.t);
      let k = lanes.findIndex((lx) => x - lx >= 13);
      if (k === -1) k = lanes.length < 7 ? lanes.length : lanes.indexOf(Math.min(...lanes));
      lanes[k] = x;
      return { ...it, x, k };
    });
    const H = 22 + lanes.length * 13 + m.b;
    const base = H - m.b;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": "Cronologia das fontes citadas" });
    const axis = s("g", { class: "viz-axis" });
    axis.appendChild(s("line", { x1: m.l, x2: W - m.r, y1: base, y2: base, stroke: "var(--viz-axis)", "stroke-width": 1 }));
    for (const tk of yearTicks(t0, t1, pw)) {
      axis.appendChild(s("line", { x1: X(tk.t), x2: X(tk.t), y1: base, y2: base + 4, stroke: "var(--viz-axis)" }));
      axis.appendChild(s("text", { x: X(tk.t), y: base + 16, "text-anchor": "middle" }, tk.label));
    }
    svg.appendChild(axis);
    for (const p of placed) {
      const cy = base - 9 - p.k * 13;
      const g = s("g", { tabindex: "0", role: "button", style: "cursor:pointer",
        "aria-label": `Nota ${p.n}: ${p.citacao}` });  // prettier-ignore
      g.append(
        s("circle", { cx: p.x, cy, r: 12, fill: "transparent" }),
        s("circle", { cx: p.x, cy, r: 5, fill: p.group.cor, stroke: "var(--viz-surface)", "stroke-width": 2 }),
      );
      const show = () => {
        tip.replaceChildren(tipRow(p.group.cor, `[${p.n}]`, p.quando, true), el("div", "s", p.citacao));
        placeTip(tip, plot, p.x, cy);
      };
      const go = () => {
        const note = document.getElementById(`n-${seq}-${p.n}`);
        if (!note) return;
        note.scrollIntoView({ behavior: "smooth", block: "center" });
        note.animate([{ background: "rgba(42,120,214,.18)" }, { background: "transparent" }], { duration: 1600 });
      };
      g.addEventListener("pointerenter", show);
      g.addEventListener("focus", show);
      g.addEventListener("pointerleave", () => (tip.style.display = "none"));
      g.addEventListener("blur", () => (tip.style.display = "none"));
      g.addEventListener("click", go);
      g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); } });
      svg.appendChild(g);
    }
    plot.prepend(svg);
  }

  function sources(citacoes, seq) {
    ensureStyles();
    const items = citacoes
      .filter((c) => c.periodo)
      .map((c) => ({ n: c.n, t: parseDate(c.periodo), quando: fmtDate(c.periodo, c.precisao), citacao: c.citacao, group: groupOf(c.linha) }));
    if (items.length < 2) return null;
    const fig = el("figure", "viz-root viz-fig");
    fig.style.margin = "14px 0";
    fig.appendChild(el("div", "viz-kicker", "Fontes desta resposta"));
    fig.appendChild(el("div", "viz-title", "Cronologia das fontes citadas"));
    const undated = citacoes.length - items.length;
    fig.appendChild(el("div", "viz-sub",
      `Data do texto: sessão do seminário ou ano da obra · clique num ponto para ir à nota${undated ? ` · ${undated} nota(s) sem data` : ""}`));  // prettier-ignore
    const present = GROUPS.filter((g) => items.some((i) => i.group === g));
    fig.appendChild(legend(present, true));
    const plot = el("div", "viz-plot");
    const tip = el("div", "viz-tip");
    plot.appendChild(tip);
    fig.appendChild(plot);
    onResize(plot, (w) => drawSources(plot, tip, items, seq, w));
    const rows = items.slice().sort((a, b) => a.t - b.t).map((i) => [`[${i.n}]`, i.quando, i.citacao]);
    footer(fig, "", table(["Nota", "Data", "Fonte"], rows, []));
    return fig;
  }

  // ---------------------------------------------------------------- análise em Python

  function python(ev) {
    ensureStyles();
    const fig = el("figure", "viz-root viz-fig");
    fig.style.margin = "14px 0";
    fig.appendChild(el("div", "viz-kicker", `Análise em Python ${ev.indice}`));
    const images = ev.arquivos.filter((a) => a.mime.startsWith("image/"));
    if (!images.length) fig.appendChild(el("div", "viz-sub", "Análise sem gráfico: veja o código e o resultado."));
    for (const a of images) {
      const frame = el("div", "viz-pyimg");
      const img = document.createElement("img");
      img.src = `data:${a.mime};base64,${a.base64}`;
      img.alt = `Gráfico gerado em Python (${a.nome})`;
      frame.appendChild(img);
      fig.appendChild(frame);
    }
    const foot = el("div", "viz-foot");
    const links = el("div", "viz-links");
    const ordered = [...ev.arquivos].sort((a, b) => Number(a.mime === "text/csv") - Number(b.mime === "text/csv"));
    for (const a of ordered) {
      const link = el("a", "viz-btn", a.mime === "text/csv" ? `Baixar dados (${a.nome})` : `Baixar ${a.nome}`);
      link.href = `data:${a.mime};base64,${a.base64}`;
      link.download = a.nome;
      links.appendChild(link);
    }
    foot.appendChild(links);
    const code = el("div", "viz-codewrap");
    code.hidden = true;
    ev.celulas.forEach((c, i) => {
      code.appendChild(el("div", "viz-codehead", `${c.linguagem === "bash" ? "Comando" : "Célula"} ${i + 1} · ${c.linguagem}`));
      code.appendChild(el("pre", "viz-code", c.codigo));
    });
    if (ev.saida) {
      code.appendChild(el("div", "viz-codehead", "Saída"));
      code.appendChild(el("pre", "viz-code", ev.saida));
    }
    if (ev.celulas.length || ev.saida) {
      const btn = el("button", "viz-btn", "Ver código");
      btn.type = "button";
      btn.setAttribute("aria-expanded", "false");
      btn.addEventListener("click", () => {
        code.hidden = !code.hidden;
        btn.textContent = code.hidden ? "Ver código" : "Ocultar código";
        btn.setAttribute("aria-expanded", String(!code.hidden));
      });
      foot.appendChild(btn);
    }
    fig.append(foot, code);
    return fig;
  }

  window.Viz = { figure, sources, python };
})();
