"""Contrôles d'intégration du moteur (nécessite openskill) : python tests/test_engine.py"""
import sys, pathlib, math, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import numpy as np
import openskill.models as osk_models
from pgrank import engine
from pgrank.config import Config
from pgrank.load import Competition, Result

D = dt.date
_CFG = Config(engine="openskill")
_BASE = getattr(osk_models, _CFG.model)
MODEL = lambda: _BASE(**_CFG.model_params)


def comp(cid, start, end, results):
    c = Competition(cid, f"C{cid}", "", start, end)
    c.results = [Result(civ, civ, "FRA", rank, score, False) for civ, rank, score in results]
    return c


def field(n, prefix="p"):
    return [(f"{prefix}{i}", i + 1, 1000 - i * 10) for i in range(n)]


def run(comps, **kw):
    cfg = Config(**{"engine": "openskill", **kw})
    pilots = {r.civlid: (r.name, r.nation) for c in comps for r in c.results}
    return engine.run(comps, pilots, cfg), cfg


def check_inactivity_inflation():
    c1 = comp(1, D(2019, 1, 1), D(2019, 1, 5), field(8))
    c2 = comp(2, D(2023, 5, 1), D(2023, 5, 5), field(8))                 # ~52 mois plus tard
    out, cfg = run([c1, c2])
    r1 = {r["civlid"]: r for r in out.traces[1].rows}
    r2 = {r["civlid"]: r for r in out.traces[2].rows}
    for civ in ("p0", "p3", "p7"):
        assert r2[civ]["mu_pre"] == r1[civ]["mu_post"], "mu doit être conservé pendant l'absence"
        idle = (D(2023, 5, 1) - D(2019, 1, 5)).days / engine.DAYS_PER_MONTH
        want = min(MODEL().sigma, math.sqrt(r1[civ]["sigma_post"] ** 2 + cfg.idle_tau ** 2 * (idle - cfg.inactive_after_months)))
        assert abs(r2[civ]["sigma_pre"] - want) < 1e-9, (civ, r2[civ]["sigma_pre"], want)
        assert r2[civ]["sigma_pre"] > r1[civ]["sigma_post"]
    print("ok  absence longue : mu conservé, sigma relevé selon la formule")


def check_no_inflation_when_active():
    c1 = comp(1, D(2020, 1, 1), D(2020, 1, 5), field(8))
    c2 = comp(2, D(2021, 1, 1), D(2021, 1, 5), field(8))                 # 12 mois : sous le seuil
    out, _ = run([c1, c2])
    for r in out.traces[2].rows:
        assert r["sigma_pre"] == r["sigma_raw"]
    print("ok  pas de gonflement sous le seuil d'inactivité")


def check_monthly_status():
    c1 = comp(1, D(2020, 1, 1), D(2020, 1, 5), field(8))
    c2 = comp(2, D(2022, 3, 1), D(2022, 3, 5), field(5, "q"))
    out, _ = run([c1, c2])
    m = {x.key: x for x in out.months}
    assert all(r["active"] for r in m["2020-01"].rows)
    assert all(r["active"] for r in m["2021-06"].rows)                    # fin juin 2021 : 17,8 mois depuis le 5 janv. 2020
    assert not any(r["active"] for r in m["2021-07"].rows)                # fin juillet 2021 : 18,8 mois, seuil dépassé
    assert m["2022-03"].n_active == 5 and m["2022-03"].n_rated == 13
    p0 = {r["civlid"]: r for r in m["2020-01"].rows}["p0"]; p0b = {r["civlid"]: r for r in m["2022-03"].rows}["p0"]
    assert p0["mu"] == p0b["mu"] and p0["sigma"] == p0b["sigma"], "niveau figé pendant l'inactivité"
    print("ok  statut actif/inactif par mois ; niveau et sigma affichés figés tant qu'il n'y a pas de compétition")


