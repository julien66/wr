"""Tous les réglages du classement au même endroit."""
from dataclasses import dataclass, field


@dataclass
class Config:
    # --- Moteur : "elo" (défaut) ou "openskill" ---
    engine: str = "elo"
    # Elo multijoueur : chaque pilote est comparé à tous les autres de la compétition (1 = devant, 0,5 = ex æquo).
    # k : amplitude des mises à jour ; boost : K multiplié par (1 + boost·e^(−n/10)) pour les pilotes peu évalués ;
    # scale : 400 = échelle d'Elo classique (400 points d'écart = 10 chances contre 1). Ici 640 = 400 × 1,6 avec K × 1,6 :
    # même classement que (400, 64), mais étiré pour que les meilleurs approchent 2800 comme aux échecs.
    elo_params: dict = field(default_factory=lambda: {"k": 102.4, "boost": 4.0, "scale": 640.0})
    elo_idle_half_life: float = 18.0   # après le seuil d'inactivité, le nombre de compétitions « effectif » est divisé par 2 tous les N mois
    # Incertitude affichée (Elo, ± 1 écart type) : σ² = floor² + new²/(n + c) + drift² × mois sans compétition,
    # n = nombre de compétitions « effectif ». Valeurs calées par simulation (voir README, « Incertitude »).
    elo_sd: dict = field(default_factory=lambda: {"floor": 115.0, "new": 215.0, "c": 0.5, "drift": 20.0})
    # --- Modèle OpenSkill (si engine="openskill") ---
    # Paramètres passés tels quels à openskill.models.<model>(**model_params).
    # Vide = valeurs par défaut de la bibliothèque : mu=25, sigma=25/3, beta=25/6, kappa=1e-4, tau=25/300,
    # window_size=4 par défaut ; ici 10 (chaque pilote est comparé aux 10 classés juste devant et juste derrière) : meilleure prévision, voir README.
    model: str = "BradleyTerryPart"   # classe de openskill.models (BradleyTerryPart, PlackettLuce, ...)
    model_params: dict = field(default_factory=lambda: {"window_size": 10, "tau": 0.5})
    use_scores: bool = False          # passe les scores (normalisés à 1000 pour le 1er) à OpenSkill : l'écart de points compte (avec model_params margin>0)
    z: float = None                    # rating affiché = ordinal d'OpenSkill = mu - z * sigma

    # --- Sélection des compétitions ---
    # Compétitions écartées à la main : {identifiant CIVL: raison affichée sur la page de la compétition}
    excluded: dict = field(default_factory=lambda: {
        8703: "résultats jugés non fiables (scores de 4 à 8 points, hors barème habituel)",   # 1st Korea Open Paragliding Championships 2026
    })
    min_field: int = 5                # compétitions de moins de N pilotes : ignorées
    include_zero_scores: bool = True  # pilote à 0 point : classé ex æquo en dernier

    # --- Inactivité ---
    # Au-delà de ce délai sans compétition, le pilote est « inactif » (grisé) : son niveau (mu) est
    # conservé, mais son incertitude gonfle avant sa prochaine évaluation, à raison de idle_tau²
    # par mois d'absence au-delà du seuil (plafonnée au sigma initial du modèle).
    inactive_after_months: float = 18.0
    idle_tau: float = 1.3             # ≈ retour au sigma initial en ~3 ans au-delà du seuil

    # --- Publication ---
    contact_email: str = "j.garcia@ffvl.fr"   # affiché sur la page Mentions légales (droits d'accès, de retrait)
    editor_name: str = "Julien Garcia"              # nom de l'éditeur (mentions légales) ; vide = ligne non affichée
    host_info: str = "Render (render.com), 525 Brannan Street, Suite 300, San Francisco, CA 94107, États-Unis ; serveurs à Francfort (Allemagne)"                # hébergeur : nom et adresse ; vide = ligne non affichée
    top_n: int = 300                  # engine.run sans base (tests, outils) : pilotes gardés par mois ancien
    full_months: int = 3              # engine.run sans base : les N derniers mois gardent tous les pilotes
    pilot_min_comps: int = 3          # une page pilote à partir de N compétitions (ou si présent dans un classement affiché)
    site_title: str = "Classement mondial de parapente"
    lang: str = "fr"

    def __post_init__(self):
        if self.z is None:              # Elo : pas d'incertitude σ, rating = niveau ; OpenSkill : rating = μ − 2σ
            self.z = 0.0 if self.engine == "elo" else 2.0
