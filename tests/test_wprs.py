"""Contrôles de la comparaison WPRS : python tests/test_wprs.py"""
import sys, pathlib, datetime as dt, gzip, csv, io, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import compare_wprs as cw
from pgrank import engine
from pgrank.config import Config
from pgrank.load import Competition, Result

D = dt.date


def test_ranking_before():
    dates = [D(2026, 8, 1), D(2026, 9, 1), D(2026, 10, 1)]
    assert cw.ranking_before(dates, D(2026, 9, 1)) == D(2026, 9, 1)     # publié le jour même : utilisable
    assert cw.ranking_before(dates, D(2026, 9, 30)) == D(2026, 9, 1)
    assert cw.ranking_before(dates, D(2026, 7, 31)) is None


def test_load_and_compare():
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / "2026-09.csv.gz"
        with gzip.open(p, "wt") as f:
            f.write("rank,civlid,name,gender,nation,points\n1,a,A,M,FRA,300\n2,b,B,M,FRA,200\n")
        wprs = cw.load_wprs(pathlib.Path(tmp))
    assert wprs == {D(2026, 9, 1): {"a": 300.0, "b": 200.0}}
    # c n'a pas de points WPRS ; l'épreuve finit c, b, a : le WPRS (a > b > c) se trompe sur les 3 duels
    c = Competition(1, "C1", "", D(2026, 9, 10), D(2026, 9, 12))
    c.results = [Result(x, x, "FRA", r, 1000 - r, False) for x, r in (("c", 1), ("b", 2), ("a", 3))]
    out = engine.run([c], {x: (x, "FRA") for x in "abc"}, Config(min_field=2))
    rows = cw.compare(out.traces, wprs)
    assert len(rows) == 1 and rows[0]["wprs"] == "2026-09-01"
    r = rows[0]
    assert r["pairs_all"] == 3 and r["wprs_all"] == 0.0
    assert r["elo_all"] == 0.5                   # tous à 1500 avant l'épreuve : ex æquo de prévision
    assert r["pairs_exp"] == 0                   # personne n'a encore 3 épreuves
    assert abs(r["no_wprs"] - 1 / 3) < 1e-9


if __name__ == "__main__":
    test_ranking_before()
    test_load_and_compare()
    print("ok")
