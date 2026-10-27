"""SQLite store shared by the recognition service and the web app (WAL mode: both can run at once).

Holds administrators, sessions, people (face embeddings only, never photos), zones, access rules
and the access-event history. The schema is created on first use and versioned with
``PRAGMA user_version`` so later phases can migrate it.
"""

import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from .crypto import CryptoError, VectorCipher, aad_for, is_encrypted

SCHEMA_VERSION = 3
ERASED = "[personne supprimée]"  # what remains of a name in the history once the person was erased
PERIODS = {"24h": (24, 1), "7j": (168, 6), "30j": (720, 24)}  # period -> (hours, bucket size in hours)
SPOOF_REASONS = ("screen_detected", "challenge_timeout")  # refusals that look like a presentation attack
DEFAULT_EVENT_RETENTION_DAYS = 90  # access log
DEFAULT_AUDIT_RETENTION_DAYS = 365  # administration log
ROLES = ("viewer", "manager", "owner")
# columns added to `admins` after the first release: older databases get them on open (existing admins become owners)
ADMIN_COLUMNS = {
    "role": "TEXT NOT NULL DEFAULT 'owner'",
    "active": "INTEGER NOT NULL DEFAULT 1",
    "must_change_password": "INTEGER NOT NULL DEFAULT 0",
    "totp_secret": "TEXT",
    "totp_enabled": "INTEGER NOT NULL DEFAULT 0",
    "totp_last_step": "INTEGER NOT NULL DEFAULT 0",
    "created_by": "TEXT",
}
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

