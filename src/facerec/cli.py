import argparse
import getpass
import re
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import cv2

from .attacks import SCENARIOS, ScenarioLog, build_report
from .config import Settings
from .crypto import CryptoError, VectorCipher, encode_key, load_key, new_key
from .door import make_door
from .gallery import Gallery
from .live import capture_samples, list_cameras, run_live
from .policy import AccessPolicy, policy_from_store
from .store import ROLES, Store
from .web import security

DEFAULT_GALLERY = Path("gallery/gallery.npz")
CONSENT = (
    "Ce programme calcule une empreinte numérique de votre visage (128 nombres) pour vous reconnaître.\n"
    "Aucune photo n'est conservée. Vous pouvez demander la suppression : facerec delete NOM.\n"
    "Consentez-vous à cet enregistrement ? [o/N] "
)


def _settings(args: argparse.Namespace) -> Settings:
    s = Settings()
    keys = ("match_threshold", "min_margin", "detector", "embedder")
    overrides = {k: getattr(args, k) for k in keys if getattr(args, k, None) is not None}
    return replace(s, **overrides)


DEFAULT_KEY_FILE = Path("data/facegate.key")
KEY_NOTICE = (
    "Une clé de chiffrement a été créée : {source}.\n"
    "  → Sauvegardez-la AILLEURS que la base (gestionnaire de mots de passe, coffre) : sans elle, les empreintes sont perdues.\n"
    "  → Idéalement, passez-la par la variable d'environnement FACEGATE_KEY plutôt que par un fichier à côté de la base."
)


def open_store(db: Path, key_file: Path | None, create_key: bool = False) -> Store:
    """The database with its embeddings encrypted. A key is created on first use when `create_key`, but
    never when the database already holds encrypted embeddings: a new key could not open them, and
    silently replacing a lost key would hide the problem."""
    key, source = load_key(key_file)
    if key is None and create_key:
        if Store(db).encryption_status()["encrypted"]:
            raise SystemExit(
                "La base contient des empreintes chiffrées mais aucune clé n'est disponible "
                f"(variable FACEGATE_KEY ou fichier {key_file}). Retrouvez la clé : en créer une nouvelle "
                "ne permettrait pas de les ouvrir."
            )
        key, source = load_key(key_file, create=True)
        print(KEY_NOTICE.format(source=source))
    try:
        return Store(db, VectorCipher(key) if key else None)
    except CryptoError as err:
        raise SystemExit(f"Clé de chiffrement invalide : {err}")


def warn_if_plaintext(store: Store) -> None:
    status = store.encryption_status()
    if status["plain"] and store.cipher is not None:
        print(f"Attention : {status['plain']} empreinte(s) ne sont pas chiffrées. Lancez : facerec db encrypt")
    elif status["plain"]:
        print(f"Attention : {status['plain']} empreinte(s) en clair, aucune clé de chiffrement. Lancez : facerec db encrypt")


def cmd_enroll(args: argparse.Namespace) -> None:
    if not args.yes and input(CONSENT).strip().lower() not in {"o", "oui", "y", "yes"}:
        print("Annulé : sans consentement, rien n'est enregistré.")
        return
    settings, embeddings = _settings(args), []
    gallery = Gallery.load(args.gallery)
    if len(gallery) and gallery.embedder != settings.embedder:
        raise SystemExit(
            f"Cette galerie utilise le modèle « {gallery.embedder} » : relancez avec --embedder {gallery.embedder}, "
            f"ou supprimez {args.gallery} pour repartir avec « {settings.embedder} »."
        )
    gallery.embedder = settings.embedder

    def on_sample(frame, rgb, shape, engine):
        embeddings.append(engine.embed(rgb, shape))

    taken = capture_samples(args.camera, settings, args.samples, on_sample, f"inscription {args.name}")
    if taken < args.samples:
        print(f"Inscription incomplète ({taken}/{args.samples}) : rien n'est enregistré.")
        return
    if args.db:  # same consent as above, recorded with the person; shown in the web app
        try:
            open_store(args.db, args.key_file, create_key=True).add_person(
                args.name, embeddings, settings.embedder, "consentement recueilli avec facerec enroll")
        except sqlite3.IntegrityError:
            raise SystemExit(f"« {args.name} » existe déjà dans {args.db} : supprimez-la d'abord dans l'interface web.")
        print(f"{args.name} inscrit ({taken} empreintes) dans {args.db}")
        return
    gallery.enroll(args.name, embeddings)
    gallery.save(args.gallery)
    print(f"{args.name} inscrit ({taken} empreintes) dans {args.gallery}")


