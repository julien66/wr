"""Traduction du site : catalogues JSON (clé = texte français), formats de nombres et de dates par langue.

Ajouter une langue : créer pgrank/i18n/<code>.json (copie de en.json traduite) et déclarer le code dans LANGS.
Les clés manquantes retombent sur le texte français : une traduction incomplète ne casse rien.
"""
import json
from pathlib import Path

from markupsafe import Markup

LANGS = {"fr": "Français", "en": "English", "de": "Deutsch", "es": "Español"}
DEFAULT = "fr"          # langue des textes sources
FALLBACK = "en"         # langue servie quand celle du navigateur n'est pas disponible
_DIR = Path(__file__).parent / "i18n"
CATALOGS = {}
for _code in LANGS:
    _f = _DIR / f"{_code}.json"
    CATALOGS[_code] = json.loads(_f.read_text(encoding="utf-8")) if _f.exists() else {}

MONTHS = {
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
}
ABBR = {
    "fr": ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "de": ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"],
    "es": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
}
NBSP = " "


class Locale:
    def __init__(self, lang):
        self.lang = lang if lang in LANGS else DEFAULT
        self.cat = CATALOGS.get(self.lang, {})
        self.fr = self.lang == "fr"
        self.comma = self.lang in ("fr", "de", "es")      # virgule décimale

    # ---------- textes ----------
    def gettext(self, key, **kw):
        s = self.cat.get(key, key)
        return s.format(**kw) if kw else s

    def html(self, key, **kw):
        """Comme gettext, mais le texte contient du HTML sûr (les valeurs insérées sont échappées)."""
        return Markup(self.cat.get(key, key)).format(**kw)

    def ngettext(self, one, other, n, html=False, **kw):
        plural = n > 1 if self.fr else n != 1   # français : 0 et 1 au singulier
        key = other if plural else one
        return self.html(key, n=n, **kw) if html else self.gettext(key, n=n, **kw)

    def js_catalog(self):
        base = CATALOGS.get("fr", {})
        out = {k: v for k, v in base.items() if k.startswith("js.")}
        out.update({k: v for k, v in self.cat.items() if k.startswith("js.")})
        return out

    # ---------- nombres ----------
    def f1(self, x):
        s = f"{x:.1f}"
        return s.replace(".", ",") if self.comma else s

    def f2(self, x):
        s = f"{x:.2f}"
        return s.replace(".", ",") if self.comma else s

    def fint(self, n):
        s = f"{int(n):,}"
        if self.fr:
            return s.replace(",", NBSP)
        if self.lang == "de" or (self.lang == "es" and abs(int(n)) >= 10000):
            return s.replace(",", ".")
        if self.lang == "es":
            return s.replace(",", "")
        return s

    def fdelta(self, x):
        s = self.f1(abs(x))
        return ("+" if x > 0.049 else "−" if x < -0.049 else "") + s

    def pct(self, x):
        """Pourcentage entier : « 85 % » en français, « 85% » en anglais."""
        return f"{round(x)} %" if self.comma else f"{round(x)}%"

    def pct1(self, x):
        s = self.f1(x)
        return f"{s} %" if self.comma else f"{s}%"

    # ---------- dates ----------
    def date(self, d):
        if self.fr or self.lang == "es":
            return f"{d.day} {ABBR[self.lang][d.month - 1]} {d.year}"
        if self.lang == "de":
            return f"{d.day}. {ABBR['de'][d.month - 1]} {d.year}"
        return f"{ABBR['en'][d.month - 1]} {d.day}, {d.year}"

    def span(self, a, b):
        if a == b:
            return self.date(a)
        ab = ABBR[self.lang]
        if (a.year, a.month) == (b.year, b.month):
            if self.lang == "de":
                return f"{a.day}.–{b.day}. {ab[a.month - 1]} {a.year}"
            return f"{a.day}–{b.day} {ab[a.month - 1]} {a.year}" if self.lang in ("fr", "es") else f"{ab[a.month - 1]} {a.day}–{b.day}, {a.year}"
        return f"{self.date(a)} – {self.date(b)}"

    def month_label(self, y, m):
        name = MONTHS[self.lang][m - 1]
        return f"{name.capitalize()} {y}"

    def iso_my(self, iso):
        y, m, _ = iso.split("-")
        name = MONTHS[self.lang][int(m) - 1]
        return f"{name} {y}"

    def lower_month(self, s):
        """Minuscules pour un nom de mois en fr/es ; l'allemand et l'anglais gardent la majuscule."""
        return s.lower() if self.lang in ("fr", "es") else s

    def key_my(self, k):
        return self.iso_my(k + "-01")

    def abbr(self, month):
        return ABBR[self.lang][month - 1]


def pick(accept_languages, cookie=None):
    """Langue à servir : cookie, sinon préférence du navigateur, sinon langue de repli."""
    if cookie in LANGS:
        return cookie
    return accept_languages.best_match(list(LANGS), default=FALLBACK)
