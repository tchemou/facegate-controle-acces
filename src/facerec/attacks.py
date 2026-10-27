"""Presentation-attack testing: log what happened per scenario, then report error rates honestly.

Vocabulary (ISO/IEC 30107-3):
- *bona fide* presentation: the real, enrolled person in front of the camera (scenario "reel");
- *attack* presentation: anything else (a photo, a video on a phone, an impostor...);
- APCER: share of attack presentations that were ACCEPTED (the security failure; lower is better);
- BPCER: share of bona fide presentations that were REJECTED or got no decision (the usability failure).

With few attempts a rate means little by itself: 0 successful attacks out of 20 is compatible with a true
rate of about 16 %. The report therefore always shows the 95 % Wilson interval next to each rate.
"""

import csv
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

BONA_FIDE = "reel"
SCENARIOS = {
    "reel": "Vous, en direct (utilisateur légitime)",
    "photo": "Photo de vous (téléphone ou papier)",
    "video": "Vidéo de vous sur un téléphone",
    "imposteur": "Une autre personne (avec son accord)",
    "masque": "Masque ou autre attaque",
}
KEYS = {"1": "reel", "2": "photo", "3": "video", "4": "imposteur", "5": "masque"}
GRANTED, DENIED, NO_DECISION = "granted", "denied", "no_decision"
MIN_FRAMES_FOR_AN_ATTEMPT = 8  # a face seen for fewer frames is a flicker, not a presentation
FIELDS = ["time", "scenario", "decision", "name", "reason", "earlier_denial"]
OLD_FIELDS = ["time", "scenario", "decision", "name", "reason"]  # files written before `earlier_denial` existed


class Presentation:
    """What happened during ONE presentation (one face, from its arrival to its departure).

    A presentation is judged on its outcome, not on every change of mind along the way: "refused as unknown, then
    recognised a moment later" is one presentation that ended in an acceptance (with one transient refusal), not two.
    Acceptance is sticky: once the system has let someone in, an attack has succeeded whatever came after."""

    def __init__(self, scenario: str):
        self.scenario = scenario  # what the operator announced when the face arrived
        self.final: tuple[str, str | None, str] | None = None  # (decision, name, reason)
        self.earlier_denial = ""

    def observe(self, decision: str, name: str | None, reason: str = "") -> None:
        if decision == GRANTED:
            if self.final and self.final[0] == DENIED and not self.earlier_denial:
                self.earlier_denial = self.final[2] or DENIED
            self.final = (GRANTED, name, "")
        elif decision == DENIED and not (self.final and self.final[0] == GRANTED):
            self.final = (DENIED, name, reason)

    @property
    def decided(self) -> bool:
        return self.final is not None


