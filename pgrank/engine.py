"""Calcul chronologique des ratings avec OpenSkill (Plackett-Luce), instantanés mensuels et traces."""
import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .config import Config
from .metrics import pairwise_accuracy

DAYS_PER_MONTH = 30.4375


@dataclass
class CompTrace:
    comp: object
    rated: bool
    reason: str = ""
    rows: list = field(default_factory=list)   # une entrée par pilote, triée par rang
    n_pilots: int = 0
    n_nations: int = 0
    field_rating: float = float("nan")  # rating moyen du plateau avant la compétition
    acc_all: float = float("nan")       # part des duels bien prédits (tous pilotes)
    acc_exp: float = float("nan")       # idem entre pilotes déjà évalués (>= 3 compétitions)
    n_pairs_exp: int = 0
    n_idle_returns: int = 0             # pilotes dont σ a été relevé pour cause d'inactivité


@dataclass
class Month:
    key: str                            # "2026-09"
    year: int
    month: int
    rows: list                          # top N : dicts prêts à afficher
    n_rated: int
    n_active: int
    comp_ids: list


@dataclass
class Output:
    traces: dict                        # competition_id -> CompTrace
    months: list
    pilots: dict
    metrics: dict
    cfg: Config
    model: object
    final: dict = field(default_factory=dict)   # civlid -> état au dernier mois (rang mondial, rating, activité)
    final_month: str = ""


@dataclass
class EloModel:
    """Paramètres d'un Elo multijoueur (tous contre tous) ; mêmes noms d'attributs qu'un modèle OpenSkill."""
    mu: float = 1500.0
    sigma: float = 0.0
    beta: float = None
    tau: float = None
    kappa: float = None
    k: float = 64.0
    boost: float = 4.0
    scale: float = 400.0


def _month_iter(start, end):
    y, m = start
    while (y, m) <= end:
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def _month_end(y, m):
    return dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)


def _openskill_model(cfg):
    import openskill.models as osk_models      # importé seulement si on utilise OpenSkill
    return getattr(osk_models, cfg.model)(**cfg.model_params)


