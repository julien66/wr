"""Écriture des résultats du moteur dans une base SQLite (consultée par app.py)."""
import csv
import json
import math
import sqlite3
import unicodedata
from pathlib import Path

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE pilots (civlid TEXT PRIMARY KEY, name TEXT, nation TEXT, gender TEXT, name_norm TEXT);
CREATE TABLE competitions (id INTEGER PRIMARY KEY, name TEXT, url TEXT, start TEXT, end TEXT, rated INTEGER, reason TEXT,
  n_pilots INTEGER, n_nations INTEGER, field_rating REAL, acc_all REAL, acc_exp REAL, n_pairs_exp INTEGER,
  n_idle_returns INTEGER, name_norm TEXT);
CREATE TABLE results (comp_id INTEGER, civlid TEXT, rank INTEGER, score REAL, prior INTEGER, idle REAL,
  mu_pre REAL, mu_post REAL, k REAL, won REAL, exp_rank INTEGER, exp_prob REAL, end TEXT);
CREATE TABLE months (key TEXT PRIMARY KEY, year INTEGER, month INTEGER, n_rated INTEGER, n_active INTEGER,
  n_rated_f INTEGER, n_active_f INTEGER, lo REAL, hi REAL, comp_ids TEXT);
CREATE TABLE monthly (month TEXT, rank INTEGER, rank_f INTEGER, civlid TEXT, rating REAL, n INTEGER, last TEXT,
  active INTEGER, idle REAL, move INTEGER, move_f INTEGER, rank_a INTEGER, rank_fa INTEGER);
