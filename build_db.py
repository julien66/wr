#!/usr/bin/env python3
"""Calcule le classement et l'écrit dans une base SQLite (ranking.db) pour le site (app.py).

    python build_db.py --db civl_data.db --out ranking.db [--genders data/genders.csv]
"""
import argparse
import datetime as dt
import time
from pathlib import Path

from pgrank import engine, load, store
from pgrank.config import Config


def main():
    d = Config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="base SQLite des résultats CIVL")
    ap.add_argument("--out", default="ranking.db", help="base SQLite produite")
    ap.add_argument("--genders", default=str(Path(__file__).parent / "data" / "genders.csv"),
                    help="CSV civlid,gender (M/F) ou liste CIVL .xlsx ; sert au classement féminin")
    ap.add_argument("--genders-manual", default=str(Path(__file__).parent / "data" / "genders_manual.csv"),
                    help="corrections manuelles civlid,gender : prioritaires sur --genders")
    ap.add_argument("--k", type=float, default=d.elo_params["k"])
    ap.add_argument("--removals", default=str(Path(__file__).parent / "data" / "removals.csv"),
                    help="pilotes à anonymiser (demande de retrait) : un civlid par ligne")
    ap.add_argument("--boost", type=float, default=d.elo_params["boost"])
    ap.add_argument("--inactive-months", type=float, default=d.inactive_after_months)
    a = ap.parse_args()
    cfg = Config(engine="elo", inactive_after_months=a.inactive_months,
                 elo_params={"k": a.k, "boost": a.boost, "scale": d.elo_params["scale"]})
    t0 = time.time()
    genders = store.read_genders(a.genders) if a.genders and Path(a.genders).exists() else {}
    if a.genders_manual and Path(a.genders_manual).exists():
        manual = store.read_genders(a.genders_manual)
        genders.update(manual)
        print(f"{len(manual)} corrections manuelles de genre appliquées")
    comps, pilots, report = load.load(a.db)
    print(f"{len(comps)} compétitions, {len(pilots)} pilotes, {len(genders)} genres connus")
    removed = store.read_removals(a.removals) if a.removals and Path(a.removals).exists() else set()
    if removed:
        print(f"{len(removed)} pilote(s) anonymisé(s) à leur demande")
    w = store.Writer(a.out, genders, removed)
    out = engine.run(comps, pilots, cfg, sink=w.month)
    m = out.model
    params = dict(mu0=m.mu, sigma0=m.sigma, beta=m.beta, tau=m.tau, kappa=m.kappa, version="",
                  window=None, k=m.k, boost=m.boost, scale=m.scale)
    last = max(c.end for c in comps)
    w.finish(out, cfg, params, dt.date.today(), last)
    print(f"{a.out} écrit en {time.time() - t0:.0f} s ({Path(a.out).stat().st_size / 1e6:.0f} Mo), "
          f"précision {out.metrics['acc_exp_total']:.1%}")


if __name__ == "__main__":
    main()