def check_small_field_and_ties():
    small = comp(1, D(2020, 1, 1), D(2020, 1, 5), field(3))
    tied = comp(2, D(2020, 2, 1), D(2020, 2, 5),
                [("a", 1, 900), ("b", 2, 800), ("c", 2, 800), ("d", 4, 700), ("e", 5, 0), ("f", 5, 0), ("g", 5, 0)])
    out, _ = run([small, tied])
    assert not out.traces[1].rated
    rows = {r["civlid"]: r for r in out.traces[2].rows}
    assert rows["b"]["rank"] == rows["c"]["rank"] == 2, "ex æquo => même rang"
    assert rows["e"]["rank"] == rows["f"]["rank"] == rows["g"]["rank"] == 5
    assert rows["e"]["mu_post"] < rows["d"]["mu_post"]
    # avec le modèle complet, des ex æquo reçoivent exactement la même mise à jour
    out_pl, _ = run([small, tied], model="PlackettLuce", model_params={})
    rpl = {r["civlid"]: r for r in out_pl.traces[2].rows}
    assert rpl["b"]["mu_post"] == rpl["c"]["mu_post"]
    assert rpl["e"]["mu_post"] == rpl["g"]["mu_post"]
    print("ok  plateau < 5 ignoré ; ex æquo et pilotes à 0 point traités à égalité")


def check_matches_direct_openskill_calls():
    """Le moteur doit donner exactement ce que donnent des appels directs à la bibliothèque."""
    model = MODEL()
    c1 = comp(1, D(2020, 1, 1), D(2020, 1, 5), field(12))
    c2 = comp(2, D(2020, 3, 1), D(2020, 3, 5), [(f"p{i}", 12 - i, 100 - i) for i in range(12)])   # ordre inversé
    out, _ = run([c1, c2])
    teams = [[model.rating(name=f"p{i}")] for i in range(12)]
    teams = model.rate(teams, ranks=[float(i + 1) for i in range(12)])
    teams = model.rate(teams, ranks=[float(12 - i) for i in range(12)])
    got = {r["civlid"]: r for r in out.traces[2].rows}
    for i, t in enumerate(teams):
        assert abs(got[f"p{i}"]["mu_post"] - t[0].mu) < 1e-9 and abs(got[f"p{i}"]["sigma_post"] - t[0].sigma) < 1e-9
    snap = {r["civlid"]: r for r in out.months[-1].rows}
    for i, t in enumerate(teams):
        assert abs(snap[f"p{i}"]["rating"] - t[0].ordinal(z=_CFG.z)) < 1e-9, "rating affiché = ordinal d'OpenSkill"
    print("ok  moteur == appels directs à openskill (rate, ordinal) sur deux compétitions enchaînées")


def check_deterministic():
    comps = [comp(i, D(2020, 1 + i % 12, 1), D(2020, 1 + i % 12, 5), field(10 + i)) for i in range(1, 6)]
    a, _ = run(comps); b, _ = run(comps)
    assert [r["rating"] for r in a.months[-1].rows] == [r["rating"] for r in b.months[-1].rows]
    print("ok  résultat déterministe")



def run_elo(comps, **kw):
    cfg = Config(engine="elo", **kw)
    pilots = {r.civlid: (r.name, r.nation) for c in comps for r in c.results}
    return engine.run(comps, pilots, cfg), cfg


