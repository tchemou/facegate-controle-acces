"""Create a demo database with fictional people and a day of fake access events.

    python scripts/seed_demo.py                  # writes data/demo.db and data/demo_admin.txt

The admin password is generated and written to data/demo_admin.txt (git-ignored), never printed.
"""

import random
import secrets
import time
from pathlib import Path

import numpy as np

from facerec.store import Store
from facerec.web import security

PEOPLE = ["Camille Martin", "Karim Benali", "Sophie Bernard", "Lucas Moreau", "Inès Lefèvre", "Hugo Dubois"]
REASONS = [
    ("unknown_person", None, 5),
    ("policy:outside_schedule", "Lucas Moreau", 3),
    ("challenge_timeout:turn_left", "Hugo Dubois", 2),
    ("screen_detected", "Camille Martin", 1),
    ("policy:not_authorized", "Inès Lefèvre", 2),
]


def main(db_path: Path = Path("data/demo.db")) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    store, rng = Store(db_path), random.Random(7)

    password = f"Demo-{secrets.token_urlsafe(9)}7"
    store.create_admin("demo", security.hash_password(password))
    Path("data/demo_admin.txt").write_text(f"identifiant: demo\nmot de passe: {password}\n", encoding="utf-8")

    for name in PEOPLE:
        store.add_person(name, np.random.default_rng(rng.randrange(1 << 30)).normal(size=(20, 128)), "sface",
                         "donnée de démonstration")
    ids = {p["name"]: p["id"] for p in store.list_people()}
    lab, servers = store.add_zone("Laboratoire"), store.add_zone("Salle serveurs")
    weekdays = ["mon", "tue", "wed", "thu", "fri"]
    store.add_rule(lab, ids["Camille Martin"], always=True)
    store.add_rule(lab, ids["Karim Benali"], days=weekdays, start="08:00", end="19:00")
    store.add_rule(lab, ids["Sophie Bernard"], days=weekdays, start="09:00", end="17:00")
    store.add_rule(servers, ids["Karim Benali"], days=weekdays, start="09:00", end="12:00")
    store.add_rule(servers, ids["Karim Benali"], days=weekdays, start="14:00", end="17:00")
    store.add_rule(servers, ids["Lucas Moreau"], days=["mon", "wed"], start="22:00", end="06:00")
    store.set_person_active(ids["Hugo Dubois"], False)
    store.add_audit("demo", "zone_created", "Laboratoire")
    store.add_audit("demo", "person_deactivated", "Hugo Dubois")
    now = time.time()
    for _ in range(46):  # more traffic around 8-10h and 13-14h, like a real office
        hour = rng.choice([8, 8, 9, 9, 9, 10, 11, 13, 13, 14, 16, 17, 17, 18])
        at = now - rng.uniform(0, 23) * 3600
        local = time.localtime(at)
        at += (hour - local.tm_hour) * 3600 if rng.random() < 0.7 else 0
        at = min(at, now - 60)
        store.add_event("granted", rng.choice(PEOPLE), rng.choice(["Laboratoire", "Salle serveurs"]), "", at)
    for reason, person, count in REASONS:
        for _ in range(count):
            store.add_event("denied", person, rng.choice(["Laboratoire", "Salle serveurs"]), reason,
                            now - rng.uniform(60, 22 * 3600))
    print(f"Base de démonstration : {db_path} ({len(PEOPLE)} personnes, {len(store.list_events(500))} événements)")
    print("Identifiants écrits dans data/demo_admin.txt")


if __name__ == "__main__":
    main()
