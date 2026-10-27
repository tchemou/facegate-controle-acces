"""Administration web app: sign-in, dashboard and a small JSON API.

Security choices (this serves biometric access logs, so they are not optional):
sessions are random tokens kept server-side (only their hash is stored), cookies are HttpOnly and
SameSite=Strict, every state-changing request needs a per-session CSRF token and a matching Origin,
login attempts are throttled, a wrong user and a wrong password look identical, and a strict
Content-Security-Policy forbids inline scripts. Bind it to localhost or put it behind HTTPS.
"""

import asyncio
import csv
import io
import json
import logging
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from types import SimpleNamespace

from ..channel import LiveReader
from ..store import DAYS, PERIODS, Store
from . import accounts, security

HERE = Path(__file__).parent
COOKIE = "facegate_session"
RANK = {"viewer": 1, "manager": 2, "owner": 3}
# while a password change is required, only these answer (the page to change it, and signing out)
MUST_CHANGE_ALLOWED = ("/account", "/account/password", "/logout")

REASON_LABELS = {
    "unknown_person": "Personne inconnue",
    "screen_detected": "Écran détecté",
    "policy:not_authorized": "Non autorisé dans cette zone",
    "policy:outside_schedule": "Hors des horaires autorisés",
    "policy:unknown_zone": "Zone inconnue",
}
DECISION_LABELS = {"granted": "Autorisé", "denied": "Refusé"}
DAY_LABELS = ("Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim")
TONES = ("ok", "bad", "warn", "muted", "accent")
MAX_STREAMS = 3  # simultaneous video streams: each one is a loop reading the disk
ENROLL_SAMPLES = 20
CONSENT_TEXT = (
    "Je consens à ce que FaceGate calcule et conserve une empreinte numérique de mon visage "
    "(une suite de 128 nombres, jamais de photo) afin de contrôler mon accès aux zones autorisées. "
    "Je peux en demander la suppression à tout moment."
)
EVENTS_PER_PAGE = 25
MAX_EXPORT_ROWS = 100_000
NAME_RE = re.compile(r"[\w][\w .'’-]{0,39}")  # letters (accents included), digits, space . ' -

# Messages shown after an action: the URL carries only a code, never text.
FLASH = {
    "person_updated": ("ok", "Statut mis à jour. Le changement est pris en compte immédiatement."),
    "person_deleted": ("ok", "Personne supprimée avec ses empreintes et ses droits."),
    "confirm_mismatch": ("error", "Suppression annulée : le nom saisi ne correspond pas."),
    "zone_created": ("ok", "Zone créée."),
    "zone_exists": ("error", "Cette zone existe déjà."),
    "zone_invalid": ("error", "Nom de zone invalide (40 caractères maximum : lettres, chiffres, espace, - ' .)."),
    "zone_deleted": ("ok", "Zone supprimée avec ses règles."),
    "rule_created": ("ok", "Règle ajoutée."),
    "rule_invalid": ("error", "Règle invalide : choisissez au moins un jour et des horaires HH:MM différents."),
    "rule_deleted": ("ok", "Règle supprimée."),
    "not_found": ("error", "Élément introuvable."),
    "enroll_invalid": ("error", "Nom invalide (40 caractères maximum : lettres, chiffres, espace, - ' .)."),
    "enroll_no_consent": ("error", "Le consentement de la personne est obligatoire pour enregistrer son visage."),
    "enroll_busy": ("error", "Une inscription est déjà en cours. Terminez-la ou annulez-la d'abord."),
    "enroll_name_exists": ("error", "Une personne porte déjà ce nom."),
    "enroll_cancelled": ("ok", "Inscription annulée."),
}
ACTION_LABELS = {
    "person_activated": "Personne activée", "person_deactivated": "Personne désactivée",
    "person_deleted": "Personne supprimée", "zone_created": "Zone créée", "zone_deleted": "Zone supprimée",
    "rule_created": "Règle ajoutée", "rule_deleted": "Règle supprimée", "events_exported": "Export du journal",
    "enrollment_requested": "Inscription demandée", "enrollment_cancelled": "Inscription annulée",
    "person_enrolled": "Personne inscrite",
}