SCHEMA = """
CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    role TEXT NOT NULL DEFAULT 'owner',
    active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    totp_secret TEXT,
    totp_enabled INTEGER NOT NULL DEFAULT 0,
    totp_last_step INTEGER NOT NULL DEFAULT 0,
    created_by TEXT
);
CREATE TABLE IF NOT EXISTS pending_2fa (
    token_hash TEXT PRIMARY KEY,
    admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS recovery_codes (
    id INTEGER PRIMARY KEY,
    admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    used_at REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS login_failures (
    key TEXT NOT NULL,
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_failures ON login_failures(key, at);
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    consent_at REAL,
    consent_text TEXT
);
CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    vector BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS zones (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    zone_id INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    always INTEGER NOT NULL DEFAULT 0,
    days TEXT NOT NULL DEFAULT '[]',
    start TEXT,
    end TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    decision TEXT NOT NULL,
    person TEXT,
    zone TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_at ON events(at);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
INSERT OR IGNORE INTO meta (key, value) VALUES ('revision', 0);
CREATE TABLE IF NOT EXISTS enrollments (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    admin TEXT NOT NULL,
    consent_text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    admin TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""


def name_pattern(name: str) -> re.Pattern:
    """The whole name, case-insensitive, not inside a longer word: "Ann" matches "Ann → lab" but not "Anna"."""
    return re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)


def _valid_clock(value: str | None) -> bool:
    try:
        hour, minute = (value or "").split(":")
        return len(hour) == 2 and len(minute) == 2 and 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59
    except ValueError:
        return False


class Store:
    def __init__(self, path: Path | str, cipher: VectorCipher | None = None):
        """`cipher`: encrypts the embeddings written and decrypts those read (None: stored in the clear)."""
        self.path = Path(path)
        self.cipher = cipher
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as db:
            db.executescript(SCHEMA)
            have = {r["name"] for r in db.execute("PRAGMA table_info(admins)")}
            for column, definition in ADMIN_COLUMNS.items():
                if column not in have:
                    db.execute(f"ALTER TABLE admins ADD COLUMN {column} {definition}")
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _conn(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA secure_delete = ON")  # deleted or replaced content is overwritten, not left in free pages
        try:
            with db:  # commit or roll back
                yield db
        finally:
            db.close()

    @staticmethod
    def _bump(db) -> None:
        """Signal the recognition service that people, zones or rules changed (it reloads on change)."""
        db.execute("UPDATE meta SET value = value + 1 WHERE key = 'revision'")

    def revision(self) -> int:
        with self._conn() as db:
            return db.execute("SELECT value FROM meta WHERE key = 'revision'").fetchone()[0]

    # --- administrators ------------------------------------------------------------

    def create_admin(
        self, username: str, password_hash: str, role: str = "owner", must_change_password: bool = False,
        created_by: str | None = None,
    ) -> int:
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        with self._conn() as db:
            return db.execute(
                "INSERT INTO admins (username, password_hash, created_at, role, must_change_password, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (username, password_hash, time.time(), role, int(must_change_password), created_by),
            ).lastrowid

    def get_admin(self, username: str) -> sqlite3.Row | None:
        with self._conn() as db:
            return db.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()

    def get_admin_by_id(self, admin_id: int) -> sqlite3.Row | None:
        with self._conn() as db:
            return db.execute("SELECT * FROM admins WHERE id = ?", (admin_id,)).fetchone()

    def list_admins(self) -> list[sqlite3.Row]:
        with self._conn() as db:
            return db.execute("SELECT * FROM admins ORDER BY username").fetchall()

    def admin_count(self) -> int:
        with self._conn() as db:
            return db.execute("SELECT COUNT(*) FROM admins").fetchone()[0]

    def active_owner_count(self) -> int:
        with self._conn() as db:
            return db.execute("SELECT COUNT(*) FROM admins WHERE role = 'owner' AND active = 1").fetchone()[0]

    def set_admin_role(self, admin_id: int, role: str) -> None:
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        with self._conn() as db:
            db.execute("UPDATE admins SET role = ? WHERE id = ?", (role, admin_id))

    def set_admin_active(self, admin_id: int, active: bool) -> None:
        """A deactivated administrator is signed out everywhere at once."""
        with self._conn() as db:
            db.execute("UPDATE admins SET active = ? WHERE id = ?", (int(active), admin_id))
            if not active:
                db.execute("DELETE FROM sessions WHERE admin_id = ?", (admin_id,))
                db.execute("DELETE FROM pending_2fa WHERE admin_id = ?", (admin_id,))

    def set_admin_password(self, admin_id: int, password_hash: str, must_change: bool = False,
                           keep_token: str | None = None) -> None:
        """New password; every other session of that administrator is closed (`keep_token`: the current one)."""
        with self._conn() as db:
            db.execute("UPDATE admins SET password_hash = ?, must_change_password = ? WHERE id = ?",
                       (password_hash, int(must_change), admin_id))
            if keep_token is None:
                db.execute("DELETE FROM sessions WHERE admin_id = ?", (admin_id,))
            else:
                db.execute("DELETE FROM sessions WHERE admin_id = ? AND token_hash != ?",
                           (admin_id, self._hash(keep_token)))

    # --- two-factor authentication -------------------------------------------------------------------

    def set_totp_secret(self, admin_id: int, secret: str) -> None:
        """Start enrollment: the secret is stored but not active until a code from the app has been checked."""
        with self._conn() as db:
            db.execute("UPDATE admins SET totp_secret = ?, totp_enabled = 0, totp_last_step = 0 WHERE id = ?",
                       (secret, admin_id))

    def enable_totp(self, admin_id: int, step: int, recovery_hashes: list[str]) -> None:
        with self._conn() as db:
            db.execute("UPDATE admins SET totp_enabled = 1, totp_last_step = ? WHERE id = ? AND totp_secret IS NOT NULL",
                       (step, admin_id))
            db.execute("DELETE FROM recovery_codes WHERE admin_id = ?", (admin_id,))
            db.executemany("INSERT INTO recovery_codes (admin_id, code_hash) VALUES (?, ?)",
                           [(admin_id, h) for h in recovery_hashes])

    def disable_totp(self, admin_id: int) -> None:
        with self._conn() as db:
            db.execute("UPDATE admins SET totp_secret = NULL, totp_enabled = 0, totp_last_step = 0 WHERE id = ?",
                       (admin_id,))
            db.execute("DELETE FROM recovery_codes WHERE admin_id = ?", (admin_id,))

    def update_totp_step(self, admin_id: int, step: int) -> bool:
        """Record the step of an accepted code. False when it is not newer (a concurrent replay): refuse."""
        with self._conn() as db:
            return db.execute("UPDATE admins SET totp_last_step = ? WHERE id = ? AND totp_last_step < ?",
                              (step, admin_id, step)).rowcount == 1

    def replace_recovery_codes(self, admin_id: int, recovery_hashes: list[str]) -> None:
        with self._conn() as db:
            db.execute("DELETE FROM recovery_codes WHERE admin_id = ?", (admin_id,))
            db.executemany("INSERT INTO recovery_codes (admin_id, code_hash) VALUES (?, ?)",
                           [(admin_id, h) for h in recovery_hashes])

    def unused_recovery_codes(self, admin_id: int) -> list[sqlite3.Row]:
        with self._conn() as db:
            return db.execute("SELECT id, code_hash FROM recovery_codes WHERE admin_id = ? AND used_at IS NULL",
                              (admin_id,)).fetchall()

    def use_recovery_code(self, code_id: int) -> bool:
        """One-time: False when it was already used (two requests racing with the same code)."""
        with self._conn() as db:
            return db.execute("UPDATE recovery_codes SET used_at = ? WHERE id = ? AND used_at IS NULL",
                              (time.time(), code_id)).rowcount == 1

    def create_pending_2fa(self, admin_id: int, token: str, ttl: float) -> None:
        with self._conn() as db:
            db.execute("DELETE FROM pending_2fa WHERE expires_at < ?", (time.time(),))
            db.execute("INSERT INTO pending_2fa (token_hash, admin_id, expires_at) VALUES (?, ?, ?)",
                       (self._hash(token), admin_id, time.time() + ttl))

    def get_pending_2fa(self, token: str) -> sqlite3.Row | None:
        with self._conn() as db:
            return db.execute(
                "SELECT p.admin_id, a.username, a.totp_secret, a.totp_last_step FROM pending_2fa p "
                "JOIN admins a ON a.id = p.admin_id WHERE p.token_hash = ? AND p.expires_at > ? AND a.active = 1 "
                "AND a.totp_enabled = 1", (self._hash(token), time.time()),
            ).fetchone()

    def delete_pending_2fa(self, token: str) -> None:
        with self._conn() as db:
            db.execute("DELETE FROM pending_2fa WHERE token_hash = ?", (self._hash(token),))

    # --- sessions (only a hash of the token is stored) ----------------------------

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create_session(self, admin_id: int, token: str, csrf: str, ttl: float) -> None:
        with self._conn() as db:
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
            db.execute(
                "INSERT INTO sessions (token_hash, admin_id, csrf, expires_at) VALUES (?, ?, ?, ?)",
                (self._hash(token), admin_id, csrf, time.time() + ttl),
            )

    def get_session(self, token: str) -> sqlite3.Row | None:
        with self._conn() as db:
            return db.execute(
                "SELECT s.csrf, s.admin_id, a.username, a.role, a.must_change_password, a.totp_enabled "
                "FROM sessions s JOIN admins a ON a.id = s.admin_id "
                "WHERE s.token_hash = ? AND s.expires_at > ? AND a.active = 1",
                (self._hash(token), time.time()),
            ).fetchone()

    def delete_session(self, token: str) -> None:
        with self._conn() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (self._hash(token),))

    # --- login throttling ------------------------------------------------------------

    def record_login_failure(self, key: str) -> None:
        with self._conn() as db:
            db.execute("INSERT INTO login_failures (key, at) VALUES (?, ?)", (key, time.time()))

    def recent_login_failures(self, key: str, window: float) -> int:
        with self._conn() as db:
            db.execute("DELETE FROM login_failures WHERE at < ?", (time.time() - 86400,))
            return db.execute(
                "SELECT COUNT(*) FROM login_failures WHERE key = ? AND at > ?", (key, time.time() - window)
            ).fetchone()[0]

    def clear_login_failures(self, key: str) -> None:
        with self._conn() as db:
            db.execute("DELETE FROM login_failures WHERE key = ?", (key,))

    # --- embeddings: encryption at rest -----------------------------------------------------------

    def _encode(self, vector: np.ndarray, person_id: int, model: str) -> bytes:
        raw = np.asarray(vector, dtype=np.float32).tobytes()
        return self.cipher.encrypt(raw, aad_for(person_id, model)) if self.cipher else raw

    def _decode(self, blob: bytes, person_id: int, model: str) -> np.ndarray:
        if is_encrypted(blob):
            if self.cipher is None:
                raise CryptoError("les empreintes sont chiffrées : fournissez la clé (FACEGATE_KEY ou --key-file)")
            blob = self.cipher.decrypt(blob, aad_for(person_id, model))
        return np.frombuffer(blob, dtype=np.float32)

    def _scrub(self) -> None:
        """After rewriting embeddings: empty the write-ahead log and rebuild the file, so that no earlier
        (plaintext) copy of a value survives in free pages or in the log."""
        db = sqlite3.connect(self.path, isolation_level=None)  # autocommit: VACUUM cannot run inside a transaction
        try:
            db.execute("PRAGMA secure_delete = ON")
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("VACUUM")
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            db.close()

    def encryption_status(self) -> dict:
        """How many embeddings are stored encrypted and how many in the clear."""
        with self._conn() as db:
            blobs = [r[0] for r in db.execute("SELECT vector FROM embeddings")]
        encrypted = sum(is_encrypted(b) for b in blobs)
        return {"encrypted": encrypted, "plain": len(blobs) - encrypted}

    def encrypt_existing(self) -> int:
        """Encrypt every embedding still stored in the clear (needs a cipher). Returns how many."""
        if self.cipher is None:
            raise CryptoError("clé requise pour chiffrer")
        done = 0
        with self._conn() as db:
            for row in db.execute("SELECT id, person_id, model, vector FROM embeddings").fetchall():
                if not is_encrypted(row["vector"]):
                    vector = np.frombuffer(row["vector"], dtype=np.float32)
                    db.execute("UPDATE embeddings SET vector = ? WHERE id = ?",
                               (self._encode(vector, row["person_id"], row["model"]), row["id"]))
                    done += 1
        if done:
            self._scrub()
        return done

    def rotate_key(self, new_cipher: VectorCipher) -> int:
        """Re-encrypt everything under a new key, atomically: all rows change or none do."""
        done = 0
        with self._conn() as db:
            for row in db.execute("SELECT id, person_id, model, vector FROM embeddings").fetchall():
                vector = self._decode(row["vector"], row["person_id"], row["model"])
                blob = new_cipher.encrypt(vector.astype(np.float32).tobytes(), aad_for(row["person_id"], row["model"]))
                db.execute("UPDATE embeddings SET vector = ? WHERE id = ?", (blob, row["id"]))
                done += 1
        self.cipher = new_cipher
        self._scrub()
        return done

    def backup_to(self, destination: Path | str) -> Path:
        """A consistent copy taken while the database is in use (embeddings stay encrypted in it)."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(f"{destination} existe déjà")
        source = sqlite3.connect(self.path)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return destination

    # --- people ----------------------------------------------------------------------

    def add_person(
        self, name: str, embeddings: np.ndarray, model: str, consent_text: str | None = None
    ) -> int:
        """Store a person and their embeddings (128 numbers each). Never an image."""
        now = time.time()
        with self._conn() as db:
            cur = db.execute(
                "INSERT INTO people (name, created_at, consent_at, consent_text) VALUES (?, ?, ?, ?)",
                (name, now, now if consent_text else None, consent_text),
            )
            db.executemany(
                "INSERT INTO embeddings (person_id, model, vector) VALUES (?, ?, ?)",
                [(cur.lastrowid, model, self._encode(v, cur.lastrowid, model)) for v in embeddings],
            )
            self._bump(db)
            return cur.lastrowid

    def get_person(self, person_id: int) -> sqlite3.Row | None:
        with self._conn() as db:
            return db.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()

    def list_people(self) -> list[sqlite3.Row]:
        with self._conn() as db:
            return db.execute(
                "SELECT p.id, p.name, p.active, p.created_at, p.consent_at, COUNT(e.id) AS samples "
                "FROM people p LEFT JOIN embeddings e ON e.person_id = p.id GROUP BY p.id ORDER BY p.name"
            ).fetchall()

    def set_person_active(self, person_id: int, active: bool) -> None:
        with self._conn() as db:
            db.execute("UPDATE people SET active = ? WHERE id = ?", (int(active), person_id))
            self._bump(db)

    def delete_person(self, person_id: int) -> bool:
        """Right to erasure: the person, their embeddings and rules, and their name everywhere else.

        The access log keeps its decisions (statistics, security) but without the name; the administration
        log keeps who did what but without the name; enrollment requests of that name are deleted."""
        with self._conn() as db:
            row = db.execute("SELECT name FROM people WHERE id = ?", (person_id,)).fetchone()
            if row is None:
                return False
            name = row["name"]
            db.execute("DELETE FROM people WHERE id = ?", (person_id,))
            db.execute("UPDATE events SET person = ? WHERE lower(person) = lower(?)", (ERASED, name))
            pattern = name_pattern(name)  # whole name only: erasing "Ann" must leave "Anna" alone
            for entry in db.execute("SELECT id, detail FROM audit WHERE instr(lower(detail), lower(?)) > 0",
                                    (name,)).fetchall():
                db.execute("UPDATE audit SET detail = ? WHERE id = ?", (pattern.sub(ERASED, entry["detail"]), entry["id"]))
            db.execute("DELETE FROM enrollments WHERE lower(name) = lower(?) AND status NOT IN ('pending', 'running')",
                       (name,))
            self._bump(db)
        return True

    def embeddings_by_person(self, only_active: bool = True) -> dict[str, np.ndarray]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT p.name, e.person_id, e.model, e.vector FROM embeddings e JOIN people p ON p.id = e.person_id "
                + ("WHERE p.active = 1 " if only_active else "")
                + "ORDER BY p.name, e.id"
            ).fetchall()
        grouped: dict[str, list[np.ndarray]] = {}
        for row in rows:
            grouped.setdefault(row["name"], []).append(self._decode(row["vector"], row["person_id"], row["model"]))
        return {name: np.vstack(vectors) for name, vectors in grouped.items()}

    def load_gallery(self, embedder: str, only_active: bool = True):
        """People whose embeddings come from `embedder`, as a `Gallery` for the recognition loop.

        Active people only by default; enrollment also checks the deactivated ones for duplicates."""
        from .gallery import Gallery

        gallery = Gallery(embedder)
        with self._conn() as db:
            rows = db.execute(
                "SELECT p.name, e.person_id, e.model, e.vector FROM embeddings e JOIN people p ON p.id = e.person_id "
                "WHERE " + ("p.active = 1 AND " if only_active else "") + "e.model = ? ORDER BY p.name, e.id",
                (embedder,),
            ).fetchall()
        grouped: dict[str, list[np.ndarray]] = {}
        for row in rows:  # a wrong key or a tampered row raises: an access system must not silently drop people
            grouped.setdefault(row["name"], []).append(self._decode(row["vector"], row["person_id"], row["model"]))
        for name, vectors in grouped.items():
            gallery.enroll(name, np.vstack(vectors))
        return gallery

    # --- zones and rules ---------------------------------------------------------------

    def add_zone(self, name: str) -> int:
        with self._conn() as db:
            zone_id = db.execute("INSERT INTO zones (name) VALUES (?)", (name,)).lastrowid
            self._bump(db)
            return zone_id

    def list_zones(self) -> list[sqlite3.Row]:
        with self._conn() as db:
            return db.execute(
                "SELECT z.id, z.name, COUNT(r.id) AS rules FROM zones z "
                "LEFT JOIN rules r ON r.zone_id = z.id GROUP BY z.id ORDER BY z.name"
            ).fetchall()

    def delete_zone(self, zone_id: int) -> bool:
        with self._conn() as db:
            removed = db.execute("DELETE FROM zones WHERE id = ?", (zone_id,)).rowcount > 0
            self._bump(db)
            return removed

    def add_rule(
        self, zone_id: int, person_id: int, always: bool = False,
        days: list[str] | None = None, start: str | None = None, end: str | None = None,
    ) -> int:
        """`always`, or a weekly window (days + start/end as HH:MM). Anything else is refused."""
        if not always:
            days = list(days or [])
            if not days or any(d not in DAYS for d in days):
                raise ValueError("choisissez au moins un jour valide")
            if not (_valid_clock(start) and _valid_clock(end)) or start == end:
                raise ValueError("horaires invalides (format HH:MM, début différent de la fin)")
        with self._conn() as db:
            rule_id = db.execute(
                "INSERT INTO rules (zone_id, person_id, always, days, start, end) VALUES (?, ?, ?, ?, ?, ?)",
                (zone_id, person_id, int(always), json.dumps(days or []), None if always else start,
                 None if always else end),
            ).lastrowid
            self._bump(db)
            return rule_id

    def list_rules(self) -> list[dict]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT r.id, r.zone_id, z.name AS zone, p.name AS person, r.always, r.days, r.start, r.end "
                "FROM rules r JOIN zones z ON z.id = r.zone_id JOIN people p ON p.id = r.person_id "
                "ORDER BY z.name, p.name, r.id"
            ).fetchall()
        return [{**dict(r), "days": json.loads(r["days"])} for r in rows]

    def delete_rule(self, rule_id: int) -> bool:
        with self._conn() as db:
            removed = db.execute("DELETE FROM rules WHERE id = ?", (rule_id,)).rowcount > 0
            self._bump(db)
            return removed

    def policy_dict(self) -> dict:
        """The rules in the format `AccessPolicy.from_dict` reads. Inactive people get no access."""
        with self._conn() as db:
            zones = {z["name"]: {} for z in db.execute("SELECT name FROM zones")}
            rows = db.execute(
                "SELECT z.name AS zone, p.name AS person, r.always, r.days, r.start, r.end "
                "FROM rules r JOIN zones z ON z.id = r.zone_id JOIN people p ON p.id = r.person_id "
                "WHERE p.active = 1"
            ).fetchall()
        for row in rows:
            people = zones[row["zone"]]
            if row["always"]:
                people[row["person"]] = "always"
            elif people.get(row["person"]) != "always":
                people.setdefault(row["person"], []).append(
                    {"days": json.loads(row["days"]) or list(DAYS), "from": row["start"], "to": row["end"]}
                )
        return {"zones": zones}

    # --- events ------------------------------------------------------------------------

    def add_event(
        self, decision: str, person: str | None, zone: str = "", reason: str = "", at: float | None = None
    ) -> None:
        with self._conn() as db:
            db.execute(
                "INSERT INTO events (at, decision, person, zone, reason) VALUES (?, ?, ?, ?, ?)",
                (at if at is not None else time.time(), decision, person, zone, reason),
            )

    @staticmethod
    def _event_filter(decision, person, zone, since, until) -> tuple[str, list]:
        where, params = [], []
        for column, value in (("decision", decision), ("person", person), ("zone", zone)):
            if value:
                where.append(f"{column} = ?")
                params.append(value)
        if since is not None:
            where.append("at >= ?")
            params.append(since)
        if until is not None:
            where.append("at < ?")
            params.append(until)
        return (("WHERE " + " AND ".join(where)) if where else ""), params

    def list_events(
        self, limit: int = 50, offset: int = 0, decision: str | None = None, person: str | None = None,
        zone: str | None = None, since: float | None = None, until: float | None = None,
    ) -> list[sqlite3.Row]:
        clause, params = self._event_filter(decision, person, zone, since, until)
        with self._conn() as db:
            return db.execute(
                f"SELECT * FROM events {clause} ORDER BY at DESC, id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()

    def count_events(
        self, decision: str | None = None, person: str | None = None, zone: str | None = None,
        since: float | None = None, until: float | None = None,
    ) -> int:
        clause, params = self._event_filter(decision, person, zone, since, until)
        with self._conn() as db:
            return db.execute(f"SELECT COUNT(*) FROM events {clause}", params).fetchone()[0]

    def event_zones(self) -> list[str]:
        with self._conn() as db:
            return [r[0] for r in db.execute("SELECT DISTINCT zone FROM events WHERE zone != '' ORDER BY zone")]

    # --- enrollment requests (web app asks, the recognition service owning the camera performs) --------

    ACTIVE_ENROLLMENT = ("pending", "running")

    def request_enrollment(self, name: str, admin: str, consent_text: str, total: int = 20) -> int:
        """Queue an enrollment. One at a time, and never for a name that already exists."""
        with self._conn() as db:
            if db.execute("SELECT 1 FROM people WHERE name = ?", (name,)).fetchone():
                raise ValueError("name_exists")
            if db.execute("SELECT 1 FROM enrollments WHERE status IN ('pending', 'running')").fetchone():
                raise ValueError("busy")
            return db.execute(
                "INSERT INTO enrollments (name, status, total, created_at, admin, consent_text) "
                "VALUES (?, 'pending', ?, ?, ?, ?)",
                (name, total, time.time(), admin, consent_text),
            ).lastrowid

    def claim_enrollment(self) -> sqlite3.Row | None:
        """The service takes the oldest pending request (atomically: it becomes 'running')."""
        with self._conn() as db:
            row = db.execute(
                "SELECT * FROM enrollments WHERE status = 'pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute("UPDATE enrollments SET status = 'running', message = '' WHERE id = ?", (row["id"],))
            return db.execute("SELECT * FROM enrollments WHERE id = ?", (row["id"],)).fetchone()

    def update_enrollment(self, enrollment_id: int, **fields) -> None:
        allowed = {"status", "progress", "message"}
        if not fields or set(fields) - allowed:
            raise ValueError(f"fields must be among {sorted(allowed)}")
        with self._conn() as db:
            # a request cancelled meanwhile stays cancelled, whatever the service reports afterwards
            db.execute(
                f"UPDATE enrollments SET {', '.join(f + ' = ?' for f in fields)} "
                "WHERE id = ? AND status IN ('pending', 'running')",
                (*fields.values(), enrollment_id),
            )

    def get_enrollment(self, enrollment_id: int) -> sqlite3.Row | None:
        with self._conn() as db:
            return db.execute("SELECT * FROM enrollments WHERE id = ?", (enrollment_id,)).fetchone()

    def cancel_enrollment(self, enrollment_id: int) -> bool:
        with self._conn() as db:
            return db.execute(
                "UPDATE enrollments SET status = 'cancelled', message = 'Annulée' "
                "WHERE id = ? AND status IN ('pending', 'running')",
                (enrollment_id,),
            ).rowcount > 0

    def expire_enrollments(self, pending_after: float = 90.0) -> int:
        """A request nobody picked up (service not running) must not block the next one forever."""
        with self._conn() as db:
            return db.execute(
                "UPDATE enrollments SET status = 'failed', message = 'Service caméra injoignable' "
                "WHERE status = 'pending' AND created_at < ?",
                (time.time() - pending_after,),
            ).rowcount

    # --- settings and retention --------------------------------------------------------------------------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._conn() as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as db:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                       "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))

    def retention_days(self) -> tuple[int, int]:
        """(access log, administration log) retention in days; 0 means keep without limit."""
        return (int(self.get_setting("retention_events_days", str(DEFAULT_EVENT_RETENTION_DAYS))),
                int(self.get_setting("retention_audit_days", str(DEFAULT_AUDIT_RETENTION_DAYS))))

    def set_retention_days(self, events_days: int, audit_days: int) -> None:
        for days in (events_days, audit_days):
            if days != 0 and not 7 <= days <= 3650:
                raise ValueError("retention must be 0 (no limit) or between 7 and 3650 days")
        self.set_setting("retention_events_days", str(events_days))
        self.set_setting("retention_audit_days", str(audit_days))

    def apply_retention(self) -> tuple[int, int]:
        """Delete what is older than the retention periods. Returns (events deleted, audit entries deleted)."""
        events_days, audit_days = self.retention_days()
        now = time.time()
        with self._conn() as db:
            events = db.execute("DELETE FROM events WHERE at < ?", (now - events_days * 86400,)).rowcount if events_days else 0
            audit = db.execute("DELETE FROM audit WHERE at < ?", (now - audit_days * 86400,)).rowcount if audit_days else 0
            db.execute("DELETE FROM enrollments WHERE created_at < ? AND status NOT IN ('pending', 'running')",
                       (now - 30 * 86400,))  # finished enrollment requests carry a name: keep them a month at most
        return events, audit

    def retention_overview(self) -> dict:
        with self._conn() as db:
            oldest_event = db.execute("SELECT MIN(at) FROM events").fetchone()[0]
            events = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            oldest_audit = db.execute("SELECT MIN(at) FROM audit").fetchone()[0]
            audit = db.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        return {"events": events, "oldest_event": oldest_event, "audit": audit, "oldest_audit": oldest_audit}

    def export_person(self, person_id: int) -> dict | None:
        """Everything held about one person (right of access), for a download. Embedding values are not included:
        only how many there are and which model made them; the numbers themselves are a security asset."""
        with self._conn() as db:
            person = db.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
            if person is None:
                return None
            name = person["name"]
            embeddings = db.execute("SELECT model, COUNT(*) AS n FROM embeddings WHERE person_id = ? GROUP BY model",
                                    (person_id,)).fetchall()
            rules = db.execute(
                "SELECT z.name AS zone, r.always, r.days, r.start, r.end FROM rules r JOIN zones z ON z.id = r.zone_id "
                "WHERE r.person_id = ? ORDER BY z.name", (person_id,)).fetchall()
            events = db.execute("SELECT at, decision, zone, reason FROM events WHERE lower(person) = lower(?) "
                                "ORDER BY at DESC LIMIT 100000", (name,)).fetchall()
            candidates = db.execute("SELECT at, admin, action, detail FROM audit WHERE instr(lower(detail), lower(?)) > 0 "
                                    "ORDER BY at DESC", (name,)).fetchall()
            pattern = name_pattern(name)
            audit = [a for a in candidates if pattern.search(a["detail"])][:1000]  # never another person's entries

        def iso(ts):
            return None if ts is None else datetime.fromtimestamp(ts).isoformat(timespec="seconds")

        return {
            "exported_at": iso(time.time()),
            "person": {"name": name, "active": bool(person["active"]), "registered_at": iso(person["created_at"]),
                       "consent_at": iso(person["consent_at"]), "consent_text": person["consent_text"]},
            "biometric_templates": [{"model": e["model"], "count": e["n"]} for e in embeddings],
            "biometric_templates_note": "Les valeurs numériques des empreintes ne sont pas communiquées.",
            "access_rules": [{"zone": r["zone"], "always": bool(r["always"]), "days": json.loads(r["days"]),
                              "from": r["start"], "to": r["end"]} for r in rules],
            "access_events": [{"at": iso(e["at"]), "decision": e["decision"], "zone": e["zone"], "reason": e["reason"]}
                              for e in events],
            "administration_log": [{"at": iso(a["at"]), "admin": a["admin"], "action": a["action"]} for a in audit],
        }

    # --- administration audit trail -------------------------------------------------------------

    def add_audit(self, admin: str, action: str, detail: str = "") -> None:
        with self._conn() as db:
            db.execute(
                "INSERT INTO audit (at, admin, action, detail) VALUES (?, ?, ?, ?)",
                (time.time(), admin, action, detail),
            )

    def list_audit(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._conn() as db:
            return db.execute("SELECT * FROM audit ORDER BY at DESC, id DESC LIMIT ?", (limit,)).fetchall()

    def stats(self, since: float) -> dict:
        """Counts used by the dashboard: totals, refusal reasons and a per-hour series."""
        with self._conn() as db:
            by_decision = {
                r["decision"]: r["n"]
                for r in db.execute(
                    "SELECT decision, COUNT(*) AS n FROM events WHERE at >= ? GROUP BY decision", (since,)
                )
            }
            by_reason = {
                r["reason"]: r["n"]
                for r in db.execute(
                    "SELECT reason, COUNT(*) AS n FROM events WHERE at >= ? AND decision = 'denied' "
                    "AND reason != '' GROUP BY reason ORDER BY n DESC",
                    (since,),
                )
            }
            times = [r["at"] for r in db.execute("SELECT at FROM events WHERE at >= ?", (since,))]
        hours: dict[str, int] = {}
        for t in times:
            key = datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:00")
            hours[key] = hours.get(key, 0) + 1
        return {
            "granted": by_decision.get("granted", 0),
            "denied": by_decision.get("denied", 0),
            "reasons": by_reason,
            "per_hour": dict(sorted(hours.items())),
        }

    def overview(self, period: str = "24h", now: float | None = None) -> dict:
        """Everything the dashboard draws, in one query: a time series, the previous period for comparison,
        the reasons of refusals and the number of refusals that look like an attack."""
        hours, bucket = PERIODS[period if period in PERIODS else "24h"]
        now = time.time() if now is None else now
        moment = datetime.fromtimestamp(now)
        if bucket >= 24:
            last = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            last = moment.replace(minute=0, second=0, microsecond=0)
            last = last.replace(hour=(last.hour // bucket) * bucket)  # local-time boundaries: 00h, 06h, 12h, 18h
        count = hours // bucket
        starts = [(last - timedelta(hours=bucket * (count - 1 - i))).timestamp() for i in range(count)]
        first, step = starts[0], bucket * 3600
        with self._conn() as db:
            rows = db.execute("SELECT at, decision, reason FROM events WHERE at >= ? AND at <= ?",
                              (first - count * step, now + 1)).fetchall()
        granted, denied = [0] * count, [0] * count
        previous = {"granted": 0, "denied": 0}
        reasons: dict[str, int] = {}
        alerts = 0
        for row in rows:
            if row["at"] < first:
                if row["decision"] in previous:
                    previous[row["decision"]] += 1
                continue
            i = min(count - 1, int((row["at"] - first) // step))
            if row["decision"] == "granted":
                granted[i] += 1
            elif row["decision"] == "denied":
                denied[i] += 1
                key = row["reason"] or "unknown"
                reasons[key] = reasons.get(key, 0) + 1
                if row["reason"].startswith(SPOOF_REASONS):
                    alerts += 1
        total_g, total_d = sum(granted), sum(denied)
        return {
            "period": period if period in PERIODS else "24h", "bucket_hours": bucket, "starts": starts,
            "granted": granted, "denied": denied,
            "totals": {"granted": total_g, "denied": total_d},
            "previous": previous,
            "reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
            "alerts": alerts,
            "generated_at": now,
        }

    def heatmap(self, days: int = 28, now: float | None = None) -> dict:
        """Accesses by weekday (Monday = 0) and hour of day over the last `days` days."""
        now = time.time() if now is None else now
        grid = [[0] * 24 for _ in range(7)]
        with self._conn() as db:
            times = [r["at"] for r in db.execute("SELECT at FROM events WHERE at >= ? AND at <= ?",
                                                 (now - days * 86400, now + 1))]
        for t in times:
            moment = datetime.fromtimestamp(t)
            grid[moment.weekday()][moment.hour] += 1
        return {"days": days, "grid": grid, "max": max((max(row) for row in grid), default=0), "total": len(times)}

    def purge_events_older_than(self, days: float) -> int:
        """Retention: delete events older than `days`. Returns how many were removed."""
        with self._conn() as db:
            return db.execute("DELETE FROM events WHERE at < ?", (time.time() - days * 86400,)).rowcount