def cmd_collect(args: argparse.Namespace) -> None:
    """Save labelled images of a consenting volunteer, only to evaluate the system (kept local)."""
    if not args.yes and input(
        "Ces photos servent uniquement à évaluer le système, restent sur cet ordinateur et "
        "seront supprimées après l'étude. Consentez-vous ? [o/N] "
    ).strip().lower() not in {"o", "oui", "y", "yes"}:
        print("Annulé.")
        return
    folder = Path(args.out) / re.sub(r"[^\w-]", "_", args.name)
    folder.mkdir(parents=True, exist_ok=True)
    start = len(list(folder.glob("*.jpg")))

    def on_sample(frame, rgb, shape, engine):
        cv2.imwrite(str(folder / f"{start + len(list(folder.glob('*.jpg'))):03d}.jpg"), frame)

    taken = capture_samples(args.camera, _settings(args), args.count, on_sample, f"collecte {args.name}", 0.8)
    print(f"{taken} images enregistrées dans {folder}")


def cmd_run(args: argparse.Namespace) -> None:
    # first of all, before anything is opened: an attack test must never be able to drive a real lock
    if args.attack_log and args.door == "gpio":
        raise SystemExit("Mode test d'attaques : jamais avec une vraie gâche (--door gpio). Utilisez --door sim ou rien.")
    store = open_store(args.db, args.key_file, create_key=True) if args.db else None
    if store is not None:
        warn_if_plaintext(store)
        events, audit = store.apply_retention()  # the web app also does it every 6 hours; this covers a service started alone
        if events or audit:
            print(f"Conservation : {events} accès et {audit} entrées d'administration supprimés (durées dépassées).")
        # people and rules come from the web app's database and are reloaded when it changes them
        settings = _settings(args)
        gallery = store.load_gallery(settings.embedder)
        if not len(gallery):
            raise SystemExit(
                "Aucune personne active dans la base pour le modèle « %s ».\n"
                "Importez la galerie existante (facerec import-gallery) ou inscrivez quelqu'un "
                "(facerec enroll NOM --db %s), ou activez une personne dans l'interface web." % (settings.embedder, args.db)
            )
    else:
        gallery = Gallery.load(args.gallery)
        settings = replace(_settings(args), embedder=gallery.embedder)  # empreintes comparables uniquement entre elles
    door = make_door(args.door, args.door_seconds, args.gpio_pin)
    reload_rules = store is not None and not args.policy
    if args.policy:
        policy = AccessPolicy.load(args.policy)
    elif store is not None:
        policy = policy_from_store(store)
        if policy is None:
            print("Aucune zone définie dans la base : toute personne reconnue est autorisée. "
                  "Créez des zones et des règles dans l'interface web pour restreindre l'accès.")
    else:
        policy = None
    run_live(
        gallery, settings, args.camera, args.liveness, Path(args.log) if args.log else None,
        args.challenge, door, policy, args.zone, store, live_reload=store is not None, reload_rules=reload_rules,
        live_dir=args.live_dir, show_window=args.show_window,
        scenario_log=ScenarioLog(args.attack_log) if args.attack_log else None, scenario=args.scenario,
    )


DEFAULT_DB = Path("data/facegate.db")


def cmd_admin_create(args: argparse.Namespace) -> None:
    store = Store(args.db)
    if store.get_admin(args.username):
        raise SystemExit(f"L'administrateur « {args.username} » existe déjà.")
    password = getpass.getpass("Mot de passe : ")
    problem = security.check_password_strength(password)
    if problem:
        raise SystemExit(f"Mot de passe trop faible : {problem}.")
    if getpass.getpass("Confirmez le mot de passe : ") != password:
        raise SystemExit("Les mots de passe ne correspondent pas.")
    store.create_admin(args.username, security.hash_password(password), args.role)
    print(f"Administrateur « {args.username} » ({args.role}) créé dans {args.db}")


def cmd_import_gallery(args: argparse.Namespace) -> None:
    gallery, store = Gallery.load(args.gallery), open_store(args.db, args.key_file, create_key=True)
    existing = {p["name"].lower() for p in store.list_people()}
    added = 0
    for name in gallery.names:
        if name.lower() in existing:
            continue
        store.add_person(name, gallery.embeddings_of(name), gallery.embedder, "importé depuis la galerie existante")
        added += 1
    print(f"{added} personne(s) importée(s) dans {args.db} ({len(gallery.names) - added} déjà présente(s)).")


