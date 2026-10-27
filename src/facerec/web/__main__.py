"""`python -m facerec.web`: the administration web app on its own.

Unlike `facerec web` it does not import the recognition engine, so it runs where dlib and a camera are not
available (the Docker image of the web app). Every option can also come from an environment variable.
"""

import argparse
import os
import socket
import sys
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m facerec.web", description=__doc__)
    p.add_argument("--db", type=Path, default=Path(_env("FACEGATE_DB", "data/facegate.db")))
    p.add_argument("--host", default=_env("FACEGATE_HOST", "127.0.0.1"), help="127.0.0.1 = this machine only")
    p.add_argument("--port", type=int, default=int(_env("FACEGATE_PORT", "8000")))
    live = _env("FACEGATE_LIVE_DIR")
    p.add_argument("--live-dir", type=Path, default=Path(live) if live else None,
                   help="folder where the camera service publishes the current frame (enables the live page)")
    p.add_argument("--secure-cookies", action="store_true",
                   default=_env("FACEGATE_SECURE_COOKIES", "") in ("1", "true", "yes"),
                   help="cookies only over HTTPS (behind a TLS proxy)")
    return p


def check_port_free(host: str, port: int) -> None:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError as err:
            sys.exit(f"Port {port} unavailable ({err.strerror or err}). Try --port {port + 1}.")


def create_admin(argv: list[str]) -> None:
    """`python -m facerec.web create-admin NAME [--db PATH] [--role owner|manager|viewer]`: the password is
    typed in the terminal (never on the command line, where it would stay in the shell history)."""
    import getpass

    from ..store import ROLES, Store
    from . import security

    p = argparse.ArgumentParser(prog="python -m facerec.web create-admin")
    p.add_argument("username")
    p.add_argument("--db", type=Path, default=Path(_env("FACEGATE_DB", "data/facegate.db")))
    p.add_argument("--role", choices=ROLES, default="owner")
    args = p.parse_args(argv)
    store = Store(args.db)
    if store.get_admin(args.username):
        sys.exit(f"The administrator '{args.username}' already exists.")
    password = getpass.getpass("Password: ")
    problem = security.check_password_strength(password)
    if problem:
        sys.exit(f"Password too weak: {problem}.")
    if getpass.getpass("Confirm the password: ") != password:
        sys.exit("The passwords do not match.")
    store.create_admin(args.username, security.hash_password(password), args.role)
    print(f"Administrator '{args.username}' ({args.role}) created in {args.db}")


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    from .app import create_app
    from ..store import Store

    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "create-admin":
        return create_admin(argv[1:])
    args = build_parser().parse_args(argv)
    if not Store(args.db).admin_count():
        print("No administrator yet: create one with `facerec admin create NAME`.", file=sys.stderr)
    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.secure_cookies:
        print("Warning: outside this machine, put the app behind HTTPS and use --secure-cookies.", file=sys.stderr)
    check_port_free(args.host, args.port)
    app = create_app(args.db, secure_cookies=args.secure_cookies, live_dir=args.live_dir)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
