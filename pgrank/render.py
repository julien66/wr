"""Génération du site statique (Jinja2) à partir des résultats du moteur."""
import csv
import json
import re
import datetime as dt
import math
import shutil
from collections import defaultdict
from pathlib import Path

import numpy as np
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .engine import _month_end
from .i18n import Locale

HERE = Path(__file__).parent
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]
ABBR = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
NBSP = " "


def f1(x):
    return f"{x:.1f}".replace(".", ",")


def f2(x):
    return f"{x:.2f}".replace(".", ",")


def fint(n):
    return f"{int(n):,}".replace(",", NBSP)


def fdelta(x):
    s = f"{abs(x):.1f}".replace(".", ",")
    return ("+" if x > 0.049 else "−" if x < -0.049 else "") + s


def date_fr(d):
    return f"{d.day} {ABBR[d.month - 1]} {d.year}"


def span_fr(a, b):
    if a == b:
        return date_fr(a)
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{b.day} {ABBR[a.month - 1]} {a.year}"
    return f"{date_fr(a)} – {date_fr(b)}"


def month_label(y, m):
    return f"{MONTHS[m - 1].capitalize()} {y}"


def iso_my(iso):
    y, m, _ = iso.split("-")
    return f"{MONTHS[int(m) - 1]} {y}"


def _pct(v, lo, hi):
    return round(max(0.0, min(100.0, (v - lo) / (hi - lo) * 100)), 1) if hi > lo else 0.0


def build_picker(months, prefix, current, qs="", loc=None):
    loc = loc or Locale("fr")
    """Sélecteur de mois : une ligne par année, douze cases (liens vers chaque classement)."""
    have = {m.key for m in months}
    years = sorted({m.year for m in months}, reverse=True)
    out = []
    for y in years:
        cells = []
        for mo in range(1, 13):
            k = f"{y:04d}-{mo:02d}"
            if k in have:
                cur = ' aria-current="page"' if k == current else ""
                cells.append(f'<a href="{prefix}{k}.html{qs}"{cur}>{loc.abbr(mo).rstrip(".")}</a>')
            else:
                cells.append("<span></span>")
        out.append(f'<div class="yr"><b>{y}</b>{"".join(cells)}</div>')
    return "".join(out)


def _slim(html):
    """Retire les indentations et lignes vides (gain de ~15 % sur le poids des pages)."""
    return re.sub(r"\n\s*", "\n", html).strip() + "\n"


def build_pager(key, pg, n_pages, prefix, qs="", loc=None):
    loc = loc or Locale("fr")
    """Liens de pagination : 1 … p-2 p-1 p p+1 p+2 … N."""
    if n_pages <= 1:
        return ""
    href = lambda p: (f"{prefix}{key}.html" if p == 1 else f"{prefix}{key}-p{p}.html") + qs
    nums = sorted({1, n_pages, *range(max(1, pg - 2), min(n_pages, pg + 2) + 1)})
    out, prev = [], 0
    if pg > 1:
        out.append(f'<a href="{href(pg - 1)}" rel="prev">‹ {loc.gettext("Précédente")}</a>')
    for p in nums:
        if p - prev > 1:
            out.append('<span class="gap">…</span>')
        out.append(f'<a href="{href(p)}" aria-current="page">{p}</a>' if p == pg else f'<a href="{href(p)}">{p}</a>')
        prev = p
    if pg < n_pages:
        out.append(f'<a href="{href(pg + 1)}" rel="next">{loc.gettext("Suivante")} ›</a>')
    return '<nav class="pager" aria-label="' + loc.gettext("Pages du classement") + '">' + "".join(out) + "</nav>"


