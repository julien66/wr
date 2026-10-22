#!/usr/bin/env python3
"""Mise à jour du classement à partir de civlcomps.org, en une commande.

    python update.py              # importe les compétitions manquantes, le WPRS du mois, puis recalcule ranking.db
    python update.py --dry-run    # affiche seulement ce qui manque, n'écrit rien
    python update.py --no-build   # met à jour les données sans recalculer

Étapes :
1. Lit sur civlcomps la liste des compétitions du dernier classement WPRS publié (le 1er de chaque mois ;
   elle couvre les 3 dernières années).
2. Importe dans data/civl_data.db les compétitions absentes de la base, et réimporte celles dont civlcomps
   a mis à jour les résultats depuis le dernier passage (colonne « Results Updated »).
3. Télécharge le classement WPRS officiel du mois s'il manque dans data/wprs/ (page /wprs du site).
4. Recalcule ranking.db (build_db.py) : tout est recalculé depuis zéro.

Le site se met à jour quand data/ est poussé sur GitHub : Render recalcule la base au déploiement.
Une demande à la fois, avec une pause entre deux pages (--pause), pour ménager civlcomps.
Nécessite requests et openpyxl (pip install -r requirements-wprs.txt).
"""
import argparse
import datetime as dt
import html
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
import fetch_wprs  # noqa: E402

LIST = "https://civlcomps.org/ranking/paragliding-xc/competitions"
COMP = "https://civlcomps.org/ranking/paragliding-xc/competition?id={id}"
DATE = "%b %d, %Y"        # format des dates de data/civl_data.db (« Sep 05, 2026 »)

SYNC = """CREATE TABLE IF NOT EXISTS sync_state (
    competition_id INTEGER PRIMARY KEY,
    results_updated TEXT,
    checked_at TEXT
)"""


def _text(cell):
    """Texte d'une cellule HTML : balises retirées (« Nom<span>Sport</span> » -> « NomSport », comme la collecte d'origine)."""
    return html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()


def _date(s):
    return dt.datetime.strptime(" ".join(s.split()), "%b %d, %Y").date()


