#!/usr/bin/env python3
"""Compare la prévision de l'Elo du dépôt et celle du classement WPRS officiel, épreuve par épreuve.

    python tools/compare_wprs.py                                   # toute l'histoire couverte par data/wprs/
    python tools/compare_wprs.py --since 2023-12 --until 2026-09   # période de l'article
    python tools/compare_wprs.py --csv comparaison.csv             # détail par épreuve

Pour chaque épreuve calculée par l'Elo, on prend le dernier classement WPRS publié au plus tard le jour
du début de l'épreuve (classements du 1er de chaque mois, téléchargés par tools/fetch_wprs.py), et le
rating Elo de chaque pilote juste avant l'épreuve. Un duel est bien prédit si le pilote le mieux classé
finit devant (ex æquo de prévision = 0,5 ; ex æquo d'arrivée exclus), comme pairwise_accuracy.
Un pilote absent du WPRS a 0 point.

Trois mesures, sur exactement les mêmes duels pour les deux classements :
  - « ≥ 3 épreuves » : duels entre pilotes ayant déjà au moins 3 épreuves dans la base (mesure de l'article) ;
  - « tous » : tous les duels ;
  - « classés WPRS » : duels entre pilotes ayant tous deux des points WPRS et ≥ 3 épreuves
    (le WPRS n'est alors jamais pénalisé par des pilotes à 0 point) ;
  - « 30 meilleurs » : duels entre les 30 meilleurs Elo de l'épreuve parmi ceux qui ont ≥ 10 épreuves
    (même sélection que la mesure « elite » du moteur).
"""
import argparse
import bisect
import csv
import datetime as dt
import gzip
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pgrank import engine, load                    # noqa: E402
from pgrank.config import Config                   # noqa: E402
from pgrank.metrics import pairwise_accuracy       # noqa: E402

WPRS_DIR = ROOT / "data" / "wprs"


def load_wprs(folder=WPRS_DIR):
    """{date de publication: {civlid: points}} pour chaque fichier AAAA-MM.csv.gz."""
    out = {}
    for p in sorted(folder.glob("????-??.csv.gz")):
        y, m = map(int, p.name[:7].split("-"))
        with gzip.open(p, "rt", encoding="utf-8") as f:
            out[dt.date(y, m, 1)] = {r["civlid"]: float(r["points"]) for r in csv.DictReader(f) if r["civlid"]}
    return out


def ranking_before(dates, day):
    """Dernière date de publication <= day (dates triées), ou None."""
    i = bisect.bisect_right(dates, day)
    return dates[i - 1] if i else None


def compare(traces, wprs, since=None, until=None):
    """Liste de dicts, un par épreuve comparée."""
    dates = sorted(wprs)
    rows = []
    for t in traces.values():
        c = t.comp
        if not t.rated or len(t.rows) < 2:
            continue
        key = f"{c.start:%Y-%m}"
        if since and key < since or until and key > until:
            continue
        pub = ranking_before(dates, c.start)
        if pub is None or (c.start - pub).days > 45:     # pas de classement récent pour cette épreuve
            continue
        pts = wprs[pub]
        ranks = np.array([r["rank"] for r in t.rows], float)
        elo = np.array([r["mu_pre"] for r in t.rows])
        w = np.array([pts.get(r["civlid"], 0.0) for r in t.rows])
        exp = np.array([r["prior"] >= 3 for r in t.rows])
        both = exp & (w > 0)
        prior = np.array([r["prior"] for r in t.rows])
        vet = np.where(prior >= 10)[0]
        elite = np.zeros(len(ranks), bool)
        if len(vet) >= 4:
            elite[vet[np.argsort(-elo[vet])[:30]]] = True
        row = {"id": c.id, "name": c.name, "start": c.start.isoformat(), "wprs": pub.isoformat(),
               "pilots": len(ranks), "no_wprs": float((w == 0).mean())}
        for tag, mask in (("exp", exp), ("all", np.ones(len(ranks), bool)), ("ranked", both), ("elite", elite)):
            if mask.sum() >= 2:
                a_e, n = pairwise_accuracy(elo[mask], ranks[mask])
                a_w, _ = pairwise_accuracy(w[mask], ranks[mask])
            else:
                a_e = a_w = float("nan"); n = 0
            row.update({f"elo_{tag}": a_e, f"wprs_{tag}": a_w, f"pairs_{tag}": n})
        rows.append(row)
    rows.sort(key=lambda r: (r["start"], r["id"]))
    return rows


