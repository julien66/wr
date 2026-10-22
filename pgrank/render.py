"""Petits morceaux de HTML partagés par les pages du site (sélecteur de mois, pagination, type de compétition)."""
import re

from .i18n import Locale, pub_month


_PRE = re.compile(r"\bpre[- ]?(pwc|world)|test event", re.I)
_PWC = re.compile(r"paragliding world cup|\bpwc\b|super ?final", re.I)
_CHAMP = re.compile(r"world (paragliding )?championship|fai world|world air games|"
                    r"(european|asian|pan.?american|south american|oceania|asia.?pacific|continental)\b.*championship", re.I)


def comp_kind(name):
    """Type de compétition deviné d'après son nom : « pwc » (Coupe du monde), « champ » (championnat du monde ou
    continental) ou « » (le reste). Sert seulement au filtre de la liste des compétitions."""
    if _PRE.search(name):
        return ""
    if _PWC.search(name):
        return "pwc"
    if _CHAMP.search(name) and not re.search(r"national|south african", name, re.I):
        return "champ"
    return ""


def _pct(v, lo, hi):
    return round(max(0.0, min(100.0, (v - lo) / (hi - lo) * 100)), 1) if hi > lo else 0.0


def build_picker(months, prefix, current, qs="", loc=None):
    loc = loc or Locale("fr")
    """Sélecteur de mois : une ligne par année, douze cases (liens vers chaque classement)."""
    # une case par mois de publication : le classement de clé 2025-12 est rangé en janvier 2026
    by_pub = {pub_month(m.year, m.month): m.key for m in months}
    years = sorted({y for y, _ in by_pub}, reverse=True)
    out = []
    for y in years:
        cells = []
        for mo in range(1, 13):
            k = by_pub.get((y, mo))
            if k:
                cur = ' aria-current="page"' if k == current else ""
                cells.append(f'<a href="{prefix}{k}{qs}"{cur}>{loc.abbr(mo).rstrip(".")}</a>')
            else:
                cells.append("<span></span>")
        out.append(f'<div class="yr"><b>{y}</b>{"".join(cells)}</div>')
    return "".join(out)


def build_pager(key, pg, n_pages, prefix, qs="", loc=None):
    loc = loc or Locale("fr")
    """Liens de pagination : 1 … p-2 p-1 p p+1 p+2 … N."""
    if n_pages <= 1:
        return ""
    href = lambda p: (f"{prefix}{key}" if p == 1 else f"{prefix}{key}-p{p}") + qs
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
