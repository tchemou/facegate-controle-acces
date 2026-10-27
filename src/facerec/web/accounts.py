"""Sign-in (with an optional second factor), "my account" and administrator management.

Rules enforced here, each with a test:
- a wrong user, a wrong password and a disabled account answer identically (no account enumeration);
- the second step has its own short-lived token, its own throttle, and a TOTP code cannot be replayed;
- recovery codes work once; regenerating them invalidates the old ones;
- changing the password, enabling or disabling the second factor needs the current password;
- a password change signs the account out everywhere else;
- an owner cannot act on their own account, and the last active owner can never be removed.
"""

import re
import sqlite3
from types import SimpleNamespace

from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..store import ROLES
from . import security, totp

PENDING_COOKIE = "facegate_2fa"
PENDING_TTL_S = 300
USERNAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,31}")
ROLE_LABELS = {"viewer": "Lecteur", "manager": "Gestionnaire", "owner": "Propriétaire"}
ROLE_HELP = {
    "viewer": "Voit le tableau de bord et le journal des accès.",
    "manager": "Gère les personnes, les zones et les horaires, inscrit des visages, voit la vidéo et exporte le journal.",
    "owner": "Tout, y compris les administrateurs, le journal d'administration et la conformité.",
}

FLASH = {
    "must_change": ("error", "Vous devez choisir un nouveau mot de passe avant de continuer."),
    "password_changed": ("ok", "Mot de passe modifié. Les autres sessions ont été fermées."),
    "bad_current": ("error", "Le mot de passe actuel est incorrect."),
    "password_mismatch": ("error", "Les deux nouveaux mots de passe ne correspondent pas."),
    "weak_password": ("error", "Mot de passe trop faible : 12 caractères minimum, majuscules et minuscules, au moins un chiffre."),
    "same_password": ("error", "Le nouveau mot de passe doit être différent de l'actuel."),
    "2fa_bad_code": ("error", "Code incorrect. Vérifiez l'heure de votre téléphone et réessayez."),
    "2fa_disabled": ("ok", "Double authentification désactivée."),
    "admin_created": ("ok", "Administrateur créé. Il devra choisir son mot de passe à la première connexion."),
    "admin_exists": ("error", "Cet identifiant existe déjà."),
    "admin_invalid": ("error", "Identifiant invalide (3 à 32 caractères : lettres, chiffres, . _ -) ou rôle inconnu."),
    "admin_updated": ("ok", "Administrateur mis à jour."),
    "admin_self": ("error", "Vous ne pouvez pas modifier votre propre compte ici : demandez à un autre propriétaire."),
    "admin_last_owner": ("error", "Il doit rester au moins un propriétaire actif."),
    "admin_password_reset": ("ok", "Mot de passe réinitialisé : l'administrateur devra en choisir un nouveau."),
    "admin_2fa_reset": ("ok", "Double authentification réinitialisée."),
}
ACTION_LABELS = {
    "login": "Connexion", "password_changed": "Mot de passe modifié", "2fa_enabled": "Double authentification activée",
    "2fa_disabled": "Double authentification désactivée", "recovery_regenerated": "Codes de secours régénérés",
    "recovery_code_used": "Code de secours utilisé", "admin_created": "Administrateur créé",
    "admin_role_changed": "Rôle modifié", "admin_deactivated": "Administrateur désactivé",
    "admin_activated": "Administrateur réactivé", "admin_password_reset": "Mot de passe réinitialisé",
    "admin_2fa_reset": "Double authentification réinitialisée",
}


