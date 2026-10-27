// Dashboard: loads /api/overview and draws everything as SVG/DOM. No libraries, no inline code, no innerHTML:
// every value from the server goes through textContent or an attribute.
(() => {
  const SVG = "http://www.w3.org/2000/svg";
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const nf = new Intl.NumberFormat("fr-FR");
  const $ = (id) => document.getElementById(id);

  // ---- tiny DOM helpers ----------------------------------------------------------------------------------------
  const assign = (node, attrs) => { for (const [k, v] of Object.entries(attrs || {})) if (v !== undefined && v !== null) node.setAttribute(k, String(v)); return node; };
  const add = (node, kids) => { for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) node.append(kid.nodeType ? kid : document.createTextNode(String(kid))); return node; };
  const h = (tag, attrs, ...kids) => add(assign(document.createElement(tag), attrs), kids);
  const s = (tag, attrs, ...kids) => add(assign(document.createElementNS(SVG, tag), attrs), kids);
  const icon = (name, cls) => s("svg", { class: "icon " + (cls || ""), "aria-hidden": "true" }, s("use", { href: `/static/icons.svg#${name}` }));
  const clear = (node) => node.replaceChildren();

  // ---- formatting ------------------------------------------------------------------------------------------------
  const DAYS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"];
  const DAYS_LONG = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
  const hour = (ts) => String(new Date(ts * 1000).getHours()).padStart(2, "0") + "h";
  const dayShort = (ts) => new Date(ts * 1000).toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" });
  const dateLong = (ts, bucketHours) => {
    const d = new Date(ts * 1000);
    const day = d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" });
    return bucketHours >= 24 ? day : `${day} · ${hour(ts)}`;
  };
  function relative(ts) {
    const seconds = Math.max(0, Date.now() / 1000 - ts);
    if (seconds < 45) return "à l'instant";
    if (seconds < 3600) return `il y a ${Math.round(seconds / 60)} min`;
    if (seconds < 86400) return `il y a ${Math.round(seconds / 3600)} h`;
    return new Date(ts * 1000).toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
  }
  const PERIOD_TEXT = { "24h": "dernières 24 heures", "7j": "7 derniers jours", "30j": "30 derniers jours" };
  const GRANULARITY = { "24h": "heure par heure", "7j": "par tranches de 6 heures", "30j": "jour par jour" };

  // ---- geometry: a smooth curve through the points (monotone cubic keeps it from overshooting) ------------------
  function smoothPath(points) {
    if (points.length < 2) return points.length ? `M${points[0][0]},${points[0][1]}` : "";
    const n = points.length, dx = [], dy = [], m = [];
    for (let i = 0; i < n - 1; i++) { dx.push(points[i + 1][0] - points[i][0]); dy.push(points[i + 1][1] - points[i][1]); m.push(dy[i] / dx[i]); }
    const t = [m[0]];
    for (let i = 1; i < n - 1; i++) t.push(m[i - 1] * m[i] <= 0 ? 0 : 3 * (dx[i - 1] + dx[i]) / ((2 * dx[i] + dx[i - 1]) / m[i - 1] + (dx[i] + 2 * dx[i - 1]) / m[i]));
    t.push(m[n - 2]);
    let d = `M${points[0][0]},${points[0][1]}`;
    for (let i = 0; i < n - 1; i++) {
      const x1 = points[i][0] + dx[i] / 3, y1 = points[i][1] + t[i] * dx[i] / 3;
      const x2 = points[i + 1][0] - dx[i] / 3, y2 = points[i + 1][1] - t[i + 1] * dx[i] / 3;
      d += ` C${x1.toFixed(1)},${y1.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)} ${points[i + 1][0].toFixed(1)},${points[i + 1][1].toFixed(1)}`;
    }
    return d;
  }
  function niceMax(value) {
    if (value <= 4) return 4;
    const pow = Math.pow(10, Math.floor(Math.log10(value)));
    for (const step of [1, 2, 2.5, 5, 10]) if (step * pow >= value) return step * pow;
    return value;
  }

  // ---- KPI cards -----------------------------------------------------------------------------------------------------
  function setCount(el, value, suffix) {
    if (!el) return;
    const previous = el.dataset.count;
    el.dataset.count = String(value);
    if (previous === String(value)) return;
    window.facegate.countUp(el, value, suffix || "");
  }
  function delta(el, current, previous, goodWhenUp) {
    clear(el);
    el.className = "delta";
    if (previous === 0 && current === 0) { el.textContent = "stable"; return; }
    if (previous === 0) { el.textContent = "nouveau"; return; }
    const pct = Math.round(((current - previous) / previous) * 100);
    if (pct === 0) { el.textContent = "stable"; return; }
    const up = pct > 0;
    el.classList.add(up === goodWhenUp ? "good" : "bad");
    el.append(icon(up ? "arrow-up" : "arrow-down"), `${up ? "+" : ""}${pct} %`);
  }
  function sparkline(svg, values, colorVar) {
    clear(svg);
    if (!values.length || Math.max(...values) === 0) { svg.append(s("path", { d: "M0,36 L112,36", stroke: "var(--border-strong)", "stroke-width": 2, "stroke-linecap": "round", fill: "none" })); return; }
    const max = Math.max(...values), w = 112, hgt = 36;
    const pts = values.map((v, i) => [(i / (values.length - 1)) * w, hgt - (v / max) * (hgt - 4) - 1]);
    const line = smoothPath(pts);
    svg.append(
      s("path", { class: "fill", d: `${line} L${w},${hgt} L0,${hgt} Z`, fill: colorVar, "fill-opacity": ".14" }),
      s("path", { class: "line", d: line, stroke: colorVar }));
  }
  function renderKpis(d) {
    setCount($("kpi-granted"), d.totals.granted);
    setCount($("kpi-denied"), d.totals.denied);
    setCount($("kpi-alerts"), d.alerts);
    setCount($("kpi-people"), d.people);
    delta($("delta-granted"), d.totals.granted, d.previous.granted, true);
    delta($("delta-denied"), d.totals.denied, d.previous.denied, false);
    sparkline($("spark-granted"), d.granted, "var(--ok)");
    sparkline($("spark-denied"), d.denied, "var(--bad)");
    const total = d.totals.granted + d.totals.denied;
    $("kpi-rate").textContent = `${total ? Math.round((d.totals.denied / total) * 100) : 0} % de refus · ${PERIOD_TEXT[d.period]}`;
  }

  // ---- main chart: two smooth area lines, hover crosshair and tooltip ------------------------------------------------
  function renderChart(d, animate) {
    const box = $("chart-box");
    clear(box);
    box.classList.toggle("is-static", !animate);
    const W = 760, H = 280, L = 40, R = 14, T = 14, B = 30, n = d.granted.length;
    const max = niceMax(Math.max(1, ...d.granted, ...d.denied));
    const x = (i) => L + (n === 1 ? 0 : (i / (n - 1)) * (W - L - R));
    const y = (v) => T + (1 - v / max) * (H - T - B);
    const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Courbe des passages autorisés et refusés" });
    svg.append(s("defs", {},
      s("linearGradient", { id: "gOk", x1: 0, y1: 0, x2: 0, y2: 1 }, s("stop", { offset: "0%", class: "stop-ok", "stop-opacity": ".34" }), s("stop", { offset: "100%", class: "stop-ok", "stop-opacity": "0" })),
      s("linearGradient", { id: "gBad", x1: 0, y1: 0, x2: 0, y2: 1 }, s("stop", { offset: "0%", class: "stop-bad", "stop-opacity": ".28" }), s("stop", { offset: "100%", class: "stop-bad", "stop-opacity": "0" }))));
    for (let g = 0; g <= 4; g++) {
      const v = (max / 4) * g, yy = y(v);
      svg.append(s("line", { class: "grid-line", x1: L, x2: W - R, y1: yy, y2: yy }), s("text", { x: L - 8, y: yy + 4, "text-anchor": "end" }, nf.format(Math.round(v))));
    }
    // x labels: about seven, on meaningful boundaries
    const every = Math.max(1, Math.round(n / 7));
    d.starts.forEach((ts, i) => {
      if (i % every !== 0 && i !== n - 1) return;
      if (i === n - 1 && i % every !== 0 && (n - 1 - Math.floor((n - 1) / every) * every) < every / 2) return;
      const label = d.bucket_hours >= 24 ? dayShort(ts) : (d.bucket_hours === 1 ? hour(ts) : `${DAYS[(new Date(ts * 1000).getDay() + 6) % 7]} ${hour(ts)}`);
      svg.append(s("text", { x: x(i), y: H - 8, "text-anchor": "middle" }, label));
    });
    const series = [["granted", "ok", "gOk"], ["denied", "bad", "gBad"]];
    for (const [key, tone, grad] of series) {
      const pts = d[key].map((v, i) => [x(i), y(v)]);
      const line = smoothPath(pts);
      svg.append(
        s("path", { class: "area-fill", d: `${line} L${x(n - 1)},${y(0)} L${x(0)},${y(0)} Z`, fill: `url(#${grad})` }),
        s("path", { class: `area-line line-${tone}`, d: line }));
    }
    const cursor = s("line", { class: "cursor", y1: T, y2: H - B });
    const dotOk = s("circle", { class: "cursor-dot line-ok-fill", r: 5 }), dotBad = s("circle", { class: "cursor-dot line-bad-fill", r: 5 });
    const hit = s("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent" });
    svg.append(cursor, dotOk, dotBad, hit);
    const tip = h("div", { class: "tooltip" }, h("strong", {}, ""), h("div", {}, h("span", {}, h("i", { class: "bg-ok" }), "Autorisés"), h("b", {}, "0")), h("div", {}, h("span", {}, h("i", { class: "bg-bad" }), "Refusés"), h("b", {}, "0")));
    box.append(svg, tip);
    const move = (event) => {
      const rect = svg.getBoundingClientRect();
      const px = ((event.clientX - rect.left) / rect.width) * W;
      const i = Math.min(n - 1, Math.max(0, Math.round(((px - L) / (W - L - R)) * (n - 1))));
      const cx = x(i);
      assign(cursor, { x1: cx, x2: cx });
      assign(dotOk, { cx, cy: y(d.granted[i]) });
      assign(dotBad, { cx, cy: y(d.denied[i]) });
      tip.querySelector("strong").textContent = dateLong(d.starts[i], d.bucket_hours);
      const values = tip.querySelectorAll("b");
      values[0].textContent = nf.format(d.granted[i]);
      values[1].textContent = nf.format(d.denied[i]);
      const left = (cx / W) * rect.width, flip = left > rect.width * 0.62;
      tip.style.transform = `translate(${flip ? left - tip.offsetWidth - 14 : left + 14}px, ${Math.max(0, (y(Math.max(d.granted[i], d.denied[i])) / H) * rect.height - 20)}px)`;
      box.classList.add("is-hover");
    };
    hit.addEventListener("pointermove", move);
    hit.addEventListener("pointerleave", () => box.classList.remove("is-hover"));
    $("chart-sub").textContent = `Accès autorisés et refusés, ${GRANULARITY[d.period]}`;
  }

  // ---- donut: why accesses were refused ------------------------------------------------------------------------------
  const REASON_COLORS = ["#fb7185", "#fbbf24", "#a78bfa", "#60a5fa", "#34d399", "#f472b6", "#94a3b8"];
  function renderDonut(d) {
    const box = $("donut-box");
    clear(box);
    const entries = Object.entries(d.reasons);
    const total = entries.reduce((sum, [, n]) => sum + n, 0);
    if (!total) {
      box.append(h("div", { class: "empty-note" }, icon("shield", "icon-large"), h("p", {}, "Aucun refus sur la période.")));
      return;
    }
    const R = 62, C = 2 * Math.PI * R;
    const svg = s("svg", { class: "donut", viewBox: "0 0 170 170", role: "img", "aria-label": "Répartition des motifs de refus" });
    svg.append(s("circle", { cx: 85, cy: 85, r: R, fill: "none", stroke: "var(--surface-3)", "stroke-width": 18 }));
    const legend = h("ul", { class: "legend" });
    const segments = [];
    let offset = 0;
    entries.forEach(([reason, n], i) => {
      const length = (n / total) * C, color = REASON_COLORS[Math.min(i, REASON_COLORS.length - 1)];
      const seg = s("circle", { class: "seg", cx: 85, cy: 85, r: R, stroke: color, "stroke-dasharray": `${Math.max(0, length - 2)} ${C}`, "stroke-dashoffset": -offset, transform: "rotate(-90 85 85)" });
      segments.push(seg);
      svg.append(seg);
      offset += length;
      const item = h("li", {}, h("i", { class: "legend-dot" }), h("span", { class: "legend-name" }, d.reason_labels[reason] || reason), h("b", {}, nf.format(n)));
      item.querySelector("i").style.background = color;
      item.addEventListener("pointerenter", () => segments.forEach((sg, j) => { sg.classList.toggle("is-active", j === i); sg.classList.toggle("is-dim", j !== i); }));
      item.addEventListener("pointerleave", () => segments.forEach((sg) => sg.classList.remove("is-active", "is-dim")));
      legend.append(item);
    });
    svg.append(s("text", { class: "donut-center", x: 85, y: 88 }, nf.format(total)), s("text", { class: "donut-sub", x: 85, y: 106 }, "refus"));
    box.append(h("div", { class: "donut-wrap" }, svg, legend));
  }

  // ---- heatmap: weekday x hour ---------------------------------------------------------------------------------------
  function renderHeatmap(d) {
    const box = $("heat-box");
    clear(box);
    const { grid, max, total } = d.heatmap;
    if (!total) { box.append(h("p", { class: "empty-note" }, "Pas encore assez d'accès pour dessiner une tendance.")); return; }
    const W = 760, left = 34, gap = 3, cw = (W - left) / 24 - gap, ch = 22, top = 16, H = top + 7 * (ch + gap) + 4;
    const svg = s("svg", { class: "heatmap", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Carte de chaleur de la fréquentation" });
    for (let hr = 0; hr < 24; hr += 3) svg.append(s("text", { x: left + hr * (cw + gap) + cw / 2, y: 9, "text-anchor": "middle" }, `${hr}h`));
    grid.forEach((row, di) => {
      svg.append(s("text", { x: 0, y: top + di * (ch + gap) + ch / 2 + 4 }, DAYS[di]));
      row.forEach((count, hr) => {
        const t = max ? count / max : 0;
        const cell = s("rect", { class: "cell", x: left + hr * (cw + gap), y: top + di * (ch + gap), width: cw, height: ch, rx: 5,
          fill: count ? "var(--accent)" : "var(--surface-2)", "fill-opacity": count ? (0.16 + 0.84 * Math.pow(t, 0.65)).toFixed(2) : 1 },
          s("title", {}, `${DAYS_LONG[di]} ${String(hr).padStart(2, "0")}h : ${count} accès`));
        svg.append(cell);
      });
    });
    const ramp = h("span", { class: "ramp" });
    [0.16, 0.35, 0.55, 0.78, 1].forEach((o) => { const i = h("i"); i.style.background = "var(--accent)"; i.style.opacity = String(o); ramp.append(i); });
    box.append(svg, h("div", { class: "heat-legend" }, "moins", ramp, "plus"));
  }

  // ---- activity feed: new events slide in --------------------------------------------------------------------------
  const seen = new Set();
  let feedReady = false;
  function feedItem(e, isNew) {
    const granted = e.decision === "granted";
    const li = h("li", { class: "feed-item" + (isNew ? " is-new" : ""), "data-id": e.id },
      h("span", { class: "feed-icon " + (granted ? "ok" : "bad") }, icon(granted ? "check" : "x")),
      h("div", { class: "feed-main" }, h("strong", {}, e.person || "Inconnu"), h("span", {}, `${e.zone || "—"} · ${e.reason_label || "Accès autorisé"}`)),
      h("span", { class: "feed-time", "data-at": e.at }, relative(e.at)));
    return li;
  }
  function renderFeed(events) {
    const list = $("feed");
    if (!events.length) return;
    if (!feedReady) {
      clear(list);
      events.slice(0, 8).forEach((e) => { seen.add(e.id); list.append(feedItem(e, false)); });
      feedReady = true;
      return;
    }
    const fresh = events.filter((e) => !seen.has(e.id)).reverse();
    fresh.forEach((e) => { seen.add(e.id); list.prepend(feedItem(e, true)); });
    while (list.children.length > 8) list.lastElementChild.remove();
    list.querySelectorAll(".feed-time").forEach((t) => { t.textContent = relative(Number(t.dataset.at)); });
  }
  // the server already drew the first rows (for people without scripts): remember them so only new ones animate
  document.querySelectorAll("#feed .feed-item").forEach((li) => seen.add(Number(li.dataset.id)));

  // ---- security posture (managers and owners) ----------------------------------------------------------------------
  function renderPosture(p) {
    const box = $("posture-box");
    if (!box || !p) return;
    clear(box);
    const tone = p.score >= 80 ? "ok" : p.score >= 55 ? "warn" : "bad";
    const C = 2 * Math.PI * 52;
    const value = s("circle", { class: "value", cx: 62, cy: 62, r: 52, transform: "rotate(-90 62 62)", stroke: `var(--${tone})` });
    const ring = s("svg", { class: "score-ring", viewBox: "0 0 124 124", role: "img", "aria-label": `Score de sécurité : ${p.score} sur 100` },
      s("circle", { class: "track", cx: 62, cy: 62, r: 52 }), value,
      s("text", { class: "score-num", x: 62, y: 68 }, String(p.score)), s("text", { class: "score-sub", x: 62, y: 84 }, "sur 100"));
    const checks = h("ul", { class: "checks" });
    p.items.forEach((item) => {
      const body = h("div", {}, h("strong", {}, item.title), h("span", { class: "hint" }, item.hint));
      const row = h("li", { class: "check-item " + item.state }, h("span", { class: "state" }, icon(item.state === "ok" ? "check" : item.state === "warn" ? "alert" : "x")), body);
      checks.append(row);
    });
    const verdict = p.score >= 80 ? "Bonne posture" : p.score >= 55 ? "À améliorer" : "Action requise";
    box.append(h("div", { class: "posture" }, ring, h("div", {}, h("h3", {}, verdict), h("p", { class: "muted small" }, "Chaque point ci-dessous indique ce qui reste à faire."))), checks);
    requestAnimationFrame(() => requestAnimationFrame(() => { value.style.strokeDashoffset = String(C * (1 - p.score / 100)); }));
  }

  // ---- camera card -------------------------------------------------------------------------------------------------------
  let miniSrc = false;
  function renderService(service) {
    const box = $("service-box");
    if (!box || !service) return;
    const online = service.enabled && service.online;
    let frame = box.querySelector(".mini-video");
    if (!frame) {
      clear(box);
      frame = h("div", { class: "mini-video" }, h("img", { alt: "Flux de la caméra de contrôle", hidden: true }), h("div", { class: "veil" }));
      box.append(frame, h("div", { class: "health", id: "health" }));
    }
    const img = frame.querySelector("img"), veil = frame.querySelector(".veil");
    clear(veil);
    if (online) {
      if (!miniSrc) { img.src = "/live/stream"; miniSrc = true; }
      img.hidden = false; veil.hidden = true;
    } else {
      img.hidden = true; veil.hidden = false; miniSrc = false; img.removeAttribute("src");
      veil.append(icon("camera-off", "icon-large"), h("strong", {}, service.enabled ? "Service arrêté" : "Vidéo non activée"),
        h("span", {}, service.enabled ? "Lancez la reconnaissance pour reprendre la vidéo." : "Aucune image n'est partagée (option --live-dir)."));
    }
    const health = box.querySelector("#health");
    clear(health);
    const row = (label, value, tone) => h("div", { class: "health-row" }, h("span", {}, label), tone ? h("span", { class: `chip chip-${tone}` }, value) : h("b", {}, value));
    health.append(
      row("Service", service.enabled ? (online ? "En marche" : "Arrêté") : "Non connecté", service.enabled ? (online ? "ok" : "bad") : "off"),
      row("Cadence", online && service.fps ? `${service.fps} img/s` : "—"),
      row("Porte", online && service.door ? (service.door.open ? "Ouverte" : "Fermée") : "—", online && service.door ? (service.door.open ? "ok" : "off") : null));
  }

  // ---- loading ---------------------------------------------------------------------------------------------------------
  let period = "24h", busy = false;
  async function load(animate) {
    if (busy) return;
    busy = true;
    try {
      const response = await fetch(`/api/overview?period=${encodeURIComponent(period)}`, { credentials: "same-origin", cache: "no-store" });
      if (response.status === 401) { window.location.href = "/login"; return; }
      if (!response.ok) return;
      const d = await response.json();
      renderKpis(d);
      renderChart(d, animate);
      renderDonut(d);
      if (animate || !$("heat-box").firstChild) renderHeatmap(d);
      renderFeed(d.events);
      renderPosture(d.posture);
      renderService(d.service);
    } catch { /* the server is restarting: the next tick tries again */ } finally { busy = false; }
  }
  document.querySelectorAll("#period button").forEach((button) => button.addEventListener("click", () => {
    period = button.dataset.period;
    document.querySelectorAll("#period button").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
    $("dash-sub").textContent = `Vue d'ensemble de l'activité · ${PERIOD_TEXT[period]}`;
    load(true);
  }));
  load(true);
  setInterval(() => { if (!document.hidden) load(false); }, 8000);
})();
