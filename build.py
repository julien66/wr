#!/usr/bin/env python3
"""Construit le site : python build.py --db civl_data.db --out site"""
import argparse
import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pgrank import engine, load, render
from pgrank.config import Config


def _check_openskill():
    import re, openskill
    v = tuple(int(x) for x in re.findall(r"\d+", getattr(openskill, "__version__", "0"))[:3])
    if v < (6, 1, 1):
        raise SystemExit(f"openskill {openskill.__version__} est trop ancien (résultats faux avant 6.1.1) : "
                         "pip install -U 'openskill>=6.1.1'")



def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="base SQLite des résultats CIVL")
    ap.add_argument("--out", default="site", help="dossier de sortie (écrasé)")
    d = Config()
    ap.add_argument("--engine", choices=["elo", "openskill"], default=d.engine, help="elo (défaut) ou openskill")
    ap.add_argument("--k", type=float, default=d.elo_params["k"], help="Elo : amplitude des mises à jour (défaut 64)")
    ap.add_argument("--boost", type=float, default=d.elo_params["boost"], help="Elo : K multiplié par 1 + boost·e^(−n/10) pour les pilotes peu évalués (défaut 4)")
    ap.add_argument("--model", default=d.model, help="OpenSkill : BradleyTerryPart (défaut), PlackettLuce, ThurstoneMostellerPart...")
    ap.add_argument("--window", type=int, default=d.model_params.get("window_size"), help="OpenSkill BradleyTerryPart : voisins comparés de chaque côté (défaut 10)")
    ap.add_argument("--tau", type=float, default=d.model_params.get("tau"), help="OpenSkill : dérive de σ à chaque compétition (défaut 0,5)")
    ap.add_argument("--z", type=float, default=None, help="OpenSkill : rating = mu - z*sigma (défaut 2)")
    ap.add_argument("--full-months", type=int, default=d.full_months, help="nombre de derniers mois listant tous les pilotes (paginés)")
    ap.add_argument("--page-size", type=int, default=d.page_size, help="pilotes par page de classement")
    ap.add_argument("--top-n", type=int, default=d.top_n, help="pilotes affichés par classement mensuel")
    ap.add_argument("--inactive-months", type=float, default=d.inactive_after_months)
    ap.add_argument("--min-field", type=int, default=d.min_field)
    a = ap.parse_args()

    kw = dict(engine=a.engine, z=a.z, top_n=a.top_n, full_months=a.full_months, page_size=a.page_size, inactive_after_months=a.inactive_months, min_field=a.min_field)
    if a.engine == "elo":
        kw["elo_params"] = {"k": a.k, "boost": a.boost, "scale": d.elo_params["scale"]}
    else:
        _check_openskill()
        params = {"window_size": a.window, "tau": a.tau} if a.model.endswith("Part") else {}
        kw.update(model=a.model, model_params=params)
    cfg = Config(**kw)
    t0 = time.time()
    comps, pilots, report = load.load(a.db)
    print(f"{len(comps)} compétitions, {len(pilots)} pilotes, {report['résultats lus']} résultats"
          + (f" ({report['lignes sans civlid ignorées']} lignes sans civlid ignorées)" if report["lignes sans civlid ignorées"] else ""))
    out = engine.run(comps, pilots, cfg)
    print(f"{sum(t.rated for t in out.traces.values())} compétitions calculées, {len(out.months)} mois, "
          f"duels bien prédits : {out.metrics['acc_exp_total']:.1%}")
    render.render_site(out, cfg, a.out)
    print(f"Site écrit dans {a.out}/ en {time.time() - t0:.0f} s. Ouvrez {a.out}/index.html")


if __name__ == "__main__":
    main()