def render_site(out, cfg, out_dir, build_date=None):
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    for sub in ("ranking", "competition", "pilot", "static", "exports"):
        (out_dir / sub).mkdir(parents=True)
    shutil.copy(HERE / "static" / "style.css", out_dir / "static" / "style.css")
    shutil.copy(HERE / "static" / "app.js", out_dir / "static" / "app.js")

    env = Environment(loader=FileSystemLoader(HERE / "templates"),
                      autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    fr = Locale("fr")
    env.globals["mlc"] = lambda s: s.lower()
    env.filters.update(f1=f1, f2=f2, fint=fint, fdelta=fdelta, date_fr=date_fr, iso_my=iso_my, pct=fr.pct, pct1=fr.pct1)
    env.globals.update(elo=(cfg.engine == "elo"), cfg=cfg, span_fr=span_fr, month_label=month_label,
                       _=fr.gettext, _h=fr.html, _n=fr.ngettext, lang="fr")

    months = out.months
    traces = out.traces
    all_comps = sorted(traces.values(), key=lambda t: (t.comp.end, t.comp.id), reverse=True)
    last_result = max(t.comp.end for t in all_comps)
    build_date = build_date or dt.date.today()
    latest = months[-1]
    # ---------- historique par pilote (une entrée par compétition évaluée) ----------
    hist = defaultdict(list)
    for t in sorted((t for t in all_comps if t.rated), key=lambda t: (t.comp.end, t.comp.id)):
        for r in t.rows:
            hist[r["civlid"]].append((t, r))
    in_ranking = {r["civlid"] for mth in months for r in mth.rows}
    pages = {c for c, h in hist.items() if len(h) >= cfg.pilot_min_comps} | (in_ranking & set(hist))
    m = out.model
    elo = cfg.engine == "elo"
    try:
        import openskill
        osk_version = getattr(openskill, "__version__", "") if not elo else ""
    except ImportError:
        osk_version = ""
    params = dict(mu0=m.mu, sigma0=m.sigma, beta=m.beta, tau=m.tau, kappa=m.kappa, version=osk_version,
                  window=getattr(m, "window_size", None), k=getattr(m, "k", None), boost=getattr(m, "boost", None),
                  scale=getattr(m, "scale", None))
    common = dict(pages=pages, site_title=cfg.site_title, latest_key=latest.key, last_result=last_result,
                  build_date=build_date, params=params)

    # ---------- classements mensuels ----------
    def comp_link_rows(ids):
        return [traces[i] for i in ids]

    tpl = env.get_template("ranking.html")
    for idx, mth in enumerate(months):
        rows = mth.rows
        hi = max((r["mu"] + (r["sd"] if elo else cfg.z * r["sigma"]) for r in rows), default=1.0)
        step = 100 if elo else 10
        hi = math.ceil(hi / step) * step
        lo = (math.floor(min((r["rating"] for r in rows), default=0.0) / step) * step) if elo else 0
        for r in rows:
            if elo:     # barre : rating ± 1 écart type
                r["a"] = _pct(r["rating"] - r["sd"], lo, hi); r["b"] = _pct(r["rating"], lo, hi); r["c"] = _pct(r["rating"] + r["sd"], lo, hi)
            else:
                r["a"] = _pct(r["rating"], lo, hi); r["b"] = _pct(r["mu"], lo, hi)
                r["c"] = _pct(r["mu"] + cfg.z * r["sigma"], lo, hi)
        prev_m = months[idx - 1] if idx else None
        next_m = months[idx + 1] if idx + 1 < len(months) else None
        ps = max(1, cfg.page_size)
        n_pages = max(1, math.ceil(len(rows) / ps))
        for pg in range(1, n_pages + 1):
            part = rows[(pg - 1) * ps: pg * ps]
            name = f"{mth.key}.html" if pg == 1 else f"{mth.key}-p{pg}.html"
            for target, root, prefix in ((f"ranking/{name}", "../", ""),
                                         *([("index.html" if pg == 1 else f"index-p{pg}.html", "", "ranking/")] if mth is latest else [])):
                html = tpl.render(
                    **common, root=root, page="ranking", month=mth, rows=part, scale_hi=hi,
                    prev_m=prev_m, next_m=next_m, prefix=prefix,
                    picker=build_picker(months, prefix, mth.key),
                    pager=build_pager(mth.key, pg, n_pages, prefix, ""),
                    pg=pg, n_pages=n_pages, first=(pg - 1) * ps + 1, last=(pg - 1) * ps + len(part),
                    n_shown=len(rows),
                    comps=comp_link_rows(mth.comp_ids), title=month_label(mth.year, mth.month) + (f" (page {pg})" if pg > 1 else ""))
                (out_dir / target).write_text(_slim(html), encoding="utf-8")

    # ---------- pages de compétition ----------
    tpl = env.get_template("competition.html")
    for t in all_comps:
        mkey = f"{t.comp.end.year:04d}-{t.comp.end.month:02d}"
        rows = t.rows
        if t.rated:
            vals = [r["mu_pre"] - cfg.z * r["sigma_pre"] for r in rows] + \
                   [r["mu_post"] - cfg.z * r["sigma_post"] for r in rows]
            lo, hi = min(vals), max(vals)
            for r in rows:
                r["rt_pre"] = r["mu_pre"] - cfg.z * r["sigma_pre"]
                r["rt_post"] = r["mu_post"] - cfg.z * r["sigma_post"]
                r["d"] = r["rt_post"] - r["rt_pre"]
                r["a"] = _pct(r["rt_pre"], lo, hi); r["b"] = _pct(r["rt_post"], lo, hi)
                r["inflated"] = (r["idle"] > cfg.inactive_after_months) if elo else (r["sigma_pre"] > r["sigma_raw"] + 1e-9)
        html = tpl.render(**common, root="../", page="competition", t=t, c=t.comp, rows=rows,
                          month_key=mkey, month_name=month_label(t.comp.end.year, t.comp.end.month),
                          title=t.comp.name)
        (out_dir / "competition" / f"{t.comp.id}.html").write_text(_slim(html), encoding="utf-8")

    # ---------- pages pilote ----------
    tpl = env.get_template("pilot.html")
    z = cfg.z
    n_world = sum(f["active"] for f in out.final.values())
    for civ in pages:
        h = hist[civ]
        name, nation = out.pilots[civ]
        pts, table = [], []
        for t, r in h:
            rt_pre = r["mu_pre"] - z * r["sigma_pre"]; rt_post = r["mu_post"] - z * r["sigma_post"]
            pts.append({"d": t.comp.end.isoformat(), "id": t.comp.id, "n": t.comp.name, "k": r["rank"], "N": t.n_pilots,
                        "m0": round(r["mu_pre"], 3), "s0": round(r["sigma_pre"], 3),
                        "m1": round(r["mu_post"], 3), "s1": round(r["sigma_post"], 3),
                        "x": r["exp_rank"], "w": round(r["won"], 4), "e": round(r["exp_prob"], 4)})
            table.append({"t": t, "r": r, "rt_pre": rt_pre, "rt_post": rt_post, "d": rt_post - rt_pre,
                          "pct": 100.0 * r["rank"] / t.n_pilots})
        ranks = [r["rank"] for _, r in h]
        st = dict(n=len(h), wins=sum(k == 1 for k in ranks), podiums=sum(k <= 3 for k in ranks),
                  top10=sum(k <= 10 for k in ranks),
                  median_pct=float(np.median([x["pct"] for x in table])), best=min(ranks))
        fin = out.final.get(civ)
        if fin:     # rang officiel : parmi les actifs (aucun pour un inactif)
            fin = dict(fin, rank=fin["rank_a"])
        data = {"elo": elo, "z": z, "inactive": cfg.inactive_after_months, "last": fin["last"] if fin else None, "active": fin["active"] if fin else None,
                "mu": fin["mu"] if fin else None, "sigma": fin["sigma"] if fin else None,
                "now": _month_end(*map(int, out.final_month.split("-"))).isoformat(), "pts": pts}
        js = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        html = tpl.render(**common, root="../", page="pilot", title=name, name=name, nation=nation, civ=civ,
                          fin=fin, st=st, table=list(reversed(table)), js=js,
                          n_world=n_world)
        (out_dir / "pilot" / f"{civ}.html").write_text(_slim(html), encoding="utf-8")

    # ---------- liste des compétitions ----------
    by_year = defaultdict(list)
    for t in all_comps:
        by_year[t.comp.end.year].append(t)
    (out_dir / "competitions.html").write_text(
        env.get_template("competitions.html").render(
            **common, root="", page="competitions", years=sorted(by_year.items(), reverse=True),
            n_comps=len(all_comps), title="Compétitions"), encoding="utf-8")

    # ---------- méthode ----------
    (out_dir / "method.html").write_text(
        env.get_template("method.html").render(
            **common, root="", page="method", metrics=out.metrics, title="Méthode",
            n_rated=sum(t.rated for t in all_comps), n_comps=len(all_comps),
            n_pilots=len(out.pilots), first=months[0], n_results=sum(t.n_pilots for t in all_comps if t.rated),
            skipped=[t for t in all_comps if not t.rated]), encoding="utf-8")

    # ---------- exports CSV ----------
    with open(out_dir / "exports" / "calculs_par_competition.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["competition_id", "competition", "fin", "civlid", "pilote", "nation", "rang", "score",
                    "competitions_avant", "mois_inactif", "sigma_avant", "sigma_apres_inactivite",
                    "mu_avant", "rang_attendu", "proba_attendue", "mu_apres", "sigma_apres",
                    "rating_avant", "rating_apres", "duels_gagnes"])
        for t in sorted(all_comps, key=lambda t: (t.comp.end, t.comp.id)):
            if not t.rated:
                continue
            for r in t.rows:
                w.writerow([t.comp.id, t.comp.name, t.comp.end.isoformat(), r["civlid"], r["name"], r["nation"],
                            r["rank"], r["score"], r["prior"], round(r["idle"], 1),
                            round(r["sigma_raw"], 3), round(r["sigma_pre"], 3), round(r["mu_pre"], 3),
                            r["exp_rank"], round(r["exp_prob"], 5), round(r["mu_post"], 3), round(r["sigma_post"], 3),
                            round(r["mu_pre"] - cfg.z * r["sigma_pre"], 3),
                            round(r["mu_post"] - cfg.z * r["sigma_post"], 3),
                            "" if r["won"] != r["won"] else round(r["won"], 4)])
    with open(out_dir / "exports" / "classement_mensuel.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["mois", "rang", "civlid", "pilote", "nation", "rating", "mu", "sigma", "competitions",
                    "derniere_competition", "actif", "incertitude", "rang_probable_min", "rang_probable_max"])
        for m in months:
            for r in m.rows:
                w.writerow([m.key, r["rank_a"] or "", r["civlid"], r["name"], r["nation"], round(r["rating"], 3),
                            round(r["mu"], 3), round(r["sigma"], 3), r["n"], r["last"], int(r["active"]),
                            round(r["sd"], 1), r.get("rank_lo") or "", r.get("rank_hi") or ""])
    return out_dir
