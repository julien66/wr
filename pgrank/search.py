"""Recherche de pilotes : insensible aux accents et à l'ordre des mots, préfixes (« j garcia »), fautes de frappe, classement par pertinence."""
import re
from collections import Counter, defaultdict

from .store import norm

TOKEN = re.compile(r"[a-z0-9]+")


def tokens(s):
    return TOKEN.findall(norm(s))


def _lev(a, b, cap):
    """Distance d'édition (Damerau simplifié : transposition de deux lettres = 1), abandonne au-delà de cap."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j, cb in enumerate(b, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                v = min(v, prev2[j - 2] + 1)
            cur[j] = v
            best = min(best, v)
        if best > cap:
            return cap + 1
        prev2, prev = prev, cur
    return prev[-1]


class PilotIndex:
    def __init__(self, pilots):
        """pilots : liste de dicts (civlid, name, nation, gender, rank, rating, active, n, last, peak, first_year...)."""
        self.p = pilots
        self.by_tok = defaultdict(list)           # jeton -> indices de pilotes
        self.ptoks = []
        for i, p in enumerate(pilots):
            ts = tokens(p["name"])
            p["_norm"] = " ".join(ts)
            self.ptoks.append(ts)
            for t in set(ts):
                self.by_tok[t].append(i)
        self.vocab = list(self.by_tok)
        self.by_id = {p["civlid"]: i for i, p in enumerate(pilots)}

    def _token_scores(self, q):
        """jeton de la requête -> {indice pilote: score 0..1}."""
        out = {}
        cap = 0 if len(q) < 4 else 1 if len(q) < 8 else 2
        for t in self.vocab:
            if t == q:
                s = 1.0
            elif t.startswith(q):
                s = 0.9 if len(q) > 1 else 0.7
            elif len(q) >= 3 and q in t:
                s = 0.6
            elif cap and abs(len(t) - len(q)) <= cap and t[0] == q[0] or (cap and abs(len(t) - len(q)) <= cap and len(q) >= 5):
                d = _lev(q, t, cap)
                s = 0.0 if d > cap else (0.55 if d == 1 else 0.4)
            else:
                continue
            if s:
                for i in self.by_tok[t]:
                    if out.get(i, 0) < s:
                        out[i] = s
        return out

    def search(self, q, nation=None, sex=None, limit=100):
        """Retourne (résultats triés, total, facettes de nations)."""
        qs = tokens(q)
        if not qs or sum(map(len, qs)) < 2:
            return [], 0, []
        per = [self._token_scores(t) for t in qs]
        cand = set(per[0])
        for d in per[1:]:
            cand &= set(d)
        qn = " ".join(qs)
        scored = []
        for i in cand:
            p = self.p[i]
            s = sum(d[i] for d in per) / len(qs)
            if p["_norm"] == qn:
                s += 1.0                                    # nom exact
            elif sorted(self.ptoks[i]) == sorted(qs):
                s += 0.8                                    # mêmes mots dans un autre ordre
            elif p["_norm"].startswith(qn):
                s += 0.3
            extra = len(self.ptoks[i]) - len(qs)            # moins de mots en trop = plus proche
            s -= 0.02 * max(0, extra)
            scored.append((s, i))
        # filtres appliqués après le calcul des facettes de nations
        facets = Counter(self.p[i]["nation"] for _, i in scored if (sex != "F" or self.p[i]["gender"] == "F"))
        res = [(s, i) for s, i in scored
               if (not nation or self.p[i]["nation"] == nation) and (sex != "F" or self.p[i]["gender"] == "F")]
        # tri : pertinence (arrondie pour départager les quasi-égalités), puis actifs, puis meilleur rating
        res.sort(key=lambda x: (-round(x[0], 1), not (self.p[x[1]]["rank"] and self.p[x[1]]["active"]),
                                -(self.p[x[1]]["rating"] or 0), self.p[x[1]]["name"]))
        return [self.p[i] for _, i in res[:limit]], len(res), facets.most_common(12)

    def suggest(self, q, limit=8):
        r, _, _ = self.search(q, limit=limit)
        return r
