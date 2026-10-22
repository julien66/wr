# Déployer sur Render

La base `ranking.db` n'est pas envoyée : Render la **calcule pendant le déploiement** (≈ 1 minute) à partir de `data/civl_data.db`
(7 Mo, la base des résultats CIVL). Pas de disque à payer, pas de gros fichier à transférer.

## 1. Mettre le projet sur GitHub (dépôt **privé**)

Le dépôt contient les résultats nominatifs et la liste des genres : gardez-le privé.

```bash
cd "/Users/julien/Documents/World Ranking/ranking"
git init -b main
git add .
git commit -m "Classement mondial de parapente"
```
Sur github.com : *New repository* → nom `classement-parapente` → **Private** → créer (sans README). Puis :
```bash
git remote add origin https://github.com/<votre-compte>/classement-parapente.git
git push -u origin main
```

## 2. Créer le service sur Render

1. render.com → *New* → **Blueprint** → connecter GitHub → choisir le dépôt. Render lit `render.yaml`.
2. Le plan est **Starter** (≈ 7 $/mois, site toujours actif), déjà réglé dans `render.yaml`.
3. Le premier déploiement dure quelques minutes (installation, calcul de la base). Quand le statut est *Live*, l'adresse `https://classement-parapente-xxxx.onrender.com` fonctionne.

## 3. Avant d'ouvrir au public

- `editor_name` et `host_info` sont renseignés dans `pgrank/config.py`. Compléter `host_info` avec l'adresse postale de Render (à copier depuis render.com) : la loi demande le nom, l'adresse et le contact de l'hébergeur.
- Vérifier la limitation de débit : le site lit l'adresse du visiteur dans `X-Forwarded-For` (`RANKING_PROXY=1`).
- Nom de domaine : *Settings → Custom Domains* dans Render, puis ajouter l'enregistrement DNS indiqué (HTTPS automatique).

## 4. Mettre à jour les résultats

Chaque mois, après la publication du classement WPRS (le 1er) :
```bash
python update.py
git add data && git commit -m "Mise à jour des résultats" && git push
```
`update.py` ajoute à `data/civl_data.db` les compétitions manquantes (voir le README). Render redéploie seul et recalcule le classement.

## 5. Retirer un pilote

Ajouter son numéro CIVL à `data/removals.csv`, puis `git commit` et `git push`.

## Remarques

- Hébergement aux États-Unis par défaut pour la société, mais le service tourne à Francfort (`region: frankfurt`).
- Si Render signale un manque de mémoire au build, passer à *Starter*.