FLASH.update(accounts.FLASH)
ACTION_LABELS.update(accounts.ACTION_LABELS)
FLASH.update({
    "retention_saved": ("ok", "Durées de conservation enregistrées. Elles s'appliquent à la prochaine purge automatique."),
    "retention_invalid": ("error", "Durée invalide : 0 (sans limite) ou entre 7 et 3650 jours."),
    "purge_done": ("ok", "Purge effectuée."),
})
ACTION_LABELS.update({
    "retention_changed": "Durées de conservation modifiées", "purge_run": "Purge du journal",
    "person_exported": "Export des données d'une personne",
})
log = logging.getLogger("facegate")
PURGE_EVERY_S = 6 * 3600


def _day_start(value: str | None, plus_days: int = 0) -> float | None:
    """`YYYY-MM-DD` from a date input to a local-midnight timestamp, None when absent or invalid."""
    try:
        return (datetime.strptime(value or "", "%Y-%m-%d") + timedelta(days=plus_days)).timestamp()
    except ValueError:
        return None


def _csv_safe(value) -> str:
    """Spreadsheet formula injection: a cell starting with = + - @ would run as a formula when opened."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def reason_label(reason: str) -> str:
    if reason.startswith("challenge_timeout"):
        return "Vérification de présence non réussie"
    return REASON_LABELS.get(reason, reason or "—")


async def mjpeg_chunks(reader: LiveReader, is_disconnected, interval: float = 0.1, max_seconds: float = 3600):
    """Multipart JPEG chunks of the latest frame, only when it changes; stops on disconnect or after a cap."""
    started, last = time.monotonic(), None
    while time.monotonic() - started < max_seconds:
        if await is_disconnected():
            return
        data = reader.frame()
        if data and data != last:
            last = data
            header = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(data)
            yield header + data + b"\r\n"
        await asyncio.sleep(interval)


def create_app(
    db_path: Path | str, *, secure_cookies: bool = False, session_ttl: float = 8 * 3600,
    live_dir: Path | str | None = None, stream_max_seconds: float = 3600, purge_interval_s: float = PURGE_EVERY_S,
) -> FastAPI:
    store = Store(db_path)
    reader = LiveReader(live_dir) if live_dir else None

    @asynccontextmanager
    async def lifespan(_app):
        """Retention: delete what is older than the configured periods, now and then every few hours."""

        async def purge_loop():
            while True:
                try:
                    events, audit = await asyncio.to_thread(store.apply_retention)
                    if events or audit:
                        log.info("retention: %d access events and %d administration entries deleted", events, audit)
                except Exception:  # a failing purge must be visible, and must not stop the web app
                    log.exception("retention purge failed")
                await asyncio.sleep(purge_interval_s)

        task = asyncio.create_task(purge_loop())
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(title="FaceGate", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)  # no public API docs
    app.state.store = store
    app.state.streams = 0
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["dt"] = lambda ts: datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M:%S")
    templates.env.filters["reason"] = reason_label
    templates.env.filters["decision"] = lambda d: DECISION_LABELS.get(d, d)
    templates.env.filters["hue"] = lambda name: "h%d" % (sum(map(ord, name or "?")) % 8 + 1)  # a stable colour per name

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return PlainTextResponse("Origine refusée", status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # not "no-referrer": Chrome then sends `Origin: null` on same-site form posts, which the Origin
        # check above (rightly) refuses. same-origin still leaks nothing to other sites.
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    # --- authentication and roles --------------------------------------------------------------------

    def current_session(request: Request):
        token = request.cookies.get(COOKIE)
        return store.get_session(token) if token else None

    def role_dependency(role: str, api: bool):
        """A route dependency: signed in, password up to date, and at least `role`. Checked before anything else
        in the handler runs (including the CSRF check), so a lower role learns nothing about the action."""

        def dependency(request: Request):
            session = current_session(request)
            if session is None:
                if api:
                    raise HTTPException(status_code=401, detail="Authentification requise")
                raise HTTPException(status_code=303, headers={"Location": "/login"})
            if session["must_change_password"] and request.url.path not in MUST_CHANGE_ALLOWED:
                if api:
                    raise HTTPException(status_code=403, detail="Changement de mot de passe requis")
                raise HTTPException(status_code=303, headers={"Location": "/account?m=must_change"})
            if RANK[session["role"]] < RANK[role]:
                raise HTTPException(status_code=403, detail="forbidden_page" if not api else "Droits insuffisants")
            return session

        return dependency

    require_page = role_dependency("viewer", api=False)  # any signed-in administrator
    require_manager = role_dependency("manager", api=False)
    require_owner = role_dependency("owner", api=False)
    require_api = role_dependency("viewer", api=True)
    require_api_manager = role_dependency("manager", api=True)

    def check_csrf(session, token: str) -> None:
        if not security.safe_equal(session["csrf"], token):
            raise HTTPException(status_code=403, detail="Jeton CSRF invalide")

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> str:
        return "ok"

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form(...), session=Depends(require_page)):
        check_csrf(session, csrf)
        store.delete_session(request.cookies[COOKIE])
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response

    # --- pages -----------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, session=Depends(require_page)):
        stats = store.stats(since=time.time() - 24 * 3600)
        context = page_context(
            request, session, "dashboard", stats=stats, events=[dict(e) for e in store.list_events(limit=15)],
            people_count=len(store.list_people()),
            alerts=store.overview("24h")["alerts"],
        )
        return templates.TemplateResponse(request, "dashboard.html", context)

    # --- management pages --------------------------------------------------------------------------

    def page_context(request: Request, session, active: str, **extra) -> dict:
        code = request.query_params.get("m", "")
        flash = FLASH.get(code)  # only fixed messages: nothing from the URL is ever echoed back
        return {
            "user": session["username"], "csrf": session["csrf"], "active": active, "flash": flash,
            "role": session["role"], "role_label": accounts.ROLE_LABELS[session["role"]],
            "rank": RANK[session["role"]], **extra,
        }

    def back(path: str, code: str) -> RedirectResponse:
        return RedirectResponse(f"{path}?m={code}", status_code=303)

    @app.get("/people", response_class=HTMLResponse)
    def people_page(request: Request, session=Depends(require_manager)):
        return templates.TemplateResponse(
            request, "people.html",
            page_context(request, session, "people", people=[dict(p) for p in store.list_people()]),
        )

    @app.post("/people/{person_id}/toggle")
    def person_toggle(person_id: int, csrf: str = Form(...), session=Depends(require_manager)):
        check_csrf(session, csrf)
        person = store.get_person(person_id)
        if person is None:
            return back("/people", "not_found")
        store.set_person_active(person_id, not person["active"])
        store.add_audit(session["username"], "person_deactivated" if person["active"] else "person_activated", person["name"])
        return back("/people", "person_updated")

    @app.post("/people/{person_id}/delete")
    def person_delete(person_id: int, csrf: str = Form(...), confirm: str = Form(""), session=Depends(require_manager)):
        check_csrf(session, csrf)
        person = store.get_person(person_id)
        if person is None:
            return back("/people", "not_found")
        if confirm.strip().lower() != person["name"].lower():
            return back("/people", "confirm_mismatch")  # erasure is irreversible: type the name to confirm
        store.delete_person(person_id)
        store.add_audit(session["username"], "person_deleted", f"identifiant {person_id}")  # never the name: it was just erased
        return back("/people", "person_deleted")

    @app.get("/zones", response_class=HTMLResponse)
    def zones_page(request: Request, session=Depends(require_manager)):
        context = page_context(
            request, session, "zones",
            zones=[dict(z) for z in store.list_zones()],
            people=[dict(p) for p in store.list_people()],
            rules=store.list_rules(),
            days=list(zip(DAYS, DAY_LABELS)),
            day_labels=dict(zip(DAYS, DAY_LABELS)),
        )
        return templates.TemplateResponse(request, "zones.html", context)

    @app.post("/zones")
    def zone_create(name: str = Form(""), csrf: str = Form(...), session=Depends(require_manager)):
        check_csrf(session, csrf)
        name = name.strip()
        if not NAME_RE.fullmatch(name):
            return back("/zones", "zone_invalid")
        try:
            store.add_zone(name)
        except sqlite3.IntegrityError:
            return back("/zones", "zone_exists")
        store.add_audit(session["username"], "zone_created", name)
        return back("/zones", "zone_created")

    @app.post("/zones/{zone_id}/delete")
    def zone_delete(zone_id: int, csrf: str = Form(...), session=Depends(require_manager)):
        check_csrf(session, csrf)
        zone = next((z for z in store.list_zones() if z["id"] == zone_id), None)
        if zone is None:
            return back("/zones", "not_found")
        store.delete_zone(zone_id)
        store.add_audit(session["username"], "zone_deleted", zone["name"])
        return back("/zones", "zone_deleted")

    @app.post("/rules")
    def rule_create(
        zone_id: int = Form(...), person_id: int = Form(...), mode: str = Form(...),
        days: list[str] = Form(default=[]), start: str = Form(""), end: str = Form(""),
        csrf: str = Form(...), session=Depends(require_manager),
    ):
        check_csrf(session, csrf)
        zone = next((z for z in store.list_zones() if z["id"] == zone_id), None)
        person = store.get_person(person_id)
        if zone is None or person is None:
            return back("/zones", "not_found")
        try:
            store.add_rule(zone_id, person_id, always=(mode == "always"), days=days, start=start, end=end)
        except ValueError:
            return back("/zones", "rule_invalid")
        when = "toujours" if mode == "always" else f"{','.join(days)} {start}-{end}"
        store.add_audit(session["username"], "rule_created", f"{person['name']} → {zone['name']} ({when})")
        return back("/zones", "rule_created")

    @app.post("/rules/{rule_id}/delete")
    def rule_delete(rule_id: int, csrf: str = Form(...), session=Depends(require_manager)):
        check_csrf(session, csrf)
        rule = next((r for r in store.list_rules() if r["id"] == rule_id), None)
        if rule is None:
            return back("/zones", "not_found")
        store.delete_rule(rule_id)
        store.add_audit(session["username"], "rule_deleted", f"{rule['person']} → {rule['zone']}")
        return back("/zones", "rule_deleted")

    def parse_filters(request: Request) -> dict:
        q = request.query_params
        decision = q.get("decision") if q.get("decision") in DECISION_LABELS else None
        return {
            "decision": decision,
            "person": (q.get("person") or "").strip() or None,
            "zone": (q.get("zone") or "").strip() or None,
            "since": _day_start(q.get("from")),
            "until": _day_start(q.get("to"), plus_days=1),
        }

    @app.get("/events", response_class=HTMLResponse)
    def events_page(request: Request, page: int = 1, session=Depends(require_page)):
        filters = parse_filters(request)
        total = store.count_events(**filters)
        pages = max(1, -(-total // EVENTS_PER_PAGE))
        page = min(max(1, page), pages)
        rows = store.list_events(EVENTS_PER_PAGE, (page - 1) * EVENTS_PER_PAGE, **filters)
        keep = {k: v for k, v in request.query_params.items() if k != "page" and v}

        def link(n: int) -> str:
            return "/events?" + urlencode({**keep, "page": n})

        context = page_context(
            request, session, "events",
            events=[dict(r) for r in rows], total=total, page=page, pages=pages,
            prev_url=link(page - 1) if page > 1 else None, next_url=link(page + 1) if page < pages else None,
            export_url="/events.csv" + (("?" + urlencode(keep)) if keep else ""),
            zones=store.event_zones(), q=request.query_params,
        )
        return templates.TemplateResponse(request, "events.html", context)

    @app.get("/events.csv")
    def events_csv(request: Request, session=Depends(require_manager)):
        rows = store.list_events(MAX_EXPORT_ROWS, 0, **parse_filters(request))
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["date", "personne", "zone", "decision", "raison"])
        for r in rows:
            writer.writerow([_csv_safe(v) for v in (
                datetime.fromtimestamp(r["at"]).isoformat(sep=" ", timespec="seconds"),
                r["person"] or "", r["zone"], r["decision"], r["reason"],
            )])
        store.add_audit(session["username"], "events_exported", f"{len(rows)} lignes")
        return Response(
            out.getvalue(), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="acces.csv"'},
        )

    @app.get("/audit", response_class=HTMLResponse)
    def audit_page(request: Request, session=Depends(require_owner)):
        return templates.TemplateResponse(
            request, "audit.html",
            page_context(request, session, "audit", entries=[dict(e) for e in store.list_audit(200)],
                         action_labels=ACTION_LABELS),
        )

    # --- live view and enrollment ----------------------------------------------------------------------

    @app.get("/live", response_class=HTMLResponse)
    def live_page(request: Request, session=Depends(require_manager)):
        return templates.TemplateResponse(request, "live.html", page_context(
            request, session, "live", live_enabled=reader is not None,
        ))

    @app.get("/live/stream")
    async def live_stream(request: Request, session=Depends(require_api_manager)):
        """The annotated camera image as MJPEG, for signed-in administrators only."""
        if reader is None:
            raise HTTPException(status_code=404, detail="Vidéo en direct non activée")
        if app.state.streams >= MAX_STREAMS:
            raise HTTPException(status_code=429, detail="Trop de flux ouverts")

        async def body():
            app.state.streams += 1
            try:
                async for chunk in mjpeg_chunks(reader, request.is_disconnected, max_seconds=stream_max_seconds):
                    yield chunk
            finally:
                app.state.streams -= 1

        return StreamingResponse(body(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/live")
    def api_live(session=Depends(require_api_manager)):
        """Service state for the live page: only known, simple fields are relayed."""
        if reader is None:
            return {"enabled": False, "online": False}
        raw = reader.state()
        door, enrollment = raw.get("door"), raw.get("enrollment")
        return {
            "enabled": True,
            "online": bool(raw.get("online")),
            "fps": raw.get("fps"),
            "zone": raw.get("zone", ""),
            "mode": raw.get("mode", ""),
            "faces": [
                {"label": str(f.get("label", "")), "tone": f.get("tone") if f.get("tone") in TONES else "muted",
                 "state": str(f.get("state", ""))}
                for f in raw.get("faces", [])[:5]
            ],
            "door": None if not isinstance(door, dict) else
                    {"open": bool(door.get("open")), "seconds_left": float(door.get("seconds_left") or 0)},
            "enrollment": None if not isinstance(enrollment, dict) else {
                "name": str(enrollment.get("name", "")), "progress": int(enrollment.get("progress") or 0),
                "total": int(enrollment.get("total") or 0), "hint": str(enrollment.get("hint", "")),
            },
        }

    @app.get("/people/new", response_class=HTMLResponse)
    def enroll_form(request: Request, session=Depends(require_manager)):
        return templates.TemplateResponse(request, "enroll_new.html", page_context(
            request, session, "people", consent_text=CONSENT_TEXT, samples=ENROLL_SAMPLES,
            live_enabled=reader is not None,
        ))

    @app.post("/people/enroll")
    def enroll_start(
        name: str = Form(""), consent: str = Form(""), csrf: str = Form(...), session=Depends(require_manager),
    ):
        check_csrf(session, csrf)
        name = name.strip()
        if not NAME_RE.fullmatch(name):
            return back("/people/new", "enroll_invalid")
        if consent != "yes":  # biometric data: no enrollment without the person's recorded consent
            return back("/people/new", "enroll_no_consent")
        try:
            enrollment_id = store.request_enrollment(
                name, session["username"], f"{CONSENT_TEXT} (consentement recueilli par {session['username']})",
                ENROLL_SAMPLES,
            )
        except ValueError as err:
            return back("/people/new", "enroll_busy" if str(err) == "busy" else "enroll_name_exists")
        store.add_audit(session["username"], "enrollment_requested", name)
        return RedirectResponse(f"/enroll/{enrollment_id}", status_code=303)

    @app.get("/enroll/{enrollment_id}", response_class=HTMLResponse)
    def enroll_page(enrollment_id: int, request: Request, session=Depends(require_manager)):
        enrollment = store.get_enrollment(enrollment_id)
        if enrollment is None:
            return back("/people", "not_found")
        return templates.TemplateResponse(request, "enroll_run.html", page_context(
            request, session, "people", enrollment=dict(enrollment), live_enabled=reader is not None,
        ))

    @app.get("/api/enroll/{enrollment_id}")
    def api_enroll(enrollment_id: int, session=Depends(require_api_manager)):
        store.expire_enrollments()  # nobody picked it up: stop waiting
        enrollment = store.get_enrollment(enrollment_id)
        if enrollment is None:
            raise HTTPException(status_code=404, detail="Introuvable")
        return {k: enrollment[k] for k in ("id", "name", "status", "progress", "total", "message")}

    @app.post("/enroll/{enrollment_id}/cancel")
    def enroll_cancel(enrollment_id: int, csrf: str = Form(...), session=Depends(require_manager)):
        check_csrf(session, csrf)
        if store.cancel_enrollment(enrollment_id):
            store.add_audit(session["username"], "enrollment_cancelled", str(enrollment_id))
        return back("/people", "enroll_cancelled")

    # --- compliance (owners) ---------------------------------------------------------------------------------

    @app.get("/compliance", response_class=HTMLResponse)
    def compliance_page(request: Request, session=Depends(require_owner)):
        events_days, audit_days = store.retention_days()
        return templates.TemplateResponse(request, "compliance.html", page_context(
            request, session, "compliance", events_days=events_days, audit_days=audit_days,
            overview=store.retention_overview(), encryption=store.encryption_status(),
            people=[dict(p) for p in store.list_people()],
        ))

    @app.post("/compliance/retention")
    def compliance_retention(
        events_days: int = Form(...), audit_days: int = Form(...), csrf: str = Form(...), session=Depends(require_owner),
    ):
        check_csrf(session, csrf)
        try:
            store.set_retention_days(events_days, audit_days)
        except ValueError:
            return back("/compliance", "retention_invalid")
        store.add_audit(session["username"], "retention_changed", f"accès {events_days} j, administration {audit_days} j")
        return back("/compliance", "retention_saved")

    @app.post("/compliance/purge")
    def compliance_purge(csrf: str = Form(...), session=Depends(require_owner)):
        check_csrf(session, csrf)
        events, audit = store.apply_retention()
        store.add_audit(session["username"], "purge_run", f"{events} accès, {audit} entrées d'administration")
        return back("/compliance", "purge_done")

    @app.get("/people/{person_id}/export.json")
    def person_export(person_id: int, session=Depends(require_owner)):
        """Right of access: everything held about one person, as a JSON download."""
        data = store.export_person(person_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Personne introuvable")
        store.add_audit(session["username"], "person_exported", data["person"]["name"])
        return Response(
            json.dumps(data, ensure_ascii=False, indent=2), media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="export-{person_id}.json"'},  # id only: no name in a header
        )

    # --- dashboard data --------------------------------------------------------------------------------------

    def posture(session) -> dict:
        """How well protected this installation is, as a checklist and a score. Each item says what to do."""
        items = []

        def add(state: str, title: str, hint: str) -> None:
            items.append({"state": state, "title": title, "hint": hint})

        enc = store.encryption_status()
        total = enc["encrypted"] + enc["plain"]
        if enc["plain"] == 0:
            add("ok", "Empreintes chiffrées", f"{enc['encrypted']} sur {total} au repos" if total else "Aucune empreinte stockée")
        else:
            add("bad", "Empreintes en clair", f"{enc['plain']} sur {total} : lancez « facerec db encrypt »")
        owners = store.active_owner_count()
        add("ok" if owners >= 2 else "warn", "Propriétaires", f"{owners} actif{'s' if owners > 1 else ''}" if owners >= 2
            else "Un seul : créez-en un second pour pouvoir récupérer un compte")
        mine = bool(session["totp_enabled"])
        add("ok" if mine else "warn", "Votre double authentification", "Activée" if mine else "Désactivée : à activer dans « Mon compte »")
        no_2fa = [a["username"] for a in store.list_admins() if a["active"] and a["role"] != "viewer" and not a["totp_enabled"]]
        add("ok" if not no_2fa else "warn", "2FA des gestionnaires et propriétaires",
            "Tous protégés" if not no_2fa else f"{len(no_2fa)} sans double authentification")
        events_days, _ = store.retention_days()
        add("ok" if events_days else "warn", "Conservation du journal",
            f"{events_days} jours, purge automatique" if events_days else "Sans limite : déconseillé par le RGPD")
        add("ok" if secure_cookies else "warn", "Connexion", "HTTPS (cookies sécurisés)" if secure_cookies
            else "HTTP local : placez un proxy HTTPS avant d'exposer l'interface")
        add("warn" if reader is not None else "ok", "Vidéo en direct",
            "Activée : des images de visage sont visibles des administrateurs" if reader is not None
            else "Désactivée : aucune image n'est partagée")
        weight = {"ok": 1.0, "warn": 0.5, "bad": 0.0}
        score = round(100 * sum(weight[i["state"]] for i in items) / len(items))
        return {"score": score, "items": items}

    @app.get("/api/overview")
    def api_overview(period: str = "24h", session=Depends(require_api)):
        """The dashboard in one request: series, comparison, reasons, heatmap, latest events, posture."""
        data = store.overview(period if period in PERIODS else "24h")
        data["reason_labels"] = {r: reason_label(r) for r in data["reasons"]}
        data["heatmap"] = store.heatmap(28)
        data["people"] = sum(1 for p in store.list_people() if p["active"])
        data["events"] = [
            {"id": e["id"], "at": e["at"], "decision": e["decision"], "person": e["person"], "zone": e["zone"],
             "reason": e["reason"], "reason_label": reason_label(e["reason"]) if e["reason"] else ""}
            for e in store.list_events(limit=12)
        ]
        if RANK[session["role"]] >= RANK["manager"]:  # security posture and service state are not for viewers
            data["posture"] = posture(session)
            state = reader.state() if reader is not None else {}
            data["service"] = {"enabled": reader is not None, "online": bool(state.get("online")), "fps": state.get("fps"),
                               "door": state.get("door") if isinstance(state.get("door"), dict) else None}
        return data

    # --- JSON API (read-only for now) ----------------------------------------------------------

    @app.get("/api/stats")
    def api_stats(hours: int = 24, session=Depends(require_api)):
        hours = max(1, min(hours, 24 * 90))
        return store.stats(since=time.time() - hours * 3600)

    @app.get("/api/events")
    def api_events(
        limit: int = 50, offset: int = 0, decision: str | None = None, person: str | None = None,
        session=Depends(require_api),
    ):
        limit, offset = max(1, min(limit, 500)), max(0, offset)
        rows = store.list_events(limit, offset, decision, person)
        return JSONResponse([dict(r) for r in rows])

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 303:
            return RedirectResponse(exc.headers["Location"], status_code=303)
        if exc.status_code == 403 and exc.detail == "forbidden_page":
            session = current_session(request)
            return templates.TemplateResponse(request, "forbidden.html", page_context(request, session, ""), status_code=403)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    accounts.register(app, SimpleNamespace(
        store=store, templates=templates, current_session=current_session, check_csrf=check_csrf,
        page_context=page_context, back=back, require_page=require_page, require_owner=require_owner,
        session_ttl=session_ttl, secure_cookies=secure_cookies, cookie_name=COOKIE,
    ))
    return app
