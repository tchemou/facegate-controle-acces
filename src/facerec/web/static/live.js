// Live page: polls /api/live and updates the status, the door, the faces and the enrollment card.
// Text only goes through textContent: nothing from the service is ever parsed as HTML.
(() => {
  const $ = (id) => document.getElementById(id);
  const video = $("video");
  const DOOR_CIRCUMFERENCE = 125.66;
  let doorTotal = 5;
  let lastReload = 0;
  let lastFaces = null;

  const set = (id, text) => { const el = $(id); if (el && el.textContent !== text) el.textContent = text; };

  function render(state) {
    const online = Boolean(state.online);
    $("status-dot").className = "dot " + (online ? "online" : "offline");
    set("status-text", online ? "Service en marche" : "Service arrêté");
    set("fact-zone", online && state.zone ? state.zone : "—");
    set("fact-mode", online && state.mode ? state.mode : "—");
    set("fact-fps", online && state.fps ? `${state.fps} img/s` : "—");

    $("video-offline").hidden = online;
    video.hidden = !online;
    if (online && !video.complete && Date.now() - lastReload > 3000) reloadVideo();

    const door = online ? state.door : null;
    const card = $("door-card");
    card.classList.toggle("is-open", Boolean(door && door.open));
    if (door && door.open) {
      doorTotal = Math.max(doorTotal, door.seconds_left);
      $("door-ring").style.strokeDashoffset = String(DOOR_CIRCUMFERENCE * (1 - door.seconds_left / doorTotal));
      set("door-text", "Ouverte");
      set("door-sub", `Se referme dans ${Math.ceil(door.seconds_left)} s`);
    } else {
      $("door-ring").style.strokeDashoffset = String(DOOR_CIRCUMFERENCE);
      set("door-text", door ? "Fermée" : "Non connectée");
      set("door-sub", door ? "" : "Aucune porte pilotée par ce service");
    }

    const faces = online ? state.faces : [];
    const signature = JSON.stringify([online, faces]);
    if (signature !== lastFaces) { // rebuild only on change, otherwise the entry animation replays every second
      lastFaces = signature;
      const list = $("faces");
      list.replaceChildren();
      if (!faces.length) {
        const li = document.createElement("li");
        li.className = "muted";
        li.textContent = online ? "Aucun visage" : "—";
        list.appendChild(li);
      }
      faces.forEach((face) => {
        const li = document.createElement("li");
        const dot = document.createElement("span");
        dot.className = "tone tone-" + face.tone;
        const label = document.createElement("span");
        label.textContent = face.label;
        li.append(dot, label);
        list.appendChild(li);
      });
    }

    const enroll = online ? state.enrollment : null;
    $("enroll-card").hidden = !enroll;
    if (enroll) {
      set("enroll-name", enroll.name);
      set("enroll-hint", enroll.hint);
      $("enroll-bar").max = Math.max(enroll.total, 1);
      $("enroll-bar").value = enroll.progress;
    }
  }

  function reloadVideo() {
    lastReload = Date.now();
    video.src = "/live/stream?t=" + lastReload; // the browser does not reconnect an ended MJPEG stream by itself
  }

  async function poll() {
    if (document.hidden) return;
    try {
      const response = await fetch("/api/live", { credentials: "same-origin", cache: "no-store" });
      if (response.status === 401) { window.location.href = "/login"; return; }
      if (response.ok) render(await response.json());
    } catch { /* the server is restarting: try again at the next tick */ }
  }

  video.addEventListener("error", () => { if (Date.now() - lastReload > 3000) reloadVideo(); });
  poll();
  setInterval(poll, 1000);
})();