def check_elo_formula():
    """Un Elo tous contre tous recalculé à la main doit donner exactement le même résultat que le moteur."""
    c1 = comp(1, D(2020, 1, 1), D(2020, 1, 5), field(8))
    c2 = comp(2, D(2020, 3, 1), D(2020, 3, 5), [(f"p{i}", 8 - i, 100 - i) for i in range(8)])
    out, cfg = run_elo([c1, c2])
    k0, boost, scale = (cfg.elo_params[x] for x in ("k", "boost", "scale"))
    R = {f"p{i}": 1500.0 for i in range(8)}; n = {k: 0 for k in R}
    for ranks in ({f"p{i}": i + 1 for i in range(8)}, {f"p{i}": 8 - i for i in range(8)}):
        new = {}
        for a in R:
            s = sum(1.0 if ranks[a] < ranks[b] else 0.5 if ranks[a] == ranks[b] else 0.0 for b in R if b != a)
            e = sum(1 / (1 + 10 ** ((R[b] - R[a]) / scale)) for b in R if b != a)
            new[a] = R[a] + k0 * (1 + boost * math.exp(-n[a] / 10)) * (s - e) / (len(R) - 1)
        R = new; n = {k: v + 1 for k, v in n.items()}
    snap = {r["civlid"]: r for r in out.months[-1].rows}
    for a in R:
        assert abs(snap[a]["rating"] - R[a]) < 1e-9, (a, snap[a]["rating"], R[a])
    print("ok  Elo : le moteur égale un calcul à la main (tous contre tous, K provisoire)")


def check_elo_strength_of_opponents():
    """Battre des pilotes très inférieurs rapporte presque rien ; en perdre contre eux coûte cher."""
    base = comp(1, D(2020, 1, 1), D(2020, 1, 5), field(12))
    # puis 6 compétitions où p0 (premier) bat tout le monde : de plus en plus attendu
    cs = [base] + [comp(10 + j, D(2020, 2 + j, 1), D(2020, 2 + j, 5), field(12)) for j in range(6)]
    out, _ = run_elo(cs)
    gains = [next(r for r in out.traces[c.id].rows if r["civlid"] == "p0") for c in cs]
    deltas = [g["mu_post"] - g["mu_pre"] for g in gains]
    assert all(b < a for a, b in zip(deltas, deltas[1:])), deltas   # gains décroissants
    # p11 (dernier) bat soudain tout le monde : très gros gain
    up = comp(99, D(2021, 1, 1), D(2021, 1, 5), [(f"p{i}", 12 - i, 100 - i) for i in range(12)])
    out2, _ = run_elo(cs + [up])
    r = next(r for r in out2.traces[99].rows if r["civlid"] == "p11")
    assert r["mu_post"] - r["mu_pre"] > 5 * deltas[-1]
    print("ok  Elo : gains décroissants face à des adversaires battus d'avance, gros gain si surprise")


def check_elo_idle_raises_k():
    c1 = comp(1, D(2015, 1, 1), D(2015, 1, 5), field(10))
    cs = [c1] + [comp(10 + j, D(2015, 2 + j, 1), D(2015, 2 + j, 5), field(10)) for j in range(10)]
    late = comp(50, D(2023, 1, 1), D(2023, 1, 5), field(10))
    out, cfg = run_elo(cs + [late])
    before = next(r for r in out.traces[cs[-1].id].rows if r["civlid"] == "p0")["k"]
    after = next(r for r in out.traces[50].rows if r["civlid"] == "p0")["k"]
    assert after > before, (before, after)
    print("ok  Elo : K relevé après une longue absence")

def check_excluded_competition():
    c1 = comp(1, D(2020, 1, 1), D(2020, 1, 5), field(10))
    c2 = comp(2, D(2020, 2, 1), D(2020, 2, 5), [(f"p{i}", 10 - i, 100 - i) for i in range(10)])
    out, _ = run([c1, c2], excluded={2: "test"})
    assert not out.traces[2].rated and out.traces[2].reason == "test"
    ref, _ = run([c1])
    assert {r["civlid"]: r["rating"] for r in out.months[-1].rows} == {r["civlid"]: r["rating"] for r in ref.months[-1].rows}
    print("ok  compétition exclue : n'entre pas dans le calcul, affichée avec sa raison")


if __name__ == "__main__":
    for f in (check_inactivity_inflation, check_no_inflation_when_active, check_monthly_status,
              check_small_field_and_ties, check_matches_direct_openskill_calls, check_deterministic,
              check_elo_formula, check_elo_strength_of_opponents, check_elo_idle_raises_k, check_excluded_competition):
        f()
    print("Tous les contrôles passent.")
