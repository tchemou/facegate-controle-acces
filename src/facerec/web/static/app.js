// Shell behaviour shared by every page: theme, sidebar, command palette, live status, toasts, count-up numbers.
// Strict CSP: no inline code, and nothing from the server is ever parsed as HTML (DOM API and textContent only).
(() => {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const root = document.documentElement;
  const $ = (id) => document.getElementById(id);
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* storage may be blocked */ } },
  };

  // ---- theme -----------------------------------------------------------------------------------------------
  const isLight = () => (root.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")) === "light";
  function applyTheme(next) {
    root.setAttribute("data-theme", next);
    store.set("facegate-theme", next);
  }
  // The new theme spreads as a circle from the button (View Transitions API); other browsers just switch.
  function toggleTheme(event) {
    const next = isLight() ? "dark" : "light";
    if (reduce || typeof document.startViewTransition !== "function") { applyTheme(next); return; }
    const box = event.currentTarget.getBoundingClientRect();
    const x = box.left + box.width / 2;
    const y = box.top + box.height / 2;
    const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    root.classList.add("theme-swap");
    const transition = document.startViewTransition(() => applyTheme(next));
    transition.ready.then(() => {
      root.animate(
        { clipPath: [`circle(0 at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
        { duration: 620, easing: "cubic-bezier(.2, .7, .2, 1)", pseudoElement: "::view-transition-new(root)" },
      );
    }).catch(() => {});
    transition.updateCallbackDone.catch(() => {});
    transition.finished.catch(() => {}).finally(() => root.classList.remove("theme-swap"));
    setTimeout(() => root.classList.remove("theme-swap"), 2000);  // never leave the shell without its persistent layers
  }
  const themeButton = $("theme-toggle");
  if (themeButton) themeButton.addEventListener("click", toggleTheme);

  // ---- sidebar: collapse on desktop, drawer on mobile ----------------------------------------------------
  const collapse = $("collapse-btn");
  if (collapse) collapse.addEventListener("click", () => {
    const collapsed = root.getAttribute("data-sidebar") === "collapsed";
    if (collapsed) root.removeAttribute("data-sidebar"); else root.setAttribute("data-sidebar", "collapsed");
    store.set("facegate-sidebar", collapsed ? "open" : "collapsed");
  });
  const shell = document.querySelector(".shell");
  const menu = $("menu-btn");
  if (menu && shell) {
    const toggleDrawer = (open) => shell.setAttribute("data-drawer", open ? "open" : "closed");
    menu.addEventListener("click", () => toggleDrawer(shell.getAttribute("data-drawer") !== "open"));
    shell.addEventListener("click", (event) => {
      if (shell.getAttribute("data-drawer") === "open" && !event.target.closest(".sidebar, .menu-btn")) toggleDrawer(false);
    });
    document.querySelectorAll(".sidebar .nav-link").forEach((a) => a.addEventListener("click", () => toggleDrawer(false)));
  }

  // ---- command palette (Ctrl/Cmd+K, "/" ) -------------------------------------------------------------------
  const palette = $("palette");
  if (palette && typeof palette.showModal === "function") {
    const input = $("palette-input");
    const list = $("palette-list");
    const empty = $("palette-empty");
    const items = () => [...list.querySelectorAll(".palette-item")];
    const visible = () => items().filter((li) => !li.hidden);
    let index = 0;
    const select = (i) => {
      const shown = visible();
      if (!shown.length) return;
      index = (i + shown.length) % shown.length;
      items().forEach((li) => li.classList.remove("is-selected"));
      shown[index].classList.add("is-selected");
      shown[index].scrollIntoView({ block: "nearest" });
    };
    const normalise = (text) => text.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    const filter = () => {
      const query = normalise(input.value.trim());
      items().forEach((li) => {
        const hay = normalise(`${li.textContent} ${li.dataset.keywords || ""}`);
        li.hidden = Boolean(query) && !query.split(/\s+/).every((word) => hay.includes(word));
      });
      list.querySelectorAll(".palette-group").forEach((group) => {
        let next = group.nextElementSibling, any = false;
        while (next && !next.classList.contains("palette-group")) { if (!next.hidden) any = true; next = next.nextElementSibling; }
        group.hidden = !any;
      });
      empty.hidden = visible().length > 0;
      select(0);
    };
    const open = () => { input.value = ""; filter(); palette.showModal(); input.focus(); };
    const run = (li) => {
      if (!li) return;
      const action = li.querySelector("[data-action]");
      const link = li.querySelector("a");
      palette.close();
      if (action && action.dataset.action === "theme") toggleTheme();
      else if (action && action.dataset.action === "logout") { const form = $("logout-form"); if (form) form.requestSubmit(); }
      else if (link) window.location.href = link.getAttribute("href");
    };
    const trigger = $("palette-open");
    if (trigger) trigger.addEventListener("click", open);
    input.addEventListener("input", filter);
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") { event.preventDefault(); select(index + 1); }
      else if (event.key === "ArrowUp") { event.preventDefault(); select(index - 1); }
      else if (event.key === "Enter") { event.preventDefault(); run(visible()[index]); }
    });
    list.addEventListener("click", (event) => { const li = event.target.closest(".palette-item"); if (li && event.target.closest("button")) run(li); });
    palette.addEventListener("click", (event) => { if (event.target === palette) palette.close(); });
    document.addEventListener("keydown", (event) => {
      const typing = /^(input|textarea|select)$/i.test(document.activeElement && document.activeElement.tagName);
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); palette.open ? palette.close() : open(); }
      else if (event.key === "/" && !typing && !palette.open) { event.preventDefault(); open(); }
    });
  }

  // ---- toasts: the server's flash message slides in, then leaves by itself (errors stay) ----------------------
  document.querySelectorAll(".flash").forEach((toast) => {
    const dismiss = () => { toast.classList.add("is-leaving"); toast.addEventListener("transitionend", () => toast.remove(), { once: true }); setTimeout(() => toast.remove(), 900); };
    toast.addEventListener("click", dismiss);
    if (toast.classList.contains("flash-ok") && !reduce) setTimeout(dismiss, 4800);
  });

  // ---- count-up numbers: <span data-count="37" data-suffix=" %">37</span> --------------------------------------
  window.facegate = window.facegate || {};
  window.facegate.countUp = (el, target, suffix = "") => {
    if (!Number.isFinite(target)) return;
    if (reduce || target === 0) { el.textContent = target + suffix; return; }
    const start = performance.now(), duration = 900, from = Number(el.dataset.current || 0);
    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration);
      el.textContent = Math.round(from + (target - from) * (1 - Math.pow(1 - t, 3))) + suffix;
      if (t < 1) requestAnimationFrame(tick); else el.dataset.current = String(target);
    };
    requestAnimationFrame(tick);
  };
  document.querySelectorAll("[data-count]").forEach((el) => window.facegate.countUp(el, Number(el.dataset.count), el.dataset.suffix || ""));

  // ---- card spotlight: the soft highlight follows the pointer -------------------------------------------------
  if (!reduce) document.addEventListener("pointermove", (event) => {
    const card = event.target.closest && event.target.closest(".card, .kpi");
    if (card) { const box = card.getBoundingClientRect(); card.style.setProperty("--mx", `${event.clientX - box.left}px`); }
  }, { passive: true });

  // ---- button ripple, and a "busy" shimmer on the submit button while a form is being sent ------------------------
  if (!reduce) document.addEventListener("pointerdown", (event) => {
    const button = event.target.closest && event.target.closest(".btn, .icon-btn");
    if (!button) return;
    const box = button.getBoundingClientRect();
    const wave = document.createElement("span");
    wave.className = "ripple";
    wave.style.left = `${event.clientX - box.left}px`;
    wave.style.top = `${event.clientY - box.top}px`;
    button.append(wave);
    setTimeout(() => wave.remove(), 650);
  }, { passive: true });
  document.addEventListener("submit", (event) => { if (!event.defaultPrevented && event.target.classList) event.target.classList.add("is-busy"); });
  window.addEventListener("pageshow", () => document.querySelectorAll("form.is-busy").forEach((f) => f.classList.remove("is-busy")));

  // ---- status pills in the top bar: service and door, for roles that may see them ----------------------------
  const pills = $("status-pills");
  if (pills) {
    const service = $("pill-service"), door = $("pill-door");
    const setPill = (el, tone, text) => { el.className = "pill " + tone; el.querySelector("span:last-child").textContent = text; };
    const dot = service.querySelector(".dot");
    const poll = async () => {
      if (document.hidden) return;
      try {
        const response = await fetch(pills.dataset.endpoint, { credentials: "same-origin", cache: "no-store" });
        if (!response.ok) return;
        const state = await response.json();
        pills.hidden = !state.enabled;
        if (!state.enabled) return;
        setPill(service, state.online ? "is-ok" : "is-bad", state.online ? `Service · ${state.fps ?? "–"} img/s` : "Service arrêté");
        dot.className = "dot " + (state.online ? "online" : "offline");
        if (!service.contains(dot)) service.prepend(dot);
        const open = Boolean(state.online && state.door && state.door.open);
        setPill(door, open ? "is-ok" : "", open ? "Porte ouverte" : "Porte fermée");
      } catch { /* the server is restarting */ }
    };
    poll();
    setInterval(poll, 5000);
  }
})();