def run(comps, pilots, cfg: Config, sink=None) -> Output:
    """sink(month) : si fourni, reçoit chaque classement mensuel complet (tous les pilotes) puis la liste est vidée
    pour ne pas la garder en mémoire (utilisé pour écrire la base SQLite)."""
    elo = cfg.engine == "elo"
    model = EloModel(**cfg.elo_params) if elo else _openskill_model(cfg)
    mu0, sigma0 = model.mu, model.sigma
    neff = np.zeros(32768)               # Elo : nombre de compétitions « effectif » (baisse après une longue absence)

    idx = {}                            # civlid -> position dans les tableaux d'état
    civ_by_idx = []
    cap = 32768
    mu = np.full(cap, mu0); sigma = np.full(cap, sigma0)
    last_end = np.zeros(cap, dtype=np.int64); n_comps = np.zeros(cap, dtype=np.int32)

    def slot(civ):
        nonlocal mu, sigma, last_end, n_comps, cap, neff
        i = idx.get(civ)
        if i is None:
            i = idx[civ] = len(idx)
            civ_by_idx.append(civ)
            if i >= cap:
                cap *= 2
                mu = np.resize(mu, cap); sigma = np.resize(sigma, cap)
                last_end = np.resize(last_end, cap); n_comps = np.resize(n_comps, cap); neff = np.resize(neff, cap)
                mu[i:] = mu0; sigma[i:] = sigma0; last_end[i:] = 0; n_comps[i:] = 0; neff[i:] = 0
        return i

    by_month = defaultdict(list)
    for c in comps:
        by_month[(c.end.year, c.end.month)].append(c)
    first_m, last_m = min(by_month), max(by_month)

    traces, months = {}, []
    prev_rank = None
    acc = {k: defaultdict(lambda: [0.0, 0]) for k in ("exp", "all", "elite")}

    month_list = list(_month_iter(first_m, last_m))
    for mi, (y, m) in enumerate(month_list):
        full = sink is not None or mi >= len(month_list) - cfg.full_months
        done = []
        for c in by_month.get((y, m), []):
            res = c.results
            if c.id in cfg.excluded:
                traces[c.id] = CompTrace(c, False, cfg.excluded[c.id], rows=sorted(res, key=lambda r: r.rank), n_pilots=len(res))
                continue
            if cfg.include_zero_scores is False:
                res = [r for r in res if (r.score or 0) > 0]
            if len(res) < cfg.min_field:
                traces[c.id] = CompTrace(c, False, f"moins de {cfg.min_field} pilotes classés",
                                         rows=sorted(res, key=lambda r: r.rank), n_pilots=len(res))
                continue
            # classement : les pilotes à 0 point finissent ex æquo derniers
            n_pos = sum(1 for r in res if (r.score or 0) > 0)
            ranks = np.array([r.rank if (r.score or 0) > 0 else n_pos + 1 for r in res], float)
            order = np.lexsort((np.arange(len(res)), ranks))
            res = [res[i] for i in order]; ranks = ranks[order]

            ids = np.array([slot(r.civlid) for r in res])
            if len(set(ids.tolist())) != len(ids):      # sécurité : doublon pilote dans la compétition
                keep = list({i: k for k, i in enumerate(ids)}.values())
                res = [res[k] for k in keep]; ranks = ranks[keep]; ids = ids[keep]

            mu_pre = mu[ids].copy(); sig_raw = sigma[ids].copy()
            seen = last_end[ids] > 0
            idle = np.where(seen, np.maximum((c.start.toordinal() - last_end[ids]) / DAYS_PER_MONTH, 0.0), 0.0)
            excess = np.maximum(idle - cfg.inactive_after_months, 0.0)
            prior = n_comps[ids].copy()
            if elo:
                # --- Elo : chaque pilote « joue » contre tous les autres ; le résultat compte 1 (devant), 0,5 (ex æquo), 0 ---
                sig_pre = sig_raw = np.zeros(len(ids))
                ne = neff[ids] * 0.5 ** (excess / cfg.elo_idle_half_life)   # longue absence : le pilote redevient « provisoire »
                kk = model.k * (1.0 + model.boost * np.exp(-ne / 10.0))
                nn = len(ids)
                d = mu_pre[None, :] - mu_pre[:, None]
                exp_mat = 1.0 / (1.0 + 10.0 ** (d / model.scale)); np.fill_diagonal(exp_mat, 0.0)
                s_mat = (ranks[:, None] < ranks[None, :]) * 1.0 + (ranks[:, None] == ranks[None, :]) * 0.5
                np.fill_diagonal(s_mat, 0.0)
                new_mu = mu_pre + kk * (s_mat.sum(1) - exp_mat.sum(1)) / (nn - 1)
                new_sigma = np.zeros(nn)
                exp_rank = np.rint((1.0 - exp_mat).sum(1)).astype(int)      # 1 + nombre attendu de pilotes classés devant
                exp_prob = exp_mat.sum(1) / (nn - 1)
                k_used = kk
                won = s_mat.sum(1) / (nn - 1)                  # part des duels réellement gagnés (ex æquo = 0,5)
                neff[ids] = ne + 1.0
            else:
                sig_pre = np.minimum(sigma0, np.sqrt(sig_raw ** 2 + cfg.idle_tau ** 2 * excess))
                k_used = np.zeros(len(ids)); won = np.full(len(ids), np.nan)
                # --- OpenSkill : prévision puis mise à jour ---
                teams = [[model.rating(mu=float(a), sigma=float(b))] for a, b in zip(mu_pre, sig_pre)]
                predicted = model.predict_rank(teams)                        # (rang attendu, probabilité)
                if cfg.use_scores:
                    raw = np.array([float(r.score or 0.0) for r in res])
                    sc = (raw / raw.max() * 1000.0) if raw.max() > 0 else raw
                    updated = model.rate(teams, scores=[float(x) for x in sc])
                else:
                    updated = model.rate(teams, ranks=[float(r) for r in ranks])
                new_mu = np.array([t[0].mu for t in updated]); new_sigma = np.array([t[0].sigma for t in updated])
                exp_rank = np.array([p[0] for p in predicted]); exp_prob = np.array([p[1] for p in predicted])

            acc_all, k_all = pairwise_accuracy(mu_pre, ranks)
            exp_mask = prior >= 3
            acc_exp, k_exp = (pairwise_accuracy(mu_pre[exp_mask], ranks[exp_mask])
                              if exp_mask.sum() >= 2 else (float("nan"), 0))
            elite = np.where(prior >= 10)[0]
            if len(elite) >= 4:
                elite = elite[np.argsort(-mu_pre[elite])[:30]]
                a_el, k_el = pairwise_accuracy(mu_pre[elite], ranks[elite])
                if k_el:
                    acc["elite"][c.end.year][0] += a_el * k_el; acc["elite"][c.end.year][1] += k_el
            for key, (a, k) in (("exp", (acc_exp, k_exp)), ("all", (acc_all, k_all))):
                if k:
                    acc[key][c.end.year][0] += a * k; acc[key][c.end.year][1] += k

            mu[ids], sigma[ids] = new_mu, new_sigma
            last_end[ids] = c.end.toordinal(); n_comps[ids] += 1

            rows = [{
                "civlid": r.civlid, "name": pilots[r.civlid][0], "nation": pilots[r.civlid][1] or r.nation,
                "rank": int(ranks[k]), "score": r.score, "prior": int(prior[k]),
                "idle": float(idle[k]), "sigma_raw": float(sig_raw[k]), "sigma_pre": float(sig_pre[k]),
                "mu_pre": float(mu_pre[k]), "exp_rank": int(exp_rank[k]), "exp_prob": float(exp_prob[k]),
                "mu_post": float(new_mu[k]), "sigma_post": float(new_sigma[k]), "k": float(k_used[k]), "won": float(won[k]),
            } for k, r in enumerate(res)]
            traces[c.id] = CompTrace(
                c, True, rows=rows, n_pilots=len(rows),
                n_nations=len({r["nation"] for r in rows if r["nation"]}),
                field_rating=float(np.mean(mu_pre - cfg.z * sig_pre)),
                acc_all=acc_all, acc_exp=acc_exp, n_pairs_exp=k_exp,
                n_idle_returns=int((excess > 0).sum()))
            done.append(c.id)

        # ----- instantané de fin de mois -----
        n = len(idx)
        if n:
            m_end = _month_end(y, m).toordinal()
            ordv = mu[:n] - cfg.z * sigma[:n]                    # = rating.ordinal(z) d'OpenSkill
            order = np.argsort(-ordv, kind="stable")
            rank_pos = np.empty(n, dtype=np.int64); rank_pos[order] = np.arange(1, n + 1)
            idle_m = (m_end - last_end[:n]) / DAYS_PER_MONTH
            active = idle_m <= cfg.inactive_after_months
            # rang officiel : les inactifs ne prennent pas de place (rank_a = 0 pour eux)
            act_ord = active[order]
            rank_a = np.zeros(n, dtype=np.int64); rank_a[order] = np.where(act_ord, np.cumsum(act_ord), 0)
            rows = []
            for pos, i in enumerate(order if full else order[:cfg.top_n], 1):
                civ = civ_by_idx[i]
                pr = None if prev_rank is None or i >= len(prev_rank) or prev_rank[i] == 0 else int(prev_rank[i])
                ra = int(rank_a[i]) or None
                rows.append({
                    "rank": pos, "rank_a": ra, "civlid": civ, "name": pilots[civ][0], "nation": pilots[civ][1],
                    "rating": float(ordv[i]), "mu": float(mu[i]), "sigma": float(sigma[i]),
                    "n": int(n_comps[i]), "active": bool(active[i]),
                    "last": dt.date.fromordinal(int(last_end[i])).isoformat(),
                    "idle": float(idle_m[i]), "move": None if pr is None or ra is None else pr - ra,
                })
            mth_obj = Month(f"{y:04d}-{m:02d}", y, m, rows, n, int(active.sum()), done)
            if sink is not None:
                sink(mth_obj)
                mth_obj.rows = []
            months.append(mth_obj)
            prev_rank = np.zeros(cap, dtype=np.int64); prev_rank[:n] = rank_a

    def summarize(key):
        d = acc[key]
        tot = sum(v[1] for v in d.values())
        return ({y: (v[0] / v[1], v[1]) for y, v in sorted(d.items()) if v[1]},
                (sum(v[0] for v in d.values()) / tot) if tot else float("nan"), tot)

    by_year, total, pairs = summarize("exp")
    metrics = {"acc_by_year": by_year, "acc_exp_total": total, "pairs_exp_total": pairs,
               "acc_elite_total": summarize("elite")[1], "pairs_elite_total": summarize("elite")[2]}
    final = {}
    if months:
        for i in range(n):
            final[civ_by_idx[i]] = {"rank": int(rank_pos[i]), "rank_a": int(rank_a[i]) or None, "rating": float(ordv[i]), "mu": float(mu[i]),
                                    "sigma": float(sigma[i]), "n": int(n_comps[i]), "active": bool(active[i]),
                                    "idle": float(idle_m[i]),
                                    "last": dt.date.fromordinal(int(last_end[i])).isoformat()}
    return Output(traces, months, pilots, metrics, cfg, model, final, months[-1].key if months else "")