class ScenarioLog:
    """One CSV row per presentation, labelled with the scenario the operator announced."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _migrate(self) -> None:
        """A file started with an earlier version gets the new column (empty for its old rows)."""
        if not self.path.exists():
            return
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if lines and lines[0] == ",".join(OLD_FIELDS):
            rows = [",".join(FIELDS)] + [line + "," for line in lines[1:]]
            self.path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def write(self, scenario: str, decision: str, name: str | None, reason: str = "", earlier_denial: str = "") -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}: {sorted(SCENARIOS)}")
        new = not self.path.exists()
        with self.path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if new:
                writer.writerow(FIELDS)
            writer.writerow([datetime.now().isoformat(timespec="seconds"), scenario, decision, name or "", reason,
                             earlier_denial])

    def write_presentation(self, presentation: Presentation, min_decided_frames_ok: bool = True) -> None:
        """The outcome of a presentation that is over: its decision, or "no decision" if it never got one."""
        if presentation.decided:
            decision, name, reason = presentation.final
            self.write(presentation.scenario, decision, name, reason, presentation.earlier_denial)
        elif min_decided_frames_ok:
            self.write(presentation.scenario, NO_DECISION, None, NO_DECISION)


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95 % Wilson score interval for a proportion: sensible even for tiny n and for 0 or n successes."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True)
class ScenarioResult:
    scenario: str
    attempts: int
    granted: int
    denied: int
    no_decision: int
    reasons: dict  # why attempts were refused: reason -> count
    after_denial: int = 0  # accepted, but only after a transient refusal (friction for a real user, a near miss for an attack)

    @property
    def is_attack(self) -> bool:
        return self.scenario != BONA_FIDE

    @property
    def errors(self) -> int:
        """Attack: accepted. Bona fide: rejected or without a decision."""
        return self.granted if self.is_attack else self.denied + self.no_decision

    @property
    def rate(self) -> float:
        return self.errors / self.attempts if self.attempts else float("nan")

    @property
    def interval(self) -> tuple[float, float]:
        return wilson(self.errors, self.attempts)


def load(paths: list[Path | str]) -> list[dict]:
    rows = []
    for path in paths:
        with Path(path).open(newline="", encoding="utf-8") as f:
            rows += [r for r in csv.DictReader(f) if r.get("scenario") in SCENARIOS and r.get("decision")]
    return rows


def summarise(rows: list[dict]) -> list[ScenarioResult]:
    results = []
    for scenario in SCENARIOS:
        mine = [r for r in rows if r["scenario"] == scenario]
        if not mine:
            continue
        counts = Counter(r["decision"] for r in mine)
        reasons = Counter((r.get("reason") or "sans motif") for r in mine if r["decision"] != GRANTED)
        after_denial = sum(1 for r in mine if r["decision"] == GRANTED and (r.get("earlier_denial") or "").strip())
        results.append(ScenarioResult(scenario, len(mine), counts[GRANTED], counts[DENIED], counts[NO_DECISION],
                                      dict(reasons.most_common()), after_denial))
    return results


def percent(x: float) -> str:
    return "n/d" if x != x else f"{x * 100:.0f} %"


def report(results: list[ScenarioResult]) -> str:
    """The Markdown report. Verdict wording stays cautious: it states what the numbers allow, no more."""
    lines = [
        "# Tests d'attaques de présentation",
        "",
        "Mesures normalisées (ISO/IEC 30107-3). **APCER** : part des attaques *acceptées* (échec de sécurité). "
        "**BPCER** : part des utilisateurs légitimes *rejetés ou sans décision* (échec d'usage). "
        "Les intervalles sont des intervalles de Wilson à 95 %.",
        "",
        "| Scénario | Essais | Acceptés | Refusés | Sans décision | Taux d'erreur | Intervalle 95 % |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        low, high = r.interval
        kind = "APCER" if r.is_attack else "BPCER"
        lines.append(f"| {SCENARIOS[r.scenario]} | {r.attempts} | {r.granted} | {r.denied} | {r.no_decision} | "
                     f"{kind} {percent(r.rate)} | {percent(low)} – {percent(high)} |")

    attacks = [r for r in results if r.is_attack]
    if attacks:
        total, accepted = sum(r.attempts for r in attacks), sum(r.granted for r in attacks)
        low, high = wilson(accepted, total)
        lines += ["", f"**Toutes attaques confondues : {accepted} acceptée(s) sur {total}** "
                      f"(APCER {percent(accepted / total)}, intervalle 95 % : {percent(low)} – {percent(high)})."]
        for r in attacks:
            if r.granted == 0 and r.attempts:
                lines.append(f"- {SCENARIOS[r.scenario]} : aucune attaque acceptée sur {r.attempts}, ce qui est "
                             f"compatible avec un vrai taux allant jusqu'à **{percent(r.interval[1])}**.")
    for r in results:
        if r.after_denial and not r.is_attack:
            lines.append(f"- {SCENARIOS[r.scenario]} : {r.after_denial} des {r.granted} accès ont été précédés d'un refus "
                         f"transitoire (compté comme accepté, mais c'est une gêne pour l'utilisateur).")
        elif r.after_denial:
            lines.append(f"- {SCENARIOS[r.scenario]} : **{r.after_denial} attaque(s) acceptée(s) après un premier refus** "
                         f"(le système a d'abord refusé puis cédé).")
    lines += ["", "## Pourquoi les refus ?", ""]
    for r in results:
        if r.reasons:
            detail = ", ".join(f"{reason} ×{n}" for reason, n in r.reasons.items())
            lines.append(f"- {SCENARIOS[r.scenario]} : {detail}")
    lines += [
        "", "## Limites de cette mesure",
        "- Peu d'essais, une seule personne, un seul appareil, un seul éclairage : les intervalles ci-dessus sont larges "
        "pour cette raison. Ce n'est pas une certification.",
        "- Les attaques testées sont celles que vous avez jouées. Une attaque plus soignée (vidéo qui réagit au défi, "
        "masque, deepfake temps réel) peut réussir là où celles-ci ont échoué.",
        "- Un BPCER élevé veut dire que le système gêne les vrais utilisateurs : il faut le corriger autant que l'APCER.",
    ]
    return "\n".join(lines) + "\n"


def build_report(paths: list[Path | str]) -> str:
    rows = load(paths)
    if not rows:
        raise ValueError("aucun essai dans ces fichiers")
    return report(summarise(rows))
