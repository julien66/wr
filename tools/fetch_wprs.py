#!/usr/bin/env python3
"""Télécharge les classements WPRS mensuels officiels (parapente XC) publiés par la CIVL sur civlcomps.org.

    python tools/fetch_wprs.py                    # tous les mois manquants
    python tools/fetch_wprs.py --since 2022-01    # à partir d'un mois
    python tools/fetch_wprs.py --month 2026-09    # un seul mois (réécrit)

Chaque mois est enregistré dans data/wprs/AAAA-MM.csv.gz : classement mondial « Overall » complet
(rang, CIVL ID, nom, sexe, nation, points). Avec --full, on garde aussi les 4 résultats retenus
(place, points, nom de l'épreuve) : fichiers 2,5 fois plus gros, non versionnés par défaut.
La liste des mois et leur identifiant civlcomps est dans data/wprs/rankings.csv.
Le site produit l'export Excel de façon asynchrone : on le demande, on attend qu'il soit prêt, on le lit.
Nécessite requests et openpyxl (pip install requests openpyxl).
"""
import argparse
import csv
import gzip
import io
import json
import re
import sys
import time
from pathlib import Path

import requests

BASE = "https://civlcomps.org/ranking/paragliding-xc/pilots"
EXPORT = "https://civlcomps.org/ranking/export-new?rankingId={rid}&type=export_pilots_ranking&format=xlsx&async=1"
OUT = Path(__file__).resolve().parent.parent / "data" / "wprs"
COLUMNS = ["rank", "civlid", "name", "gender", "nation", "points",
           "r1", "p1", "e1", "r2", "p2", "e2", "r3", "p3", "e3", "r4", "p4", "e4"]
SLIM = 6   # colonnes gardées sans --full


def session():
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (classement-parapente; github.com/julien66/wr)"
    return s


def ranking_list(s):
    """[{id, date 'AAAA-MM-01', ...}] : liste des classements mensuels, lue dans le sélecteur de la page."""
    html = s.get(BASE, timeout=60).text
    m = re.search(r"const data = (\[.*?\]);", html, re.S)
    if not m:
        raise SystemExit("liste des classements introuvable sur la page civlcomps")
    return json.loads(m.group(1))


def export_xlsx(s, rid, tries=3):
    """Contenu de l'export Excel du classement mondial complet pour ranking_id = rid."""
    for attempt in range(tries):
        try:
            html = s.get(BASE, params={"ranking_id": rid}, timeout=60).text
            tok = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
            data = [("_csrf", tok), ("search[nation_id]", ""), ("profile_id", ""), ("ranking_id", rid),
                    ("search[continent_id]", ""), ("search[continent_id]", "0"),
                    ("search[scoringCategory]", ""), ("search[scoringCategory]", "0"), ("selectedItems", "")]
            url = EXPORT.format(rid=rid)
            hdr = {"X-CSRF-Token": tok, "X-Requested-With": "XMLHttpRequest"}
            j = s.post(url, data=data, headers=hdr, timeout=120).json()
            for _ in range(120):
                if j.get("status") == "completed":
                    return s.get(j["url"], timeout=120).content
                if j.get("status") in ("failed", "cancelled"):
                    raise RuntimeError(f"export {j.get('status')} : {j.get('error')}")
                time.sleep(5)
                j = s.get(url, params={"hash": j["hash"]}, headers=hdr, timeout=60).json()
            raise RuntimeError("export trop long")
        except Exception as e:  # réseau, page inattendue
            if attempt == tries - 1:
                raise
            print(f"  nouvel essai ({e})", file=sys.stderr)
            time.sleep(10 * (attempt + 1))


def parse_xlsx(content):
    """(date affichée, lignes) : lignes = listes dans l'ordre de COLUMNS."""
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(content), read_only=True).active
    rows, header, date = [], None, None
    for i, r in enumerate(ws.iter_rows(values_only=True)):
        if i == 1:
            date = r[0]
        if header is None:
            if r[0] == "Rank":
                header = [c for c in r if c is not None]
            continue
        if r[0] == "count":
            break
        if r[0] is None and r[1] is None:
            continue
        rows.append(["" if v is None else v for v in r[:len(COLUMNS)]] + [""] * (len(COLUMNS) - len(r[:len(COLUMNS)])))
    if header is None or header[:6] != ["Rank", "CIVL ID", "Name", "Gender", "Nation", "Points"]:
        raise RuntimeError(f"en-tête inattendu : {header}")
    return date, rows


def write_month(path, rows, full=False):
    n = len(COLUMNS) if full else SLIM
    rows = [r[:n] for r in rows]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS[:n])
    w.writerows(rows)
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as f:  # fichier stable (pas de date)
        f.write(buf.getvalue().encode("utf-8"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="premier mois AAAA-MM")
    ap.add_argument("--until", help="dernier mois AAAA-MM")
    ap.add_argument("--month", help="un seul mois AAAA-MM (réécrit)")
    ap.add_argument("--full", action="store_true", help="garder les 4 résultats retenus de chaque pilote")
    ap.add_argument("--pause", type=float, default=20.0, help="secondes entre deux mois : le site est fragile, ne pas descendre trop bas")
    a = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    s = session()
    lst = sorted(ranking_list(s), key=lambda d: d["date"])
    with open(OUT / "rankings.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["month", "date", "ranking_id"])
        for d in lst:
            w.writerow([d["date"][:7], d["date"], d["id"]])

    todo = []
    for d in lst:
        key = d["date"][:7]
        if a.month and key != a.month:
            continue
        if a.since and key < a.since or a.until and key > a.until:
            continue
        if not a.month and (OUT / f"{key}.csv.gz").exists():
            continue
        todo.append((key, d["id"]))
    todo.reverse()                      # du plus récent au plus ancien
    print(f"{len(lst)} classements publiés, {len(todo)} à télécharger")
    for i, (key, rid) in enumerate(todo, 1):
        t0 = time.time()
        date, rows = parse_xlsx(export_xlsx(s, rid))
        write_month(OUT / f"{key}.csv.gz", rows, a.full)
        print(f"[{i}/{len(todo)}] {key} (id {rid}, {date}) : {len(rows)} pilotes, {time.time() - t0:.0f} s", flush=True)
        time.sleep(a.pause)


if __name__ == "__main__":
    main()