def _check_port_free(host: str, port: int) -> None:
    """Fail early with advice: Windows reserves some ports (WinError 10013), others may be in use."""
    import socket

    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError as err:
            raise SystemExit(
                f"Le port {port} n'est pas utilisable ({err.strerror or err}).\n"
                f"Essayez un autre port, par exemple : facerec web --port {port + 765 if port == 8000 else port + 1}"
            )


def cmd_attack_report(args: argparse.Namespace) -> None:
    try:
        text = build_report(args.csv)
    except (ValueError, OSError) as err:
        raise SystemExit(f"Rapport impossible : {err}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"Rapport écrit : {args.out}\n")
    print(text)


def cmd_db_status(args: argparse.Namespace) -> None:
    key, source = load_key(args.key_file)
    store = Store(args.db, VectorCipher(key) if key else None)
    status = store.encryption_status()
    print(f"Base : {args.db}")
    print(f"Empreintes chiffrées : {status['encrypted']}   en clair : {status['plain']}")
    print(f"Clé : {source or 'aucune'}")
    if status["encrypted"] and key is None:
        print("→ des empreintes sont chiffrées mais la clé est introuvable : la reconnaissance ne pourra pas démarrer.")
    if status["plain"]:
        print("→ lancez : facerec db encrypt")


def cmd_db_encrypt(args: argparse.Namespace) -> None:
    store = open_store(args.db, args.key_file, create_key=True)
    count = store.encrypt_existing()
    print(f"{count} empreinte(s) chiffrée(s), et les anciennes copies en clair effacées du fichier." if count
          else "Rien à chiffrer : toutes les empreintes le sont déjà.")


def cmd_db_rotate_key(args: argparse.Namespace) -> None:
    key, source = load_key(args.key_file)
    if key is None:
        raise SystemExit("Aucune clé actuelle trouvée (variable FACEGATE_KEY ou --key-file).")
    if args.new_key_file.exists():
        raise SystemExit(f"{args.new_key_file} existe déjà : choisissez un nouveau nom.")
    store, fresh = Store(args.db, VectorCipher(key)), new_key()
    try:
        count = store.rotate_key(VectorCipher(fresh))
    except CryptoError as err:
        raise SystemExit(f"Rotation annulée, rien n'a changé : {err}")
    args.new_key_file.parent.mkdir(parents=True, exist_ok=True)
    args.new_key_file.write_text(encode_key(fresh) + "\n", encoding="ascii")
    print(f"{count} empreinte(s) rechiffrée(s) avec la nouvelle clé : {args.new_key_file}")
    print("→ Utilisez désormais cette clé (remplacez FACEGATE_KEY ou le fichier), puis détruisez l'ancienne.")


def cmd_db_purge(args: argparse.Namespace) -> None:
    store = Store(args.db)
    events_days, audit_days = store.retention_days()
    events, audit = store.apply_retention()
    print(f"Durées de conservation : accès {events_days or 'sans limite'} j, administration {audit_days or 'sans limite'} j.")
    print(f"{events} événement(s) d'accès et {audit} entrée(s) d'administration supprimé(s).")


def cmd_db_backup(args: argparse.Namespace) -> None:
    store = Store(args.db)
    try:
        copy = store.backup_to(args.destination)
    except FileExistsError as err:
        raise SystemExit(str(err))
    print(f"Sauvegarde écrite : {copy}")
    print("Les empreintes y restent chiffrées. Conservez la clé séparément : la sauvegarde seule est inutilisable.")


def cmd_web(args: argparse.Namespace) -> None:
    import uvicorn

    from .web.app import create_app

    if not Store(args.db).admin_count():
        print("Aucun administrateur : créez-en un avec « facerec admin create NOM ».")
    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.secure_cookies:
        print("Attention : hors de cette machine, placez l'application derrière HTTPS et utilisez --secure-cookies.")
    _check_port_free(args.host, args.port)
    print(f"FaceGate : http://{args.host}:{args.port}  (Ctrl+C pour arrêter)")
    app = create_app(args.db, secure_cookies=args.secure_cookies, live_dir=args.live_dir)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")



def cmd_cameras(args: argparse.Namespace) -> None:
    found = list_cameras()
    print("Caméras utilisables : " + (", ".join(map(str, found)) if found else "aucune"))
    if not found:
        print("Fermez les applications qui utilisent la webcam et vérifiez Paramètres > Confidentialité > Caméra.")


def cmd_list(args: argparse.Namespace) -> None:
    gallery = Gallery.load(args.gallery)
    if not len(gallery):
        print("Galerie vide.")
    for name in gallery.names:
        print(f"{name}: {gallery.sample_count(name)} empreintes ({gallery.embedder})")


def cmd_delete(args: argparse.Namespace) -> None:
    gallery = Gallery.load(args.gallery)
    if gallery.remove(args.name):
        gallery.save(args.gallery)
        print(f"{args.name} supprimé.")
    else:
        print(f"{args.name} introuvable.")


def cmd_evaluate(args: argparse.Namespace) -> None:
    from .evaluation import evaluate

    evaluate(Path(args.data), Path(args.out), _settings(args), args.enroll, args.seeds, not args.no_cache)
    print((Path(args.out) / "report.md").read_text(encoding="utf-8"))
    print(f"Fichiers écrits dans {args.out}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="facerec", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser, gallery: bool = True) -> None:
        sp.add_argument("--camera", type=int, default=0)
        sp.add_argument("--match-threshold", dest="match_threshold", type=float)
        sp.add_argument("--min-margin", dest="min_margin", type=float)
        sp.add_argument("--detector", choices=["hog", "yunet"])
        sp.add_argument("--embedder", choices=["dlib", "sface"])
        if gallery:
            sp.add_argument("--gallery", type=Path, default=DEFAULT_GALLERY)

    sp = sub.add_parser("enroll", help="inscrire une personne (empreintes uniquement)")
    sp.add_argument("name")
    sp.add_argument("--samples", type=int, default=20)
    sp.add_argument("--yes", action="store_true", help="consentement déjà obtenu")
    sp.add_argument("--db", type=Path, help="inscrire dans la base FaceGate (visible dans l'interface web)")
    sp.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE, help="clé de chiffrement des empreintes (sinon FACEGATE_KEY)")
    common(sp)
    sp.set_defaults(func=cmd_enroll)

    sp = sub.add_parser("run", help="reconnaissance en direct")
    sp.add_argument("--liveness", action="store_true", help="exiger un clignement des yeux")
    sp.add_argument("--challenge", action="store_true",
                    help="defi aleatoire (clignement / tourner la tete) + detection d'ecran")
    sp.add_argument("--door", choices=["none", "sim", "gpio"], default="none",
                    help="porte : simulateur (sim) ou relais Raspberry Pi (gpio)")
    sp.add_argument("--door-seconds", type=float, default=5.0, help="duree d'ouverture apres un acces autorise")
    sp.add_argument("--gpio-pin", type=int, help="broche GPIO du relais (avec --door gpio)")
    sp.add_argument("--policy", type=Path, help="fichier JSON des droits d'acces (qui, quelle zone, quand)")
    sp.add_argument("--zone", default="default", help="zone controlee par cette porte (voir --policy)")
    sp.add_argument("--log", help="fichier CSV du journal des décisions")
    sp.add_argument("--db", type=Path, help="base FaceGate : y enregistre aussi les accès (tableau de bord web)")
    sp.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE, help="clé de chiffrement des empreintes (sinon FACEGATE_KEY)")
    sp.add_argument("--live-dir", type=Path, help="dossier où publier l'image courante pour la vidéo en direct "
                    "de l'interface web (facultatif : aucune image n'est partagée sans cette option)")
    sp.add_argument("--attack-log", type=Path,
                    help="mode test d'attaques : écrit chaque décision avec son scénario dans ce CSV (voir docs/PROTOCOLE-ATTAQUES.md)")
    sp.add_argument("--scenario", choices=sorted(SCENARIOS), default="reel",
                    help="scénario de départ du mode test (ensuite touches 1 à 5 dans la fenêtre)")
    sp.add_argument("--no-window", dest="show_window", action="store_false",
                    help="ne pas ouvrir la fenêtre OpenCV (service sans écran)")
    common(sp)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("admin", help="gérer les administrateurs de l'application web")
    admin_sub = sp.add_subparsers(dest="admin_command", required=True)
    sp_create = admin_sub.add_parser("create", help="créer un administrateur (mot de passe demandé en clair-masqué)")
    sp_create.add_argument("username")
    sp_create.add_argument("--db", type=Path, default=DEFAULT_DB)
    sp_create.add_argument("--role", choices=ROLES, default="owner",
                           help="owner (tout), manager (personnes, zones, vidéo) ou viewer (tableau de bord et journal)")
    sp_create.set_defaults(func=cmd_admin_create)

    sp = sub.add_parser("import-gallery", help="copier les personnes d'une galerie existante dans la base")
    sp.add_argument("--gallery", type=Path, default=DEFAULT_GALLERY)
    sp.add_argument("--db", type=Path, default=DEFAULT_DB)
    sp.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE, help="clé de chiffrement des empreintes (sinon FACEGATE_KEY)")
    sp.set_defaults(func=cmd_import_gallery)

    sp = sub.add_parser("attack-report", help="rapport (APCER / BPCER) d'une session de tests d'attaques")
    sp.add_argument("csv", type=Path, nargs="+", help="un ou plusieurs fichiers écrits par `facerec run --attack-log`")
    sp.add_argument("--out", type=Path, help="écrire aussi le rapport Markdown dans ce fichier")
    sp.set_defaults(func=cmd_attack_report)

    sp = sub.add_parser("db", help="chiffrement, rotation de clé et sauvegarde de la base")
    db_sub = sp.add_subparsers(dest="db_command", required=True)
    for name, helptext, func in (
        ("status", "état du chiffrement", cmd_db_status),
        ("encrypt", "chiffrer les empreintes encore en clair", cmd_db_encrypt),
    ):
        sp_db = db_sub.add_parser(name, help=helptext)
        sp_db.add_argument("--db", type=Path, default=DEFAULT_DB)
        sp_db.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE)
        sp_db.set_defaults(func=func)
    sp_db = db_sub.add_parser("rotate-key", help="rechiffrer toutes les empreintes avec une nouvelle clé")
    sp_db.add_argument("new_key_file", type=Path, help="fichier où écrire la nouvelle clé (ne doit pas exister)")
    sp_db.add_argument("--db", type=Path, default=DEFAULT_DB)
    sp_db.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE, help="clé actuelle (sinon FACEGATE_KEY)")
    sp_db.set_defaults(func=cmd_db_rotate_key)
    sp_db = db_sub.add_parser("purge", help="supprimer ce qui dépasse les durées de conservation")
    sp_db.add_argument("--db", type=Path, default=DEFAULT_DB)
    sp_db.set_defaults(func=cmd_db_purge)
    sp_db = db_sub.add_parser("backup", help="copie cohérente de la base (empreintes toujours chiffrées)")
    sp_db.add_argument("destination", type=Path)
    sp_db.add_argument("--db", type=Path, default=DEFAULT_DB)
    sp_db.set_defaults(func=cmd_db_backup)

    sp = sub.add_parser("web", help="lancer l'application web d'administration")
    sp.add_argument("--db", type=Path, default=DEFAULT_DB)
    sp.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = cette machine uniquement")
    sp.add_argument("--port", type=int, default=8000)
    sp.add_argument("--secure-cookies", action="store_true", help="cookies réservés à HTTPS (derrière un proxy TLS)")
    sp.add_argument("--live-dir", type=Path, help="dossier où le service caméra publie l'image courante "
                    "(active la page « En direct » ; le même dossier que `facerec run --live-dir`)")
    sp.set_defaults(func=cmd_web)

    sp = sub.add_parser("cameras", help="tester quelles caméras fonctionnent")
    sp.set_defaults(func=cmd_cameras)

    sp = sub.add_parser("list", help="lister les personnes inscrites")
    sp.add_argument("--gallery", type=Path, default=DEFAULT_GALLERY)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("delete", help="supprimer une personne (droit à l'effacement)")
    sp.add_argument("name")
    sp.add_argument("--gallery", type=Path, default=DEFAULT_GALLERY)
    sp.set_defaults(func=cmd_delete)

    sp = sub.add_parser("collect", help="collecter des images de volontaires pour l'évaluation")
    sp.add_argument("name")
    sp.add_argument("--count", type=int, default=30)
    sp.add_argument("--out", default="data/volunteers")
    sp.add_argument("--yes", action="store_true")
    common(sp, gallery=False)
    sp.set_defaults(func=cmd_collect)

    sp = sub.add_parser("evaluate", help="évaluer sur un dossier data/<personne>/<image>")
    sp.add_argument("data")
    sp.add_argument("--out", default="results")
    sp.add_argument("--enroll", type=int, default=3, help="images d'inscription par personne")
    sp.add_argument("--seeds", type=int, default=5)
    sp.add_argument("--no-cache", action="store_true")
    common(sp, gallery=False)
    sp.set_defaults(func=cmd_evaluate)
    return p


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nArrêté.")
    except RuntimeError as err:
        raise SystemExit(f"Erreur : {err}")


if __name__ == "__main__":
    main()
