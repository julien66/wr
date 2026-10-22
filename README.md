# Classement mondial de parapente (Elo)

Génère un site statique à partir de la base SQLite des résultats CIVL :

- un **classement par mois** (état des niveaux à la fin de chaque mois) ;
- une **page par compétition** qui montre, pilote par pilote, le calcul : rang réel, rang attendu, niveau avant et après, écart ;
- une **page par pilote** : rang mondial, victoires/top 10/place médiane, **graphique de l'évolution du niveau compétition par compétition** et tableau des résultats (pilotes avec au moins `pilot_min_comps` = 3 compétitions) ;
- les pilotes inactifs (plus de 18 mois sans compétition) **sortent du classement** : ils n'ont pas de rang et ne prennent la place de personne ; avec « Afficher les inactifs » ils apparaissent grisés et sans numéro à la place que leur niveau leur donnerait ; ils redeviennent « provisoires » (K relevé) à leur retour.

## Moteur par défaut : Elo multijoueur

Chaque compétition est un ensemble de duels : chaque pilote est comparé à tous les autres (1 = devant, 0,5 = ex æquo, 0 = derrière).
Pour un duel entre niveaux A et B, la probabilité que A gagne est `1 / (1 + 10^((B − A)/640))`. Le niveau d'un pilote varie de
`K × (score obtenu − score attendu)` (moyenne sur ses duels). Battre des pilotes faibles rapporte peu ; battre des pilotes forts rapporte
beaucoup ; perdre contre des pilotes faibles coûte cher. `K = 64 × (1 + 4·e^(−n/10))` : les nouveaux pilotes bougent vite.
Après une longue absence (> 18 mois), le nombre de compétitions « effectif » n est divisé par 2 tous les 18 mois, ce qui relève K.
C'est du calcul maison (une vingtaine de lignes dans `pgrank/engine.py`, vérifié par un test contre un calcul à la main) ; il ne nécessite pas OpenSkill.

Choix des réglages (K, boost) sur les mêmes données — part des duels bien prédits entre pilotes ayant ≥ 3 compétitions :

| Moteur | Duels bien prédits |
|---|---|
| OpenSkill BradleyTerryPart (fenêtre 10, τ 0,5) | 71,4 % |
| Elo K = 16 | 71,4 % |
| Elo K = 32, boost 2 | 72,9 % |
| **Elo K = 64, boost 4 (retenu)** | **74,3 %** |
| Elo K = 128, boost 4 | 74,4 % (plus réactif, favorise trop les pilotes récents) |

L'échelle (640, soit 400 × 1,6 avec K = 64 × 1,6 : mêmes classements qu'en (400, 64) mais étirés pour que les meilleurs approchent 2800) est une simple unité : (échelle 300, K 48) donne exactement le même classement que (400, 64) ; seul le rapport K / échelle compte. La resserrer ne rend donc pas les favoris plus « sûrs » à elle seule ; avec K/échelle plus grand, le rang attendu de Lambert à Gemona passe de 22 à 19–20 sans gain de prévision (74,3 → 74,4 %).
Les pages de compétition montrent, par pilote, la part de duels gagnés et la part attendue (rang attendu au survol).

Options : `python build.py ... --k 48 --boost 2`. L'ancien moteur reste disponible : `--engine openskill` (nécessite `pip install "openskill>=6.1.1"`, Python ≥ 3.10).

## Incertitude (± affiché à côté du rating)

Le moteur Elo ne calcule pas d'incertitude ; elle est ajoutée à l'affichage, sans changer le classement. Pour chaque pilote :
`σ = √(115² + 215²/(n + 0,5) + 20² × t)`, avec n le nombre de compétitions « effectif » (celui qui sert à K) et t les mois depuis la
dernière compétition : ±210 après une compétition, ±133 après dix, jamais moins de ±115. Le rang probable est le rang qu'aurait le pilote
avec rating − σ et rating + σ, les autres restant en place (actifs seulement ; colonnes `sd`, `rank_lo`, `rank_hi` de `monthly`).

Calage par simulation : des pilotes fictifs au niveau connu (niveau de départ = rating final réel, puis marche aléatoire de 20 points par mois)
courent exactement les 2 241 compétitions de la base ; chaque résultat = niveau + bruit normal de 290 points. Ce réglage reproduit les
74,9 % de duels bien prédits des vraies données et l'écart type des variations de rating sur 12 mois des vétérans (44 points simulés,
46 réels). On mesure ensuite l'écart entre le rating calculé et le vrai niveau, et on ajuste la formule (erreur relative < 5 %).
Le plancher de ±115 vient du K élevé (102) : même un vétéran bouge beaucoup à chaque compétition. Réglages : `elo_sd` dans `pgrank/config.py`.

