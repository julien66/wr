#!/usr/bin/env python3
"""Site dynamique : lit ranking.db (produit par build_db.py) et réutilise les gabarits du site statique.

    pip install flask
    python app.py --db ranking.db            # http://127.0.0.1:5000
"""
import argparse
import datetime as dt
import json
import re
import math
import sqlite3
import time as _time
from collections import defaultdict, deque
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
from flask import Flask, abort, g, jsonify, redirect, render_template, request, url_for
from jinja2 import ChoiceLoader, FileSystemLoader

from pgrank import render as R
from pgrank.i18n import LANGS, Locale, pick
from pgrank.config import Config
from pgrank.engine import _month_end
from pgrank.search import PilotIndex, tokens
from pgrank.store import norm

HERE = Path(__file__).parent / "pgrank"
PAGE_SIZE = 100
SHOW_LEADERS = False     # page « Numéros 1 » : masquée (404, hors menu et plan du site) tant que les données ne sont pas fiabilisées


class AllPages:
    """Toutes les pages pilote existent (sauf les pilotes retirés) : les gabarits testent `civlid in pages`."""
    def __init__(self, removed=()):
        self.removed = set(removed)

    def __contains__(self, civ):
        return civ not in self.removed


def create_app(db_path, proxy=False):
    app = Flask(__name__, template_folder=str(HERE / "templates"), static_folder=str(HERE / "static"), static_url_path="/static")
    if proxy:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config["DB"] = db_path
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 30 * 86400   # fichiers statiques versionnés (?v=) : cache d'un mois
    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True
    L = lambda: g.loc                                  # Locale de la requête en cours
    app.jinja_env.filters.update(
        iso_my_k=lambda k: L().key_my(k), f1=lambda x: L().f1(x), f2=lambda x: L().f2(x), fint=lambda x: L().fint(x),
        fdelta=lambda x: L().fdelta(x), date_fr=lambda d: L().date(d), iso_my=lambda i: L().iso_my(i),
        pct=lambda x: L().pct(x), pct1=lambda x: L().pct1(x))

    @app.before_request
    def set_language():
        """Langue : ?lang=xx (mémorisée dans un cookie), sinon cookie, sinon préférence du navigateur."""
        want = request.args.get("lang")
        if want in LANGS and request.method == "GET":
            args = {k: v for k, v in request.args.items() if k != "lang"}
            resp = redirect(request.path + ("?" + "&".join(f"{k}={v}" for k, v in args.items()) if args else ""))
            resp.set_cookie("lang", want, max_age=365 * 86400, samesite="Lax")
            return resp
        g.loc = Locale(pick(request.accept_languages, request.cookies.get("lang")))

    # ---------------------------------------------------------------- protections
    hits_h = defaultdict(deque)        # idem pour la recherche
    hits = defaultdict(deque)          # ip -> horodatages des requêtes de la dernière minute (par processus)
    LIMITS = {"search": 120, "all": 400}   # requêtes par minute et par adresse : recherche / toutes les pages

    @app.before_request
    def rate_limit():
        if request.endpoint in (None, "static"):
            return None
        now = _time.monotonic()
        ip = request.remote_addr or "?"
        q = hits[ip]
        while q and now - q[0] > 60:
            q.popleft()
        q.append(now)
        if len(hits) > 5000:           # évite que le dictionnaire grossisse indéfiniment
            for k in [k for k, v in hits.items() if not v or now - v[-1] > 60]:
                hits.pop(k, None)
                hits_h.pop(k, None)
        heavy = request.path.startswith(("/search", "/api/"))
        n_heavy = 0
        if heavy:
            qh = hits_h[ip]
            while qh and now - qh[0] > 60:
                qh.popleft()
            qh.append(now)
            n_heavy = len(qh)
        if len(q) > LIMITS["all"] or n_heavy > LIMITS["search"]:
            resp = jsonify({"error": "too many requests"})
            resp.status_code = 429
            resp.headers["Retry-After"] = "30"
            return resp
        return None

    @app.after_request
    def vary(resp):
        resp.headers.add("Vary", "Accept-Language, Cookie")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        p = request.path
        if p.startswith(("/pilot/", "/search", "/api/")):      # profils individuels : hors moteurs de recherche
            resp.headers["X-Robots-Tag"] = "noindex, nofollow" if p.startswith(("/search", "/api/")) else "noindex"
        if request.endpoint != "static" and resp.status_code == 200 and "Cache-Control" not in resp.headers:
            # la langue dépend du cookie : cache du navigateur seulement (pas de cache partagé/CDN)
            resp.headers["Cache-Control"] = "private, max-age=300"
        return resp

    # ---------------------------------------------------------------- numéros 1
    leaders_cache = {}

    def leaders_by_month():
        """{(sexe, mois): (civlid, rating)} : numéro 1 des pilotes actifs, mois par mois (table `leaders`, sinon calcul direct)."""
        if "d" not in leaders_cache:
            d = {}
            has = db().execute("SELECT 1 FROM sqlite_master WHERE name='leaders'").fetchone()
            if has:
                for r in db().execute("SELECT month, sex, civlid, rating FROM leaders"):
                    d[(r["sex"], r["month"])] = (r["civlid"], r["rating"])
            else:      # ancienne base sans la table : une requête indexée par mois et par catégorie (lent la 1re fois)
                for m in mkeys:
                    for sx, col in (("G", "rank_a"), ("F", "rank_fa")):
                        r = db().execute(f"SELECT civlid, rating FROM monthly WHERE month=? AND {col}=1", (m,)).fetchone()
                        if r:
                            d[(sx, m)] = (r["civlid"], r["rating"])
            leaders_cache["d"] = d
        return leaders_cache["d"]

    @app.route("/leaders.html")
    def leaders():
        if not SHOW_LEADERS:
            abort(404)
        sex = "F" if sex_arg() else "G"
        d = leaders_by_month()
        seq = [(m, *d[(sex, m)]) for m in mkeys if (sex, m) in d]
        names = {r["civlid"]: (r["name"], r["nation"]) for r in db().execute("SELECT civlid, name, nation FROM pilots")}
        reigns = []
        for m, civ, rt in seq:
            if reigns and reigns[-1]["civ"] == civ and mkeys.index(m) == reigns[-1]["i_end"] + 1:
                r = reigns[-1]; r["end"] = m; r["i_end"] += 1; r["n"] += 1; r["peak"] = max(r["peak"], rt)
            else:
                reigns.append(dict(civ=civ, start=m, end=m, i_end=mkeys.index(m), n=1, peak=rt, name=names[civ][0], nation=names[civ][1]))
        tot = defaultdict(int)
        for r in reigns:
            tot[r["civ"]] += r["n"]
        honours = sorted(({"civ": c, "n": n, "name": names[c][0], "nation": names[c][1],
                           "reigns": sum(1 for r in reigns if r["civ"] == c)} for c, n in tot.items()),
                         key=lambda x: (-x["n"], x["name"]))
        now = mkeys[-1]
        # courbe : niveau du numéro 1 mois par mois (abscisse = indice du mois)
        W, H, L, R_, T, B = 920, 280, 52, 14, 22, 30
        lo = min(rt for _, _, rt in seq) if seq else 0
        hi = max(rt for _, _, rt in seq) if seq else 1
        lo, hi = math.floor(lo / 100) * 100 - 100, math.ceil(hi / 100) * 100
        X = lambda i: L + (W - L - R_) * i / max(1, len(mkeys) - 1)
        Y = lambda v: T + (H - T - B) * (1 - (v - lo) / (hi - lo))
        pts = [(X(mkeys.index(m)), Y(rt)) for m, _, rt in seq]
        path = "".join(("M" if i == 0 or mkeys.index(seq[i][0]) != mkeys.index(seq[i - 1][0]) + 1 else "L") + f"{x:.1f},{y:.1f}"
                       for i, (x, y) in enumerate(pts))
        years = sorted({int(m[:4]) for m in mkeys})
        xt = [(X(mkeys.index(f"{y}-01")) if f"{y}-01" in mkeys else X(0), y) for y in years if y % 2 == 0 or len(years) < 12]
        yt = [(Y(v), v) for v in range(int(lo), int(hi) + 1, 100)]
        labels = [dict(x=X(mkeys.index(r["start"]) + (r["n"] - 1) / 2), y=Y(r["peak"]) - 8, text=r["name"].split()[-1], n=r["n"])
                  for r in reigns if r["n"] >= 18]
        return render_template("leaders.html", **common, page="leaders", title=g.loc.gettext("Les numéros 1"), qs="", sex=sex == "F",
                               reigns=list(reversed(reigns)), honours=honours[:12], n_leaders=len(tot), now=now, first=mkeys[0],
                               chart=dict(W=W, H=H, L=L, R=R_, T=T, B=B, path=path, xt=xt, yt=yt, labels=labels, last=pts[-1] if pts else None))

    @app.route("/robots.txt")
    def robots():
        base = request.url_root.rstrip("/")
        txt = f"User-agent: *\nDisallow: /api/\nDisallow: /search\nDisallow: /*?lang=\n\nSitemap: {base}/sitemap.xml\n"
        return app.response_class(txt, mimetype="text/plain")

    @app.route("/sitemap.xml")
    def sitemap():
        base = request.url_root.rstrip("/")
        urls = [f"{base}/", f"{base}/competitions.html", f"{base}/method.html", f"{base}/legal.html"]
        if SHOW_LEADERS:
            urls.append(f"{base}/leaders.html")
        urls += [f"{base}/ranking/{k}.html" for k in mkeys]
        urls += [f"{base}/competition/{r[0]}.html" for r in db().execute("SELECT id FROM competitions")]
        body = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
        return app.response_class(
            '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + body + "</urlset>",
            mimetype="application/xml")

    @app.route("/legal.html")
    def legal():
        return render_template("legal.html", **common, page="legal", title=g.loc.gettext("Mentions légales et données personnelles"), qs="")

    @app.context_processor
    def i18n_context():
        loc = g.loc
        return {"_": loc.gettext, "_h": loc.html, "_n": loc.ngettext, "lang": loc.lang, "langs": LANGS,
                "month_label": loc.month_label, "show_leaders": SHOW_LEADERS, "mlc": loc.lower_month, "span_fr": loc.span,
                "i18n_js": json.dumps({"lang": loc.lang, "t": loc.js_catalog()}, ensure_ascii=False).replace("</", "<\\/")}

    @app.context_processor
    def asset_version():
        """Ajoute ?v=<date de modification> aux liens CSS/JS pour que le navigateur recharge les fichiers modifiés."""
        def v(name):
            try:
                return int((HERE / "static" / name).stat().st_mtime)
            except OSError:
                return 0
        return {"asset_v": {"css": v("style.css"), "js": v("app.js")}}

    boot = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    meta = {k: json.loads(v) for k, v in boot.execute("SELECT key, value FROM meta")}
    months = [NS(key=k, year=y, month=m) for k, y, m in boot.execute("SELECT key, year, month FROM months ORDER BY key")]
    removed = {r[0] for r in boot.execute("SELECT civlid FROM pilots WHERE name_norm=''")}
    boot.close()
    if meta["engine"] != "elo":
        raise SystemExit("app.py ne gère que le moteur Elo")
    cfg = Config(engine="elo", inactive_after_months=meta["inactive_after_months"], z=meta["z"],
                 pilot_min_comps=meta["pilot_min_comps"], elo_params=meta["full_cfg"]["elo_params"])
    params = meta["params"]
    mkeys = [m.key for m in months]
    app.jinja_env.globals.update(elo=True, cfg=cfg, dyn=True)
    common = dict(pages=AllPages(removed), site_title=cfg.site_title, latest_key=mkeys[-1],
                  last_result=dt.date.fromisoformat(meta["last_result"]),
                  build_date=dt.date.fromisoformat(meta["build_date"]), params=params, root="/")

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(f"file:{app.config['DB']}?mode=ro", uri=True)
            g.db.row_factory = sqlite3.Row
        return g.db

    @app.teardown_appcontext
    def close(_):
        d = g.pop("db", None)
        if d is not None:
            d.close()

    def sex_arg():
        return "F" if request.args.get("sex", "").upper() == "F" else ""

    def actifs_arg():
        """Par défaut seuls les pilotes actifs sont classés ; ?actifs=0 affiche aussi les inactifs."""
        return request.args.get("actifs", "1") != "0"

    def nation_arg():
        n = request.args.get("nation", "").strip().upper()
        return n if re.fullmatch(r"[A-Z]{2,4}", n) else ""

    def qs_of(sex, actifs=True, nat=None):
        nat = nation_arg() if nat is None else nat
        parts = (["sex=F"] if sex else []) + ([] if actifs else ["actifs=0"]) + ([f"nation={nat}"] if nat else [])
        return "?" + "&".join(parts) if parts else ""

    # ---------------------------------------------------------------- classement mensuel
    def ranking(key, page, sex):
        """Classement d'un mois. Le rang officiel (rank_a / rank_fa) ne compte que les pilotes actifs.
        Avec ?actifs=0, les inactifs sont aussi listés, à la place que leur niveau leur donnerait, mais sans numéro."""
        actifs = actifs_arg()
        if key not in mkeys:
            abort(404)
        mrow = db().execute("SELECT * FROM months WHERE key=?", (key,)).fetchone()
        i = mkeys.index(key)
        num = "rank_fa" if sex else "rank_a"                       # numéro affiché (rang officiel)
        col = num if actifs else ("rank_f" if sex else "rank")      # ordre et pagination
        total = (mrow["n_active_f"] if actifs else mrow["n_rated_f"]) if sex else (mrow["n_active"] if actifs else mrow["n_rated"])
        n_ranked = mrow["n_active_f"] if sex else mrow["n_active"]  # pilotes numérotés
        nat = nation_arg()
        n_act_nat = 0
        nations = db().execute(
            f"SELECT p.nation AS nat, COUNT(*) AS n FROM monthly m JOIN pilots p USING(civlid) "
            f"WHERE m.month=? AND m.{col} IS NOT NULL AND p.nation<>'' GROUP BY p.nation ORDER BY n DESC, p.nation", (key,)).fetchall()
        if nat:
            total = next((r["n"] for r in nations if r["nat"] == nat), 0)
            n_act_nat = db().execute(
                f"SELECT COUNT(*) FROM monthly m JOIN pilots p USING(civlid) WHERE m.month=? AND p.nation=? "
                f"AND m.{num} IS NOT NULL", (key, nat)).fetchone()[0]
            n_ranked = n_act_nat
        # liste du mois (ou de la nation) : pos = place dans la liste affichée, nr = rang officiel (actifs seulement)
        if nat:
            base = (f"SELECT * FROM (SELECT m.*, p.name, p.nation, ROW_NUMBER() OVER w AS pos, "
                    f"CASE WHEN m.{num} IS NOT NULL THEN COUNT(m.{num}) OVER w END AS nr "
                    f"FROM monthly m JOIN pilots p USING(civlid) WHERE m.month=? AND p.nation=? AND m.{col} IS NOT NULL "
                    f"WINDOW w AS (ORDER BY m.{col} ROWS UNBOUNDED PRECEDING))")
            args = (key, nat)
        else:
            base = (f"SELECT m.*, p.name, p.nation, m.{col} AS pos, m.{num} AS nr FROM monthly m JOIN pilots p USING(civlid) "
                    f"WHERE m.month=? AND m.{col} IS NOT NULL")
            args = (key,)
        n_pages = max(1, math.ceil(total / PAGE_SIZE))
        # saut direct à un rang officiel : ?rank=600
        want = request.args.get("rank", type=int)
        if want and 1 <= want <= n_ranked:
            hit = db().execute(f"SELECT pos FROM ({base}) WHERE nr=?", (*args, want)).fetchone()
            if hit:
                p = (hit["pos"] - 1) // PAGE_SIZE + 1
                name = f"/ranking/{key}.html" if p == 1 else f"/ranking/{key}-p{p}.html"
                return redirect(f"{name}{qs_of(sex, actifs)}#r{want}")
        if page > n_pages:
            abort(404)
        lo_r, hi_r = (page - 1) * PAGE_SIZE + 1, page * PAGE_SIZE
        rows = []
        for r in db().execute(f"SELECT * FROM ({base}) WHERE pos BETWEEN ? AND ? ORDER BY pos", (*args, lo_r, hi_r)):
            d = dict(r)
            d["wrank"] = d[num]
            d["rank_a"] = d["nr"]
            d["move"] = None if nat else (d["move_f"] if sex else d["move"])
            d["mu"], d["sigma"] = d["rating"], 0.0
            d["a"], d["b"], d["c"] = (R._pct(d["rating"], mrow["lo"], mrow["hi"]),) * 3
            rows.append(d)
        comps = [NS(comp=NS(id=c["id"], name=c["name"], start=dt.date.fromisoformat(c["start"]), end=dt.date.fromisoformat(c["end"])),
                    n_pilots=c["n_pilots"])
                 for cid in json.loads(mrow["comp_ids"])
                 for c in [db().execute("SELECT * FROM competitions WHERE id=?", (cid,)).fetchone()]]
        qs = qs_of(sex, actifs)
        month = NS(key=key, year=mrow["year"], month=mrow["month"],
                   n_rated=total,
                   n_active=n_act_nat if nat else (mrow["n_active_f"] if sex else mrow["n_active"]))
        genre_known = db().execute("SELECT COUNT(*) FROM pilots WHERE gender IS NOT NULL").fetchone()[0]
        return render_template(
            "ranking.html", **common, page="ranking", month=month, rows=rows, scale_hi=mrow["hi"],
            prev_m=months[i - 1] if i else None, next_m=months[i + 1] if i + 1 < len(months) else None,
            prefix="/ranking/", qs=qs, sex=sex, actifs=actifs, genre_known=genre_known,
            qs_all=qs_of("", actifs), qs_f=qs_of("F", actifs), qs_toggle=qs_of(sex, not actifs),
            nat=nat, qs_nonat=qs_of(sex, actifs, ""),
            picker=R.build_picker(months, "/ranking/", key, qs, g.loc),
            pager=R.build_pager(key, page, n_pages, "/ranking/", qs, g.loc),
            pg=page, n_pages=n_pages, first=lo_r, last=lo_r + len(rows) - 1, n_shown=total, n_ranked=n_ranked,
            comps=comps, title=g.loc.month_label(mrow["year"], mrow["month"]) + (" – " + g.loc.gettext("féminin") if sex else "") + (" – " + nat if nat else "")
            + (" " + g.loc.gettext("(page {n})", n=page) if page > 1 else ""))

    @app.route("/")
    @app.route("/index.html")
    def index():
        return ranking(mkeys[-1], 1, sex_arg())

    @app.route("/ranking/<key>.html")
    def ranking_page1(key):
        return ranking(key, 1, sex_arg())

    @app.route("/ranking/<key>-p<int:page>.html")
    def ranking_page(key, page):
        return ranking(key, page, sex_arg())

    @app.route("/ranking/<int:year>")
    def ranking_year(year):
        ks = [k for k in mkeys if k.startswith(f"{year:04d}-")]
        if not ks:
            abort(404)
        return redirect(f"/ranking/{ks[-1]}.html{qs_of(sex_arg(), actifs_arg())}")

    # ---------------------------------------------------------------- compétition
    @app.route("/competition/<int:cid>.html")
    def competition(cid):
        c = db().execute("SELECT * FROM competitions WHERE id=?", (cid,)).fetchone()
        if c is None:
            abort(404)
        rows = []
        for r in db().execute("SELECT r.*, p.name, p.nation FROM results r JOIN pilots p USING(civlid) "
                              "WHERE r.comp_id=? ORDER BY r.rank, p.name", (cid,)):
            rows.append(dict(r))
        rated = bool(c["rated"])
        if rated:
            vals = [r["mu_pre"] for r in rows] + [r["mu_post"] for r in rows]
            lo, hi = min(vals), max(vals)
            for r in rows:
                r["rt_pre"], r["rt_post"] = r["mu_pre"], r["mu_post"]
                r["d"] = r["rt_post"] - r["rt_pre"]
                r["a"], r["b"] = R._pct(r["rt_pre"], lo, hi), R._pct(r["rt_post"], lo, hi)
                r["inflated"] = r["idle"] > cfg.inactive_after_months
        t = NS(n_pilots=c["n_pilots"], n_nations=c["n_nations"], rated=rated, reason=c["reason"],
               acc_all=c["acc_all"] if c["acc_all"] is not None else float("nan"),
               acc_exp=c["acc_exp"] if c["acc_exp"] is not None else float("nan"),
               n_pairs_exp=c["n_pairs_exp"], n_idle_returns=c["n_idle_returns"],
               field_rating=c["field_rating"] if c["field_rating"] is not None else float("nan"))
        end = dt.date.fromisoformat(c["end"])
        comp = NS(id=c["id"], name=c["name"], url=c["url"], start=dt.date.fromisoformat(c["start"]), end=end)
        return render_template("competition.html", **common, page="competition", t=t, c=comp, rows=rows,
                               month_key=f"{end.year:04d}-{end.month:02d}", month_name=g.loc.month_label(end.year, end.month),
                               title=c["name"], qs="")

    @app.route("/competitions.html")
    def competitions():
        by_year = defaultdict(list)
        for c in db().execute("SELECT * FROM competitions ORDER BY end DESC, id DESC"):
            end = dt.date.fromisoformat(c["end"])
            by_year[end.year].append(NS(comp=NS(id=c["id"], name=c["name"], start=dt.date.fromisoformat(c["start"]), end=end),
                                        n_pilots=c["n_pilots"], rated=bool(c["rated"])))
        n = sum(len(v) for v in by_year.values())
        # les liens du gabarit sont relatifs à la racine : on passe root="/" (déjà dans common)
        return render_template("competitions.html", **common, page="competitions",
                               years=sorted(by_year.items(), reverse=True), n_comps=n, title=g.loc.gettext("Compétitions"), qs="")

    # ---------------------------------------------------------------- pilote
    @app.route("/pilot/<civ>.html")
    def pilot(civ):
        p = db().execute("SELECT * FROM pilots WHERE civlid=?", (civ,)).fetchone()
        if p is None or civ in removed:
            abort(404)
        hist = db().execute(
            "SELECT r.*, c.name AS cname, c.n_pilots FROM results r JOIN competitions c ON c.id=r.comp_id "
            "WHERE r.civlid=? AND c.rated=1 ORDER BY r.end, r.comp_id", (civ,)).fetchall()
        pts, table = [], []
        for r in hist:
            end = dt.date.fromisoformat(r["end"])
            pts.append({"d": r["end"], "id": r["comp_id"], "n": r["cname"], "k": r["rank"], "N": r["n_pilots"],
                        "m0": round(r["mu_pre"], 3), "s0": 0, "m1": round(r["mu_post"], 3), "s1": 0,
                        "x": r["exp_rank"], "w": round(r["won"], 4), "e": round(r["exp_prob"], 4)})
            table.append({"t": NS(comp=NS(id=r["comp_id"], name=r["cname"], end=end), n_pilots=r["n_pilots"]),
                          "r": dict(r), "rt_pre": r["mu_pre"], "rt_post": r["mu_post"], "d": r["mu_post"] - r["mu_pre"],
                          "pct": 100.0 * r["rank"] / r["n_pilots"]})
        fm = db().execute("SELECT * FROM months WHERE key=?", (mkeys[-1],)).fetchone()
        f = db().execute("SELECT * FROM monthly WHERE month=? AND civlid=?", (mkeys[-1], civ)).fetchone()
        fin = None
        if f:
            fin = dict(rank=f["rank_a"], rank_f=f["rank_fa"], rating=f["rating"], mu=f["rating"], sigma=0.0, n=f["n"],
                       active=bool(f["active"]), idle=f["idle"], last=f["last"])
        # rang officiel de fin de mois (vide les mois où le pilote est inactif)
        rank_pts = [dict(m=r["month"], r=r["rank_a"], f=r["rank_fa"], v=round(r["rating"], 1), a=r["active"],
                         n=r["n_active"], nf=r["n_active_f"])
                    for r in db().execute(
                        "SELECT m.month, m.rank_a, m.rank_fa, m.rating, m.active, mo.n_active, mo.n_active_f FROM monthly m "
                        "JOIN months mo ON mo.key=m.month WHERE m.civlid=? ORDER BY m.month", (civ,))]
        best = None
        ranked = [x for x in rank_pts if x["r"]]
        if ranked:
            b = min(ranked, key=lambda x: (x["r"], x["m"]))
            best = {"r": b["r"], "m": b["m"], "v": b["v"]}
            bf = [x for x in rank_pts if x["f"]]
            best_f = ({"r": (c := min(bf, key=lambda x: (x["f"], x["m"])))["f"], "m": c["m"]} if bf else None)
        else:
            best_f = None
        rdata = json.dumps({"pts": rank_pts, "best": best, "best_f": best_f, "fem": p["gender"] == "F",
                            "inactive": cfg.inactive_after_months, "now": _month_end(*map(int, mkeys[-1].split("-"))).isoformat()},
                           ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        ranks = [x["r"]["rank"] for x in table]
        st = dict(n=len(table), wins=sum(k == 1 for k in ranks), podiums=sum(k <= 3 for k in ranks),
                  top10=sum(k <= 10 for k in ranks),
                  median_pct=float(np.median([x["pct"] for x in table])) if table else 0.0, best=min(ranks) if ranks else 0)
        data = {"elo": True, "z": 0.0, "inactive": cfg.inactive_after_months, "last": fin["last"] if fin else None,
                "active": fin["active"] if fin else None, "mu": fin["mu"] if fin else None, "sigma": 0.0,
                "now": _month_end(*map(int, mkeys[-1].split("-"))).isoformat(), "pts": pts}
        js = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        return render_template("pilot.html", **common, page="pilot", title=p["name"], name=p["name"], nation=p["nation"],
                               civ=civ, fin=fin, st=st, table=list(reversed(table)), js=js, n_world=fm["n_active"],
                               n_world_f=fm["n_active_f"], gender=p["gender"], qs="", rdata=rdata if rank_pts else None,
                               best=best, best_f=best_f)

    # ---------------------------------------------------------------- recherche
    index_cache = {}

    def pilot_index():
        if "idx" not in index_cache:
            last = mkeys[-1]
            rows = [dict(r) for r in db().execute(
                "SELECT p.civlid, p.name, p.nation, p.gender, m.rank AS pos, m.rank_a AS rank, m.rank_fa AS rank_f, m.rating, m.active, m.n, m.last, "
                "s.peak, s.n_comps, s.first_end, s.last_end FROM pilots p "
                "LEFT JOIN monthly m ON m.civlid=p.civlid AND m.month=? "
                "LEFT JOIN (SELECT civlid, MAX(mu_post) AS peak, COUNT(*) AS n_comps, MIN(end) AS first_end, MAX(end) AS last_end "
                "           FROM results WHERE mu_post IS NOT NULL GROUP BY civlid) s ON s.civlid=p.civlid "
                "WHERE p.name_norm<>''", (last,))]
            index_cache["idx"] = PilotIndex(rows)
        return index_cache["idx"]

    @app.route("/search")
    def search():
        q = request.args.get("q", "").strip()
        nation = request.args.get("nation", "").strip().upper() or None
        sex = sex_arg()
        qn = norm(q)
        pil, total, facets, comps = [], 0, [], []
        if q.isdigit():                                    # numéro CIVL
            if q not in removed and db().execute("SELECT 1 FROM pilots WHERE civlid=?", (q,)).fetchone():
                return redirect(f"/pilot/{q}.html")
        if len(qn) >= 2:
            pil, total, facets = pilot_index().search(q, nation=nation, sex=sex)
            words = qn.split()
            cond = " AND ".join(["name_norm LIKE ?"] * len(words))
            comps = [dict(r) for r in db().execute(
                f"SELECT id, name, start, end, n_pilots, rated FROM competitions WHERE {cond} ORDER BY end DESC LIMIT 50",
                ["%" + w + "%" for w in words])]
            # une seule réponse sans ambiguïté : on ouvre directement la page du pilote
            if request.args.get("go") and len(pil) == 1 and not nation:
                return redirect(f"/pilot/{pil[0]['civlid']}.html")
        return render_template("search.html", **common, page="search", q=q, pilots=pil, total=total, facets=facets,
                               nation=nation or "", sex=sex, comps=comps, title=g.loc.gettext("Recherche"), qs="",
                               iso_date=dt.date.fromisoformat, tokens=tokens)

    @app.route("/api/suggest")
    def suggest():
        q = request.args.get("q", "").strip()
        out, total = [], 0
        if len(norm(q)) >= 2:
            found, total, _ = pilot_index().search(q, limit=15)
            for p in found:
                out.append({"id": p["civlid"], "name": p["name"], "nation": p["nation"], "rank": p["rank"],
                            "rating": round(p["rating"]) if p["rating"] is not None else None,
                            "active": bool(p["active"]) if p["pos"] else None, "f": p["gender"] == "F",
                            "n": p["n_comps"], "years": [p["first_end"][:4], p["last_end"][:4]] if p["first_end"] else None})
        return jsonify({"items": out, "total": total})

    @app.route("/method.html")
    def method():
        q1 = lambda sql: db().execute(sql).fetchone()[0]
        return render_template("method.html", **common, page="method", metrics=meta["metrics"], title=g.loc.gettext("Méthode"),
                               n_rated=q1("SELECT COUNT(*) FROM competitions WHERE rated=1"),
                               n_comps=q1("SELECT COUNT(*) FROM competitions"),
                               n_pilots=q1("SELECT COUNT(*) FROM pilots"),
                               n_results=q1("SELECT COUNT(*) FROM results r JOIN competitions c ON c.id=r.comp_id WHERE c.rated=1"),
                               first=months[0], qs="",
                               skipped=db().execute("SELECT id FROM competitions WHERE rated=0").fetchall())

    with app.app_context():     # index de recherche construit au démarrage (≈ 5 s)
        pilot_index()
    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="ranking.db")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--proxy", action="store_true", help="derrière un reverse proxy (Caddy, nginx) : lit l'adresse du visiteur dans X-Forwarded-For")
    a = ap.parse_args()
    create_app(a.db, proxy=a.proxy).run(host=a.host, port=a.port)


if __name__ == "__main__":
    main()