def summary(rows, tag):
    """(précision Elo, précision WPRS, duels, épreuves, épreuves où l'Elo fait mieux) pondérés par les duels."""
    sel = [r for r in rows if r[f"pairs_{tag}"]]
    n = sum(r[f"pairs_{tag}"] for r in sel)
    if not n:
        return float("nan"), float("nan"), 0, 0, 0
    e = sum(r[f"elo_{tag}"] * r[f"pairs_{tag}"] for r in sel) / n
    w = sum(r[f"wprs_{tag}"] * r[f"pairs_{tag}"] for r in sel) / n
    wins = sum(r[f"elo_{tag}"] > r[f"wprs_{tag}"] for r in sel)
    return e, w, n, len(sel), wins


def pct(x):
    return f"{x * 100:.1f} %".replace(".", ",")


def pt(x):
    return f"{x * 100:+.1f} pt".replace(".", ",")


def num(n):
    return f"{n:,}".replace(",", "\u202f")


def report(rows):
    lines = []
    if not rows:
        return "Aucune épreuve comparée (data/wprs/ vide ?)"
    lines.append(f"{len(rows)} épreuves du {rows[0]['start']} au {rows[-1]['start']}, "
                 f"{pct(np.mean([r['no_wprs'] for r in rows]))} de pilotes sans points WPRS en moyenne\n")
    lines.append("| Duels | Elo | WPRS | Écart | Duels comptés | Épreuves où l'Elo fait mieux |")
    lines.append("|---|---|---|---|---|---|")
    for tag, label in (("exp", "Pilotes ≥ 3 épreuves"), ("all", "Tous"), ("ranked", "Classés WPRS et ≥ 3 épreuves"),
                       ("elite", "30 meilleurs de l'épreuve (≥ 10 épreuves)")):
        e, w, n, k, wins = summary(rows, tag)
        lines.append(f"| {label} | {pct(e)} | {pct(w)} | {pt(e - w)} | {num(n)} | {wins} / {k} |")
    lines.append("\nPar année (pilotes ≥ 3 épreuves) :\n")
    lines.append("| Année | Épreuves | Duels | Elo | WPRS | Écart |")
    lines.append("|---|---|---|---|---|---|")
    by_year = defaultdict(list)
    for r in rows:
        by_year[r["start"][:4]].append(r)
    for y, rs in sorted(by_year.items()):
        e, w, n, k, _ = summary(rs, "exp")
        if n:
            lines.append(f"| {y} | {k} | {num(n)} | {pct(e)} | {pct(w)} | {pt(e - w)} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(ROOT / "data" / "civl_data.db"))
    ap.add_argument("--since", help="premier mois AAAA-MM (début de l'épreuve)")
    ap.add_argument("--until", help="dernier mois AAAA-MM")
    ap.add_argument("--csv", help="écrit le détail par épreuve dans ce fichier")
    a = ap.parse_args()

    wprs = load_wprs()
    if not wprs:
        raise SystemExit("aucun classement dans data/wprs/ : lancez d'abord python tools/fetch_wprs.py")
    comps, pilots, _ = load.load(a.db)
    out = engine.run(comps, pilots, Config())
    rows = compare(out.traces, wprs, a.since, a.until)
    print(report(rows))
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\nDétail par épreuve : {a.csv}")


if __name__ == "__main__":
    main()