## Utilisation

```bash
pip install -r requirements.txt      # (requirements-openskill.txt pour --engine openskill)
python build.py --db civl_data.db --out site      # ~30 s pour 1 900 compétitions
```

Ouvrez `site/index.html`, ou publiez le dossier `site/` sur n'importe quel hébergement statique.

**Mise à jour mensuelle** : mettez à jour `civl_data.db` (votre collecte), relancez `build.py`, publiez `site/`.
Tout est recalculé depuis zéro (déterministe) : une compétition ajoutée avec retard se répercute
correctement sur les mois suivants.

## Règles communes aux deux moteurs

- **Inactivité** : au-delà de `inactive_after_months` (18) sans compétition, le pilote sort du classement (pas de rang, grisé s'il est affiché) ; avant son
  prochain calcul, σ est relevé de `idle_tau`² par mois au-delà du seuil (plafonné au σ initial du modèle).
- **Sélection** : compétitions de moins de `min_field` (5) pilotes ignorées ; un pilote à 0 point est classé
  ex æquo dernier.
- **Nettoyage des données** (`pgrank/load.py`) : dates `Apr 01, 2010`, libellé « Sport » collé aux noms
  (10 604 lignes), lignes sans `civlid` (7) ignorées. Les pilotes sont identifiés par `civlid`.

Réglages dans `pgrank/config.py` ; options de `build.py` : `--model`, `--window`, `--tau`, `--z`, `--top-n`, `--inactive-months`, `--min-field`.

## Pourquoi BradleyTerryPart et non PlackettLuce par défaut

Constaté sur les données CIVL (plateaux de 50 à 150 pilotes) :

- σ reste proche de sa valeur initiale (médiane 8,31 pour 8,33 chez les pilotes à 30 compétitions ou plus) :
  la relance de σ après inactivité a donc très peu d'effet visible ;
- la mise à jour dépend surtout de la place d'arrivée, peu de l'écart avec le rang attendu (sur un plateau de
  130, le 1ᵉʳ attendu 32ᵉ et le 6ᵉ attendu 92ᵉ gagnent tous deux +0,6) ; μ monte à chaque bon résultat, et le
  rating favorise les pilotes qui ont couru beaucoup de compétitions.

Autres modèles OpenSkill essayés avec le même moteur (`--model`), sur les mêmes données :

| Modèle | Duels bien prédits | Entre les 30 meilleurs | σ médian (vétérans) | Échelle de μ (top 300) |
|---|---|---|---|---|
| PlackettLuce | 69,5 % | 63,8 % | 8,31 | 40 à 98 |
| BradleyTerryPart | 69,5 % | 65,8 % | 3,72 | 25 à 38 |
| ThurstoneMostellerPart | 68,9 % | 65,5 % | 5,80 | 25 à 44 |
| BradleyTerryFull | 65,2 % | 61,2 % | 0,56 | 191 à 560 |
| ThurstoneMostellerFull | 62,0 % | 58,5 % | 0,47 | diverge |

Les variantes `Full` sont à écarter (elles divergent sur de grands plateaux). `BradleyTerryPart` prédit aussi
bien, mieux entre élites, rend σ significatif (donc l'inactivité utile) et ne récompense plus le volume de
compétitions : c'est le défaut. Pour retrouver l'ancien classement : `python build.py ... --model PlackettLuce`.

## Contrôles

```bash
python tests/test_engine.py    # moteur : inactivité, statuts mensuels, ex æquo, égalité avec des appels directs à openskill
python tests/test_wprs.py      # comparaison WPRS : choix du classement du mois, pilotes sans points
```

## Comparaison avec le WPRS officiel

Les classements WPRS mensuels publiés par la CIVL sont dans `data/wprs/` : un fichier `AAAA-MM.csv.gz` par mois
(classement mondial complet : rang, CIVL ID, nom, sexe, nation, points), de novembre 2023 à octobre 2026, et
`rankings.csv` (identifiant civlcomps de chaque mois publié depuis mars 2007).

```bash
pip install -r requirements-wprs.txt
python tools/fetch_wprs.py --since 2023-11     # télécharge les mois manquants (export Excel de civlcomps.org)
python tools/compare_wprs.py --since 2023-12 --until 2026-09 --csv comparaison.csv
```

Le téléchargement fait une demande à la fois et attend 20 s entre deux mois pour ménager le site.
Pour chaque épreuve, la comparaison prend le dernier classement publié au plus tard le jour du début de l'épreuve (au plus 45 jours avant),
et le rating Elo juste avant l'épreuve ; un pilote absent du WPRS a 0 point. Résultat (458 épreuves, décembre 2023 à septembre 2026) :

| Duels | Elo | WPRS | Écart | Duels comptés | Épreuves où l'Elo fait mieux |
|---|---|---|---|---|---|
| Pilotes ≥ 3 épreuves | 76,1 % | 72,5 % | +3,6 pt | 1 067 485 | 365 / 458 |
| Tous | 74,8 % | 72,3 % | +2,5 pt | 1 490 006 | 321 / 458 |
| Classés WPRS et ≥ 3 épreuves | 76,1 % | 72,5 % | +3,5 pt | 1 040 168 | 360 / 457 |
| 30 meilleurs de l'épreuve (≥ 10 épreuves) | 67,4 % | 65,0 % | +2,4 pt | 132 862 | 269 / 444 |

La dernière ligne ne garde que les pilotes qui ont des points WPRS : l'écart ne vient pas des pilotes non classés.
En moyenne, 13,6 % des pilotes d'une épreuve n'ont aucun point WPRS.

## Exports

`site/exports/calculs_par_competition.csv` (chaque calcul : μ, σ avant et après, rang attendu...) et
`site/exports/classement_mensuel.csv`.

## Réglages retenus : fenêtre 10 et rating = μ − 2σ

Testés sur les mêmes données (BradleyTerryPart) :

| Réglage | Duels bien prédits | Entre les 30 meilleurs | Compét. moyennes du top 20 |
|---|---|---|---|
| fenêtre 4, μ − 3σ (défaut d'OpenSkill) | 69,5 % | 65,8 % | 72 |
| fenêtre 10, μ − 3σ | 71,4 % | 66,6 % | 68 |
| **fenêtre 10, μ − 2σ (retenu)** | 71,4 % | 66,6 % | 60 |
| fenêtre 25, μ − 3σ | 72,5 % | 67,1 % | 68 (mais moins de victoires dans le top 20) |

La fenêtre agit sur la prévision (μ) ; z agit seulement sur l'affichage : plus z est grand, plus la baisse de σ
(qui vient avec chaque compétition courue) pèse dans le rating, donc plus le volume est récompensé.
`--window 4 --tau 0.0833 --z 3` redonne le classement par défaut d'OpenSkill.

## Pourquoi tau = 0,5 : les vétérans doivent rester réactifs

OpenSkill ajoute τ² à σ² avant chaque compétition. Avec τ = 25/300 (défaut), σ d'un vétéran se bloque vers 2,3
et la mise à jour devient minuscule : Andreas Malecki, 113ᵉ à Gemona (attendu 31ᵉ) puis 110ᵉ à l'Ikarus Open,
ne perdait que 0,06 et 0,13 de μ. Avec τ = 0,5, σ se stabilise vers 3,8, il perd 0,2 et 0,34 et passe du 35ᵉ au 80ᵉ rang
mondial après ses résultats récents. La prévision reste la même (duels bien prédits : 71,4 % → 71,4 %, entre les
30 meilleurs : 66,6 % → 66,7 %), donc ce réglage change la réactivité sans dégrader le modèle.

## Site dynamique (base SQLite + serveur)

Le site statique (`build.py`) reste disponible. Pour interroger n'importe quel classement (« le 600ᵉ en 2007 »), chercher un pilote
absent de la page, ou filtrer les femmes, il y a une version dynamique :

    pip install flask
    python build_db.py --db civl_data.db --out ranking.db     # calcule et écrit la base (~1 min, ~400 Mo)
    python app.py --db ranking.db                              # http://127.0.0.1:5000

- Classement d'un mois, paginé par 100 : `/ranking/2007-12.html`, `/ranking/2007-12-p6.html`.
- Saut direct à un rang : `/ranking/2007-12.html?rank=600` (formulaire « Aller au rang » sur chaque page). `/ranking/2007` ouvre le dernier mois de l'année.
- Recherche sur tous les pilotes et toutes les compétitions (sans accents) : `/search?q=garcia`.
- Femmes : onglet « Féminin » (`?sex=F`) sur les classements. Le genre vient de `data/genders.csv` (`civlid,gender`), extrait de la liste CIVL ;
  `build_db.py --genders` accepte aussi directement un .xlsx de la liste CIVL. Les pilotes dont le genre n'est pas connu n'apparaissent pas dans l'onglet. Pour en ajouter, écrire une ligne `civlid,F` (ou `M`) dans `data/genders_manual.csv`, qui est prioritaire, puis relancer `build_db.py`.
  Le niveau Elo est celui du classement général : le rang « féminin » est le rang de la pilote parmi les pilotes féminines.
- Après chaque ajout de compétitions : relancer `build_db.py`, puis redémarrer `app.py`.

## Langues

Le site dynamique choisit la langue du navigateur (en-tête `Accept-Language`) et la mémorise dans un cookie ; les liens « Français / English / Deutsch / Español » en pied de page
(`?lang=en`) la changent à la main. Langues disponibles : français (texte source), anglais, allemand et espagnol. Quand la langue du navigateur n'est pas disponible, c'est l'anglais.

- Les textes sont les textes français écrits dans les gabarits : `_("…")` pour un texte simple, `_h("…")` pour un texte avec HTML, `_n("singulier", "pluriel", n)`.
  Les traductions sont dans `pgrank/i18n/<langue>.json` (clé = texte français). Une clé absente retombe sur le français.
- Les textes du JavaScript sont les clés `js.*` du même fichier. Les formats de nombres et de dates dépendent de la langue (`pgrank/i18n.py`).
- Ajouter une langue : copier `pgrank/i18n/en.json` en `<code>.json`, le traduire, puis ajouter le code dans `LANGS` (`pgrank/i18n.py`).
- Le site statique (`build.py`) reste en français ; la page Méthode n'est traduite que pour le moteur Elo.

## Mise en ligne

- **Serveur de production** : `pip install -r requirements-dyn.txt`, puis
  `RANKING_DB=/chemin/ranking.db RANKING_PROXY=1 gunicorn -w 3 -b 127.0.0.1:8000 wsgi:application`, derrière Caddy ou nginx (HTTPS).
  `RANKING_PROXY=1` (ou `--proxy`) à n'activer que derrière un reverse proxy de confiance : l'adresse du visiteur est lue dans `X-Forwarded-For`.
- **Mise à jour** : régénérer `ranking.db` en local (`build_db.py`), la copier sur le serveur, redémarrer gunicorn.
- **Cache** : les pages sont servies avec `Cache-Control: private` (la langue dépend d'un cookie) ; ne pas les mettre en cache partagé (CDN) sans
  exclure le cookie `lang` de la clé de cache, sinon une langue serait servie à tous. Les fichiers `/static/` sont versionnés et peuvent l'être.
- **Protections** : en-têtes de sécurité (CSP, nosniff, anti-framing), limitation de débit par adresse (400 requêtes/min, 120 pour la recherche, par processus),
  `/_debug` supprimé, base ouverte en lecture seule. Pages pilotes, recherche et API : `noindex`. `robots.txt` et `sitemap.xml` sont générés.
- **Aucune ressource tierce** : plus de polices Google (police système de repli). Pour retrouver Archivo et Source Serif 4, les télécharger
  (fontsource, Google Fonts) et les servir depuis `pgrank/static/` avec des `@font-face`.
- **Mentions légales** : page `/legal.html` (4 langues). Renseigner `editor_name` et `host_info` dans `pgrank/config.py` (affichés s'ils sont remplis) ;
  `contact_email` est déjà réglé.
- **Retrait d'un pilote** (droit d'effacement / d'opposition) : ajouter son numéro CIVL à `data/removals.csv`, puis relancer `build_db.py`.
  Son nom, sa nation et son genre disparaissent, sa page devient introuvable ; sa ligne reste anonyme (« — ») pour garder les rangs intacts.
- Déploiement sur Render : voir `DEPLOY_RENDER.md`.

## Licence

Le code de ce projet est un logiciel libre, distribué sous la licence **GNU Affero General Public License v3.0** (AGPL-3.0) : voir le fichier [`LICENSE`](LICENSE).
Vous pouvez l'utiliser, l'étudier, le modifier et le redistribuer. Si vous mettez en ligne une version modifiée (site web ou API),
vous devez en publier le code source sous la même licence.

Les résultats de compétition (`data/civl_data.db`) proviennent de la CIVL (FAI) et ne sont pas couverts par cette licence.