def register(app, ctx: SimpleNamespace) -> None:
    store, templates = ctx.store, ctx.templates
    cookie_args = {"httponly": True, "samesite": "strict", "secure": ctx.secure_cookies, "path": "/"}

    def login_page(request: Request, error: str | None, status: int = 200):
        return templates.TemplateResponse(request, "login.html", {"error": error, "no_admin": False}, status_code=status)

    def verify_current(admin_id: int, password: str) -> bool:
        row = store.get_admin_by_id(admin_id)
        return bool(row) and security.verify_password(password, row["password_hash"])

    def open_session(admin, response, username: str | None = None) -> None:
        token, csrf = security.new_token(), security.new_token()
        store.create_session(admin["id"], token, csrf, ctx.session_ttl)
        response.set_cookie(ctx.cookie_name, token, max_age=int(ctx.session_ttl), **cookie_args)
        store.add_audit(admin["username"], "login", "")

    # --- sign-in -----------------------------------------------------------------------------------------

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        if ctx.current_session(request) is not None:
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(request, "login.html", {"error": None, "no_admin": store.admin_count() == 0})

    @app.post("/login")
    def login(request: Request, username: str = Form(...), password: str = Form(...)):
        client = request.client.host if request.client else "?"
        key = f"{client}|{username.strip().lower()}"
        if store.recent_login_failures(key, security.FAILURE_WINDOW_S) >= security.MAX_FAILURES:
            return login_page(request, "Trop de tentatives. Réessayez dans 15 minutes.", 429)
        admin = store.get_admin(username.strip())
        # a disabled account takes the same path as an unknown one: same message, same work
        valid = (
            security.verify_password(password, admin["password_hash"]) if admin and admin["active"]
            else security.verify_unknown_user(password)
        )
        if not valid:
            store.record_login_failure(key)
            return login_page(request, "Identifiants incorrects.", 401)
        store.clear_login_failures(key)
        if admin["totp_enabled"]:
            token = security.new_token()
            store.create_pending_2fa(admin["id"], token, PENDING_TTL_S)
            response = RedirectResponse("/login/2fa", status_code=303)
            response.set_cookie(PENDING_COOKIE, token, max_age=PENDING_TTL_S, **cookie_args)
            return response
        response = RedirectResponse("/", status_code=303)
        open_session(admin, response)
        return response

    def pending(request: Request):
        token = request.cookies.get(PENDING_COOKIE)
        return (token, store.get_pending_2fa(token)) if token else (None, None)

    @app.get("/login/2fa", response_class=HTMLResponse)
    def second_step_form(request: Request):
        _, row = pending(request)
        if row is None:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "login_2fa.html", {"error": None})

    @app.post("/login/2fa")
    def second_step(request: Request, code: str = Form("")):
        token, row = pending(request)
        if row is None:
            return RedirectResponse("/login", status_code=303)
        client = request.client.host if request.client else "?"
        key = f"2fa|{row['admin_id']}|{client}"
        if store.recent_login_failures(key, security.FAILURE_WINDOW_S) >= security.MAX_FAILURES:
            store.delete_pending_2fa(token)
            response = login_page(request, "Trop de tentatives. Réessayez dans 15 minutes.", 429)
            response.delete_cookie(PENDING_COOKIE, path="/")
            return response
        accepted, used_recovery = False, False
        step = totp.verify(row["totp_secret"], code, last_step=row["totp_last_step"])
        if step is not None:
            accepted = store.update_totp_step(row["admin_id"], step)  # False when a parallel request just used it
        elif len(totp.normalize_recovery(code)) == 10:
            normalized = totp.normalize_recovery(code)
            for candidate in store.unused_recovery_codes(row["admin_id"]):
                if security.verify_password(normalized, candidate["code_hash"]):
                    accepted = used_recovery = store.use_recovery_code(candidate["id"])
                    break
        if not accepted:
            store.record_login_failure(key)
            return templates.TemplateResponse(request, "login_2fa.html", {"error": "Code incorrect."}, status_code=401)
        store.clear_login_failures(key)
        store.delete_pending_2fa(token)
        admin = store.get_admin_by_id(row["admin_id"])
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(PENDING_COOKIE, path="/")
        open_session(admin, response)
        if used_recovery:
            store.add_audit(admin["username"], "recovery_code_used", f"{len(store.unused_recovery_codes(admin['id']))} restant(s)")
        return response

    # --- my account -----------------------------------------------------------------------------------------

    @app.get("/account", response_class=HTMLResponse)
    def account_page(request: Request, session=Depends(ctx.require_page)):
        admin = store.get_admin_by_id(session["admin_id"])
        return templates.TemplateResponse(request, "account.html", ctx.page_context(
            request, session, "account", role_label=ROLE_LABELS[admin["role"]], totp_enabled=bool(admin["totp_enabled"]),
            recovery_left=len(store.unused_recovery_codes(admin["id"])), must_change=bool(admin["must_change_password"]),
        ))

    @app.post("/account/password")
    def change_password(
        request: Request, current_password: str = Form(""), new_password: str = Form(""),
        confirm_password: str = Form(""), csrf: str = Form(...), session=Depends(ctx.require_page),
    ):
        ctx.check_csrf(session, csrf)
        if not verify_current(session["admin_id"], current_password):
            return ctx.back("/account", "bad_current")
        if new_password != confirm_password:
            return ctx.back("/account", "password_mismatch")
        if new_password == current_password:
            return ctx.back("/account", "same_password")
        if security.check_password_strength(new_password):
            return ctx.back("/account", "weak_password")
        store.set_admin_password(session["admin_id"], security.hash_password(new_password), must_change=False,
                                 keep_token=request.cookies.get(ctx.cookie_name))
        store.add_audit(session["username"], "password_changed", "")
        return ctx.back("/account", "password_changed")

    # --- second factor ---------------------------------------------------------------------------------------

    @app.post("/account/2fa/start")
    def twofa_start(password: str = Form(""), csrf: str = Form(...), session=Depends(ctx.require_page)):
        ctx.check_csrf(session, csrf)
        if not verify_current(session["admin_id"], password):  # a hijacked session must not be able to add a factor
            return ctx.back("/account", "bad_current")
        store.set_totp_secret(session["admin_id"], totp.new_secret())
        return RedirectResponse("/account/2fa", status_code=303)

    @app.get("/account/2fa", response_class=HTMLResponse)
    def twofa_setup(request: Request, session=Depends(ctx.require_page)):
        admin = store.get_admin_by_id(session["admin_id"])
        if not admin["totp_secret"] or admin["totp_enabled"]:
            return RedirectResponse("/account", status_code=303)
        uri = totp.otpauth_uri(admin["totp_secret"], admin["username"])
        return templates.TemplateResponse(request, "account_2fa.html", ctx.page_context(
            request, session, "account", qr=totp.qr_svg(uri), secret=totp.format_secret(admin["totp_secret"]),
        ))

    def recovery_page(request: Request, session, codes: list[str], title: str):
        return templates.TemplateResponse(request, "recovery_codes.html", ctx.page_context(
            request, session, "account", codes=codes, title=title,
        ))

    @app.post("/account/2fa/confirm")
    def twofa_confirm(request: Request, code: str = Form(""), csrf: str = Form(...), session=Depends(ctx.require_page)):
        ctx.check_csrf(session, csrf)
        admin = store.get_admin_by_id(session["admin_id"])
        if not admin["totp_secret"] or admin["totp_enabled"]:
            return RedirectResponse("/account", status_code=303)
        step = totp.verify(admin["totp_secret"], code)
        if step is None:
            return ctx.back("/account/2fa", "2fa_bad_code")
        codes = totp.new_recovery_codes()
        store.enable_totp(admin["id"], step, [security.hash_password(totp.normalize_recovery(c)) for c in codes])
        store.add_audit(session["username"], "2fa_enabled", "")
        return recovery_page(request, session, codes, "Double authentification activée")

    @app.post("/account/2fa/disable")
    def twofa_disable(password: str = Form(""), csrf: str = Form(...), session=Depends(ctx.require_page)):
        ctx.check_csrf(session, csrf)
        if not verify_current(session["admin_id"], password):
            return ctx.back("/account", "bad_current")
        store.disable_totp(session["admin_id"])
        store.add_audit(session["username"], "2fa_disabled", "")
        return ctx.back("/account", "2fa_disabled")

    @app.post("/account/2fa/recovery")
    def twofa_recovery(request: Request, password: str = Form(""), csrf: str = Form(...), session=Depends(ctx.require_page)):
        ctx.check_csrf(session, csrf)
        admin = store.get_admin_by_id(session["admin_id"])
        if not admin["totp_enabled"]:
            return RedirectResponse("/account", status_code=303)
        if not verify_current(session["admin_id"], password):
            return ctx.back("/account", "bad_current")
        codes = totp.new_recovery_codes()
        store.replace_recovery_codes(admin["id"], [security.hash_password(totp.normalize_recovery(c)) for c in codes])
        store.add_audit(session["username"], "recovery_regenerated", "")
        return recovery_page(request, session, codes, "Nouveaux codes de secours")

    # --- administrators (owners) --------------------------------------------------------------------------------

    @app.get("/admins", response_class=HTMLResponse)
    def admins_page(request: Request, session=Depends(ctx.require_owner)):
        admins = []
        for row in store.list_admins():
            item = dict(row)
            item["role_label"] = ROLE_LABELS[row["role"]]
            item["is_me"] = row["id"] == session["admin_id"]
            admins.append(item)
        return templates.TemplateResponse(request, "admins.html", ctx.page_context(
            request, session, "admins", admins=admins, roles=[(r, ROLE_LABELS[r], ROLE_HELP[r]) for r in ROLES],
        ))

    @app.post("/admins")
    def admin_create(
        username: str = Form(""), role: str = Form(""), password: str = Form(""), csrf: str = Form(...),
        session=Depends(ctx.require_owner),
    ):
        ctx.check_csrf(session, csrf)
        username = username.strip()
        if not USERNAME_RE.fullmatch(username) or role not in ROLES:
            return ctx.back("/admins", "admin_invalid")
        if security.check_password_strength(password):
            return ctx.back("/admins", "weak_password")
        try:
            store.create_admin(username, security.hash_password(password), role, must_change_password=True,
                               created_by=session["username"])
        except sqlite3.IntegrityError:
            return ctx.back("/admins", "admin_exists")
        store.add_audit(session["username"], "admin_created", f"{username} ({ROLE_LABELS[role]})")
        return ctx.back("/admins", "admin_created")

    def target_or_flash(admin_id: int, session):
        """The administrator to act on, or the flash code explaining why not."""
        target = store.get_admin_by_id(admin_id)
        if target is None:
            return None, "not_found"
        if target["id"] == session["admin_id"]:
            return None, "admin_self"
        return target, None

    def would_remove_last_owner(target, becomes_owner: bool, becomes_active: bool) -> bool:
        was_active_owner = target["role"] == "owner" and target["active"]
        return was_active_owner and not (becomes_owner and becomes_active) and store.active_owner_count() <= 1

    @app.post("/admins/{admin_id}/role")
    def admin_role(admin_id: int, role: str = Form(""), csrf: str = Form(...), session=Depends(ctx.require_owner)):
        ctx.check_csrf(session, csrf)
        target, problem = target_or_flash(admin_id, session)
        if problem:
            return ctx.back("/admins", problem)
        if role not in ROLES:
            return ctx.back("/admins", "admin_invalid")
        if would_remove_last_owner(target, role == "owner", bool(target["active"])):
            return ctx.back("/admins", "admin_last_owner")
        store.set_admin_role(admin_id, role)
        store.add_audit(session["username"], "admin_role_changed", f"{target['username']}: {ROLE_LABELS[target['role']]} → {ROLE_LABELS[role]}")
        return ctx.back("/admins", "admin_updated")

    @app.post("/admins/{admin_id}/active")
    def admin_active(admin_id: int, csrf: str = Form(...), session=Depends(ctx.require_owner)):
        ctx.check_csrf(session, csrf)
        target, problem = target_or_flash(admin_id, session)
        if problem:
            return ctx.back("/admins", problem)
        now_active = not target["active"]
        if would_remove_last_owner(target, target["role"] == "owner", now_active):
            return ctx.back("/admins", "admin_last_owner")
        store.set_admin_active(admin_id, now_active)
        store.add_audit(session["username"], "admin_activated" if now_active else "admin_deactivated", target["username"])
        return ctx.back("/admins", "admin_updated")

    @app.post("/admins/{admin_id}/password")
    def admin_reset_password(
        admin_id: int, password: str = Form(""), csrf: str = Form(...), session=Depends(ctx.require_owner),
    ):
        ctx.check_csrf(session, csrf)
        target, problem = target_or_flash(admin_id, session)
        if problem:
            return ctx.back("/admins", problem)
        if security.check_password_strength(password):
            return ctx.back("/admins", "weak_password")
        store.set_admin_password(admin_id, security.hash_password(password), must_change=True)
        store.add_audit(session["username"], "admin_password_reset", target["username"])
        return ctx.back("/admins", "admin_password_reset")

    @app.post("/admins/{admin_id}/reset-2fa")
    def admin_reset_2fa(admin_id: int, csrf: str = Form(...), session=Depends(ctx.require_owner)):
        ctx.check_csrf(session, csrf)
        target, problem = target_or_flash(admin_id, session)
        if problem:
            return ctx.back("/admins", problem)
        store.disable_totp(admin_id)
        store.add_audit(session["username"], "admin_2fa_reset", target["username"])
        return ctx.back("/admins", "admin_2fa_reset")
