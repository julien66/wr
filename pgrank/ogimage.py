"""Image d'aperçu (1200 × 630) d'une page pilote, pour le partage sur les réseaux sociaux et messageries."""
import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
BG, PAPER, INK, MUTED, RULE, ACCENT, SOFT = "#F1F4F8", "#FFFFFF", "#0F1B2D", "#58657A", "#D6DDE7", "#3A3FD6", "#BFC2F3"
_FONTS = {
    True: ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
           "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"],
    False: ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf", "C:/Windows/Fonts/arial.ttf"],
}


@lru_cache(maxsize=None)
def font(size, bold=False):
    for f in _FONTS[bold]:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size=size)       # police intégrée à Pillow (≥ 10.1)


def _fit(draw, text, size, bold, width):
    """Réduit la taille de police jusqu'à ce que le texte tienne dans la largeur."""
    while size > 28 and draw.textlength(text, font=font(size, bold)) > width:
        size -= 4
    return font(size, bold)


def pilot_card(site_title, name, nation, stats, series):
    """stats : liste de (libellé, valeur, précision) ; series : ratings successifs (courbe en bas de l'image)."""
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    pad = 64
    # marque du site : barre de niveau avec son repère
    d.rounded_rectangle((pad, 58, pad + 52, 78), radius=10, fill=SOFT)
    d.rounded_rectangle((pad, 58, pad + 29, 78), radius=10, fill=ACCENT)
    d.ellipse((pad + 15, 54, pad + 43, 82), fill=PAPER, outline=ACCENT, width=6)
    d.text((pad + 70, 68), site_title, font=font(30, True), fill=INK, anchor="lm")
    # nom et nation
    f = _fit(d, name, 84, True, W - 2 * pad - 140)
    d.text((pad, 190), name, font=f, fill=INK, anchor="ls")
    d.text((pad + d.textlength(name, font=f) + 24, 190), nation or "", font=font(36), fill=MUTED, anchor="ls")
    # chiffres clés
    n = max(1, len(stats))
    bw = (W - 2 * pad) / n
    for i, (label, value, sub) in enumerate(stats):
        x0 = pad + i * bw
        d.rounded_rectangle((x0, 230, x0 + bw - 16, 400), radius=12, fill=PAPER, outline=RULE, width=2)
        d.text((x0 + 26, 270), label, font=font(26), fill=MUTED, anchor="ls")
        d.text((x0 + 26, 345), value, font=_fit(d, value, 64, True, bw - 60), fill=INK, anchor="ls")
        if sub:
            d.text((x0 + 26, 382), sub, font=font(22), fill=MUTED, anchor="ls")
    # courbe du niveau
    s = [v for v in series if v is not None]
    if len(s) >= 2:
        lo, hi = min(s), max(s)
        hi = hi if hi > lo else lo + 1
        x0, x1, y0, y1 = pad, W - pad, 440, H - 50
        pts = [(x0 + (x1 - x0) * i / (len(s) - 1), y1 - (y1 - y0) * (v - lo) / (hi - lo)) for i, v in enumerate(s)]
        d.line([(x0, y1), (x1, y1)], fill=RULE, width=2)
        d.polygon(pts + [(x1, y1), (x0, y1)], fill="#E1E3FA")
        d.line(pts, fill=ACCENT, width=5, joint="curve")
        d.ellipse((pts[-1][0] - 9, pts[-1][1] - 9, pts[-1][0] + 9, pts[-1][1] + 9), fill=ACCENT, outline=PAPER, width=3)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()