"""
INDEXES = """
CREATE INDEX i_res_comp ON results(comp_id, rank);
CREATE INDEX i_res_pilot ON results(civlid, end);
CREATE INDEX i_mon_rank ON monthly(month, rank);
CREATE INDEX i_mon_rank_f ON monthly(month, rank_f);
CREATE INDEX i_mon_rank_a ON monthly(month, rank_a);
CREATE INDEX i_mon_rank_fa ON monthly(month, rank_fa);
CREATE INDEX i_mon_pilot ON monthly(civlid, month);
CREATE INDEX i_pil_norm ON pilots(name_norm);
CREATE INDEX i_comp_end ON competitions(end);
"""


def read_removals(path):
    """Fichier civlid[,note] : pilotes qui ont demandé le retrait de leurs données (lignes vides et # ignorées)."""
    out = set()
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            first = line.split(",")[0].strip()
            if first.isdigit():
                out.add(first)
    return out


REMOVED_NAME = "—"


def norm(s):
    """Minuscules sans accents, pour la recherche."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def read_genders(path):
    """CSV civlid,gender (M/F) -> dict. Accepte aussi la liste CIVL au format Excel (colonnes « CIVL ID » et « Gender »)."""
    path = Path(path)
    g = {}
    if path.suffix.lower() == ".xlsx":
        import openpyxl
        ws = openpyxl.load_workbook(path, read_only=True).active
        for r in ws.iter_rows(values_only=True):
            if r and isinstance(r[1], (int, float)) and r[3] in ("M", "F"):
                g[str(int(r[1]))] = r[3]
        return g
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["gender"].strip().upper() in ("M", "F"):
                g[str(row["civlid"]).strip()] = row["gender"].strip().upper()
    return g


class Writer:
    """Reçoit les classements mensuels un par un (engine.run(..., sink=w.month)) puis écrit le reste."""

    def __init__(self, path, genders, removed=()):
        path = Path(path)
        if path.exists():
            path.unlink()
        self.con = sqlite3.connect(path)
        self.con.executescript(SCHEMA)
        self.genders = genders
        self.removed = set(removed)
        self.prev = {}      # civlid -> (rang, rang femmes) officiels du mois précédent

    def month(self, mth):
        rows = mth.rows
        f_rank = 0
        a_rank = fa_rank = 0
        out, nf, nf_act = [], 0, 0
        new_prev = {}
        for r in rows:
            civ = r["civlid"]
            fem = self.genders.get(civ) == "F"
            rf = mf = None
            if fem:
                f_rank += 1
                rf = f_rank
                nf += 1
                nf_act += bool(r["active"])
            ra = rfa = None                        # rang officiel : parmi les seuls pilotes actifs
            if r["active"]:
                a_rank += 1
                ra = a_rank
                if fem:
                    fa_rank += 1
                    rfa = fa_rank
            pr = self.prev.get(civ)
            if rfa and pr and pr[1]:               # évolution du rang féminin (les inactifs n'en ont pas)
                mf = pr[1] - rfa
            new_prev[civ] = (ra, rfa)
            out.append((mth.key, r["rank"], rf, civ, r["rating"], r["n"], r["last"], int(r["active"]),
                        r["idle"], r["move"], mf, ra, rfa))
        self.prev = new_prev
        ratings = [r["rating"] for r in rows] or [0.0]
        hi = math.ceil(max(ratings) / 100) * 100
        lo = math.floor(min(ratings) / 100) * 100
        self.con.executemany("INSERT INTO monthly VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
        self.con.execute("INSERT INTO months VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (mth.key, mth.year, mth.month, mth.n_rated, mth.n_active, nf, nf_act, lo, hi,
                          json.dumps(mth.comp_ids)))

    def finish(self, out, cfg, params, build_date, last_result):
        con = self.con
        con.executemany("INSERT INTO pilots VALUES (?,?,?,?,?)",
                        [(c, REMOVED_NAME, "", None, "") if c in self.removed else (c, n, nat, self.genders.get(c), norm(n))
                         for c, (n, nat) in out.pilots.items()])
        for t in out.traces.values():
            c = t.comp
            con.execute("INSERT INTO competitions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (c.id, c.name, c.url, c.start.isoformat(), c.end.isoformat(), int(t.rated), t.reason,
                         t.n_pilots if t.rated else len(c.results), t.n_nations, None if t.field_rating != t.field_rating else t.field_rating,
                         None if t.acc_all != t.acc_all else t.acc_all, None if t.acc_exp != t.acc_exp else t.acc_exp,
                         t.n_pairs_exp, t.n_idle_returns, norm(c.name)))
            if t.rated:
                con.executemany("INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                [(c.id, r["civlid"], r["rank"], r["score"], r["prior"], r["idle"], r["mu_pre"], r["mu_post"],
                                  r["k"], r["won"], r["exp_rank"], r["exp_prob"], c.end.isoformat()) for r in t.rows])
            else:   # compétition hors calcul : résultats bruts
                con.executemany("INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                [(c.id, r.civlid, r.rank, r.score, 0, 0, None, None, None, None, None, None, c.end.isoformat())
                                 for r in c.results])
        meta = {"engine": cfg.engine, "z": cfg.z, "inactive_after_months": cfg.inactive_after_months,
                "site_title": cfg.site_title, "lang": cfg.lang, "pilot_min_comps": cfg.pilot_min_comps,
                "params": params, "metrics": {k: (v if not isinstance(v, dict) else {str(a): b for a, b in v.items()})
                                              for k, v in out.metrics.items()},
                "final_month": out.final_month, "build_date": build_date.isoformat(), "last_result": last_result.isoformat(),
                "full_cfg": {"elo_params": cfg.elo_params, "elo_idle_half_life": cfg.elo_idle_half_life}}
        for k, v in meta.items():
            con.execute("INSERT INTO meta VALUES (?,?)", (k, json.dumps(v, ensure_ascii=False)))
        con.executescript(INDEXES)
        # numéro 1 de chaque mois (parmi les pilotes actifs) : page « Numéros 1 »
        con.execute("CREATE TABLE leaders (month TEXT, sex TEXT, civlid TEXT, rating REAL)")
        con.execute("INSERT INTO leaders SELECT month, 'G', civlid, rating FROM monthly WHERE rank_a=1")
        con.execute("INSERT INTO leaders SELECT month, 'F', civlid, rating FROM monthly WHERE rank_fa=1")
        con.execute("CREATE INDEX i_lead ON leaders(sex, month)")
        con.commit()
        con.execute("VACUUM")
        con.close()
