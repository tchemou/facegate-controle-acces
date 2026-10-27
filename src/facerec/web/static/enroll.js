// Enrollment page: follows the request queued in the database while the camera service performs it.
(() => {
  const root = document.getElementById("enroll-root");
  const id = root.dataset.id;
  const total = Number(root.dataset.total) || 1;
  const CIRCUMFERENCE = 326.73; // 2 * pi * 52
  const $ = (name) => document.getElementById(name);
  const ring = $("ring");
  let timer = null;

  const MESSAGES = {
    pending: "En attente du service caméra…",
    running: "Inscription en cours : bougez légèrement la tête",
    done: "Inscription terminée",
    failed: "L'inscription a échoué",
    cancelled: "Inscription annulée",
  };

  function render(state) {
    const progress = Math.min(state.progress, total);
    $("count").textContent = String(progress);
    ring.style.strokeDashoffset = String(CIRCUMFERENCE * (1 - progress / total));
    $("status-line").textContent = MESSAGES[state.status] || state.status;
    $("status-detail").textContent = state.status === "failed" || state.status === "done" ? state.message : "";
    $("progress-card").dataset.status = state.status;

    if (state.status === "done") {
      ring.style.strokeDashoffset = "0";
      $("ring-check").removeAttribute("hidden"); // an SVG element has no `hidden` property: remove the attribute
      $("ring-number").hidden = true;
      $("cancel-form").hidden = true;
      $("done-link").hidden = false;
    } else if (state.status === "failed") {
      $("cancel-form").hidden = true;
      $("retry-link").hidden = false;
    } else if (state.status === "cancelled") {
      window.location.href = "/people";
    }
    if (["done", "failed", "cancelled"].includes(state.status)) clearInterval(timer);
  }

  async function poll() {
    try {
      const response = await fetch(`/api/enroll/${encodeURIComponent(id)}`, { credentials: "same-origin", cache: "no-store" });
      if (response.status === 401) { window.location.href = "/login"; return; }
      if (response.ok) render(await response.json());
    } catch { /* retry at the next tick */ }
  }

  poll();
  timer = setInterval(poll, 600);
})();
