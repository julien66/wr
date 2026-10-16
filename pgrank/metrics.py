"""Mesure de la qualité des prévisions."""
import numpy as np


def pairwise_accuracy(mu, ranks):
    """Part des duels (hors ex æquo réels) où le pilote de plus haut mu a fini devant.

    Les égalités de mu comptent pour 0,5. Retourne (précision, nombre de duels).
    """
    mu = np.asarray(mu, float)
    ranks = np.asarray(ranks, float)
    dr = ranks[:, None] - ranks[None, :]          # < 0 : i a fini devant j
    dm = mu[:, None] - mu[None, :]                # > 0 : i était mieux noté
    iu = np.triu_indices(len(mu), 1)
    dr, dm = dr[iu], dm[iu]
    valid = dr != 0
    if not valid.any():
        return float("nan"), 0
    hit = np.where(dm[valid] == 0, 0.5, ((dr[valid] < 0) == (dm[valid] > 0)).astype(float))
    return float(hit.mean()), int(valid.sum())
