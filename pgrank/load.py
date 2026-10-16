"""Lecture et nettoyage de la base SQLite des résultats CIVL."""
import re
import sqlite3
import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field

SPORT_SUFFIX = re.compile(r"(?<=[a-zà-ÿ.])Sport$|\sSport$")


@dataclass
class Result:
    civlid: str
    name: str
    nation: str
    rank: int
    score: float | None
    sport_class: bool


@dataclass
class Competition:
    id: int
    name: str
    url: str
    start: dt.date
    end: dt.date
    results: list = field(default_factory=list)


def _date(s):
    return dt.datetime.strptime(s.strip(), "%b %d, %Y").date()


def clean_name(raw):
    """Retire le libellé de classe « Sport » collé au nom par l'extraction."""
    name = (raw or "").strip()
    cleaned = SPORT_SUFFIX.sub("", name).strip()
    return cleaned, cleaned != name


def load(db_path):
    """Retourne (compétitions triées chronologiquement, {civlid: (nom, nation)}, rapport qualité)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    comps = {}
    for cid, name, url, start, end in con.execute(
            "select competition_id, name, url, start_date, end_date from competitions"):
        comps[cid] = Competition(cid, (name or "").strip().strip('"').strip(), url, _date(start), _date(end))

    report = Counter()
    names, nations = defaultdict(Counter), defaultdict(Counter)
    for cid, rank, name, nation, civ, score in con.execute(
            "select competition_id, rank, pilot_name, nation, civlid, score from rankings order by id"):
        if cid not in comps:
            report["résultats sans compétition"] += 1
            continue
        civ = (civ or "").strip()
        if not civ:
            report["lignes sans civlid ignorées"] += 1
            continue
        clean, sport = clean_name(name)
        try:
            r = int(rank)
        except (TypeError, ValueError):
            report["rang illisible ignoré"] += 1
            continue
        try:
            sc = float(score) if score not in (None, "") else None
        except ValueError:
            sc = None
        comps[cid].results.append(Result(civ, clean, (nation or "").strip(), r, sc, sport))
        names[civ][clean] += 1
        nations[civ][(nation or "").strip()] += 1
        report["résultats lus"] += 1
    con.close()

    pilots = {civ: (names[civ].most_common(1)[0][0], nations[civ].most_common(1)[0][0]) for civ in names}
    ordered = sorted(comps.values(), key=lambda c: (c.end, c.start, c.id))
    return ordered, pilots, report