def parse_list(page):
    """Compétitions d'un classement : [{id, name, start, end, pilots, updated}]."""
    total = re.search(r'id="tableMain"[^>]*data-count="(\d+)"', page)
    out = []
    for row in re.findall(r'<tr data-key="\d+">(.*?)</tr>', page, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        m = re.search(r"competition\?id=(\d+)", cells[1]) if len(cells) > 1 else None
        if not m:
            continue
        start, end = (_date(_text(x)) for x in re.split(r"<br\s*/?>", cells[0]))
        out.append(dict(id=int(m.group(1)), name=_text(cells[1]), start=start, end=end,
                        pilots=int(_text(cells[8]) or 0), updated=_text(cells[-1])))
    if not total or int(total.group(1)) != len(out):
        raise RuntimeError(f"liste des compétitions incomplète : {len(out)} lues, {total and total.group(1)} annoncées")
    return out


def parse_competition(page):
    """Résultats d'une compétition : [(rang, nom, nation, civlid, score)]."""
    m = re.search(r'id="w0" class="ranking-pilots-list">(.*?)</table>', page, re.S)
    if not m:
        raise RuntimeError("tableau des résultats introuvable")
    head = [_text(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", m.group(1), re.S)]
    need = ["Rank", "Competition score", "Pilot", "Nation", "CIVL ID"]
    if any(h not in head for h in need):
        raise RuntimeError(f"colonnes inattendues : {head}")
    i = {h: head.index(h) for h in need}
    rows = []
    for tr in re.findall(r'<tr class="pilot-item"[^>]*>(.*?)</tr>', m.group(1), re.S):
        c = [_text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        rows.append((c[i["Rank"]], c[i["Pilot"]], c[i["Nation"]], c[i["CIVL ID"]], c[i["Competition score"]]))
    n = re.search(r'Number of pilots:</span>\s*<span class="count">(\d+)<', page)
    if not rows or (n and int(n.group(1)) != len(rows)):
        raise RuntimeError(f"{len(rows)} pilotes lus, {n and n.group(1)} annoncés (page incomplète ?)")
    return rows


def save_competition(con, c, rows):
    """Remplace la compétition et ses résultats dans civl_data.db (une seule transaction)."""
    with con:
        con.execute("DELETE FROM rankings WHERE competition_id=?", (c["id"],))
        con.execute("INSERT OR REPLACE INTO competitions (competition_id, name, url, start_date, end_date) VALUES (?,?,?,?,?)",
                    (c["id"], c["name"], COMP.format(id=c["id"]), c["start"].strftime(DATE), c["end"].strftime(DATE)))
        con.executemany("INSERT INTO rankings (competition_id, rank, pilot_name, nation, civlid, score) VALUES (?,?,?,?,?,?)",
                        [(c["id"], *r) for r in rows])
        con.execute("DELETE FROM invalid_ids WHERE competition_id=?", (c["id"],))
        con.execute("INSERT OR REPLACE INTO sync_state VALUES (?,?,?)", (c["id"], c["updated"], dt.date.today().isoformat()))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(ROOT / "data" / "civl_data.db"), help="base des résultats CIVL à compléter")
    ap.add_argument("--out", default=str(ROOT / "ranking.db"), help="base du site à recalculer")
    ap.add_argument("--pause", type=float, default=8.0, help="secondes entre deux pages civlcomps (ne pas descendre trop bas)")
    ap.add_argument("--dry-run", action="store_true", help="lister ce qui manque sans rien télécharger ni écrire")
    ap.add_argument("--no-wprs", action="store_true", help="ne pas télécharger le classement WPRS du mois")
    ap.add_argument("--no-build", action="store_true", help="ne pas recalculer ranking.db")
    ap.add_argument("--refetch", type=int, nargs="*", default=[], metavar="ID", help="réimporter aussi ces compétitions")
    a = ap.parse_args()

    s = fetch_wprs.session()
    rankings = sorted(fetch_wprs.ranking_list(s), key=lambda d: d["date"])
    last = rankings[-1]
    print(f"Dernier classement WPRS publié : {last['date']} (id {last['id']})")
    time.sleep(a.pause)
    listed = parse_list(s.get(LIST, params={"ranking_id": last["id"]}, timeout=120).text)
    print(f"{len(listed)} compétitions dans ce classement")

    con = sqlite3.connect(a.db)
    con.execute(SYNC)
    have = {r[0] for r in con.execute("SELECT competition_id FROM competitions")}
    seen = dict(con.execute("SELECT competition_id, results_updated FROM sync_state"))
    if not seen and not a.dry_run:
        # premier passage : la base existante est supposée à jour, on note seulement les dates civlcomps
        with con:
            con.executemany("INSERT INTO sync_state VALUES (?,?,?)",
                            [(c["id"], c["updated"], dt.date.today().isoformat()) for c in listed if c["id"] in have])
        seen = {c["id"]: c["updated"] for c in listed if c["id"] in have}

    new = [c for c in listed if c["id"] not in have]
    changed = [c for c in listed if c["id"] in have and c["id"] in seen and seen[c["id"]] != c["updated"]]
    forced = [c for c in listed if c["id"] in a.refetch and c not in new and c not in changed]
    todo = sorted(new + changed + forced, key=lambda c: c["end"])
    print(f"{len(new)} nouvelle(s), {len(changed)} mise(s) à jour sur civlcomps, {len(forced)} demandée(s)")
    for c in todo:
        tag = "nouvelle" if c in new else "mise à jour" if c in changed else "demandée"
        print(f"  {c['id']:>5}  {c['end']}  {c['pilots']:>4} pilotes  [{tag}]  {c['name']}")
    if a.dry_run:
        return

    failed = []
    for i, c in enumerate(todo, 1):
        time.sleep(a.pause)
        try:
            rows = parse_competition(s.get(COMP.format(id=c["id"]), timeout=120).text)
            save_competition(con, c, rows)
            print(f"[{i}/{len(todo)}] {c['id']} : {len(rows)} pilotes importés", flush=True)
        except Exception as e:  # réseau, page inattendue : on passe, la compétition sera reprise au prochain passage
            failed.append(c["id"])
            print(f"[{i}/{len(todo)}] {c['id']} : ÉCHEC ({e})", file=sys.stderr, flush=True)
    con.close()

    months = []
    if not a.no_wprs:
        first = min((p.name[:7] for p in fetch_wprs.OUT.glob("*.csv.gz")), default=last["date"][:7])
        months = fetch_wprs.fetch_missing(s, rankings, pause=20.0, since=first)

    if not a.no_build:
        subprocess.run([sys.executable, str(ROOT / "build_db.py"), "--db", a.db, "--out", a.out], check=True)

    print(f"\n{len(todo) - len(failed)} compétition(s) importée(s)" + (f", échecs : {failed} (relancer plus tard)" if failed else ""))
    if len(todo) > len(failed) or months:
        print("Pour mettre le site à jour : git add data && git commit -m \"Mise à jour des résultats\" && git push")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
