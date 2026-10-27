"""Who may enter which zone, and when. Default deny: anything not explicitly allowed is refused.

Example ``policy.json``::

    {
      "zones": {
        "lab":    {"alice": "always",
                   "pagnol": [{"days": ["mon", "tue", "wed", "thu", "fri"], "from": "08:00", "to": "19:00"}]},
        "server": {"alice": [{"days": ["mon", "tue", "wed", "thu", "fri"], "from": "09:00", "to": "17:00"}]}
      }
    }

Recognition says *who* someone is; the policy says whether that person may open *this* door now.
A rule whose ``from`` is later than ``to`` spans midnight and is checked against the day it ends on.
"""

import json
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

OK, UNKNOWN_ZONE, NOT_AUTHORIZED, OUTSIDE_SCHEDULE = "ok", "unknown_zone", "not_authorized", "outside_schedule"


@dataclass(frozen=True)
class Rule:
    days: frozenset[int]
    start: time
    end: time

    def allows(self, now: datetime) -> bool:
        t = now.time()
        if self.start <= self.end:
            return now.weekday() in self.days and self.start <= t < self.end
        # overnight window: the early part belongs to the day it started on
        if t >= self.start:
            return now.weekday() in self.days
        return t < self.end and (now.weekday() - 1) % 7 in self.days


def policy_from_store(store) -> "AccessPolicy | None":
    """The rules saved by the web app, or None when no zone exists yet (nothing to enforce)."""
    data = store.policy_dict()
    return AccessPolicy.from_dict(data) if data["zones"] else None


def _clock(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def _rule(raw: dict) -> Rule:
    days = raw.get("days", DAYS)
    unknown = [d for d in days if d not in DAYS]
    if unknown:
        raise ValueError(f"unknown day(s) {unknown}: use {DAYS}")
    return Rule(frozenset(DAYS.index(d) for d in days), _clock(raw["from"]), _clock(raw["to"]))


class AccessPolicy:
    def __init__(self, zones: dict[str, dict[str, list[Rule] | None]]):
        self._zones = zones  # None means "always"

    @classmethod
    def from_dict(cls, data: dict) -> "AccessPolicy":
        zones: dict[str, dict[str, list[Rule] | None]] = {}
        for zone, people in data.get("zones", {}).items():
            zones[zone] = {
                name: None if rules == "always" else [_rule(r) for r in rules]
                for name, rules in people.items()
            }
        return cls(zones)

    @classmethod
    def load(cls, path: Path) -> "AccessPolicy":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def check(self, name: str | None, zone: str, now: datetime) -> tuple[bool, str]:
        people = self._zones.get(zone)
        if people is None:
            return False, UNKNOWN_ZONE
        if name is None or name not in people:
            return False, NOT_AUTHORIZED
        rules = people[name]
        if rules is None or any(rule.allows(now) for rule in rules):
            return True, OK
        return False, OUTSIDE_SCHEDULE
