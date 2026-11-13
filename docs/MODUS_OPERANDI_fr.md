# Données météo multi-annuelles pour S6 : sources, volumes et mode opératoire

*CIRCEO / HORIZON — préparation du sprint S6 (13 novembre 2026).*

## 1. Ce qu'il faut et pourquoi

| Besoin S6 | Donnée | Durée retenue | Pourquoi cette durée |
|---|---|---|---|
| Structures, variabilité, climatologie, valeurs terminales hors ligne | **ERA5** horaire, vent à 10 m | **30 ans (1995-2025)**, 20 ans au minimum | Une normale climatique fait 30 ans ; la variabilité interannuelle (NAO, blocages) demande 20 à 30 hivers indépendants ; ERA5 est homogène sur l'océan depuis l'ère satellitaire. Au-delà de 30 ans, le gain est faible et la tendance climatique devient un biais. |
| Qualité des prévisions, « modèle figé » | **GEFSv12 reforecast** (NOAA) | **20 ans (2000-2019)**, toute la série | Le même modèle sur 20 ans : les statistiques d'erreur (niveau, structure intensité/position, dépendance au régime et à la saison) sont propres, sans mélange de versions. |
| Qualité des prévisions actuelles, 1re source | **ECMWF IFS HRES** + **IFS ENS** + **AIFS** | **~10 ans (2016-2025)** pour HRES ; 2024-2025 pour ENS et AIFS | Le niveau absolu d'erreur évolue avec le modèle (environ +1 jour de prévisibilité par décennie) : les années récentes comptent plus que les anciennes. |
| Qualité des prévisions actuelles, 2e source | **GEFS opérationnel** (NOAA) | **5 ans (2021-2025)** | Même famille que le reforecast, version opérationnelle actuelle (v12 depuis septembre 2020) : permet de croiser avec ECMWF sur les mêmes dates. |
| Archive continue à partir d'aujourd'hui | ECMWF open data + GEFS | en continu | Le portail ECMWF ne garde que quelques jours ; on archive soi-même chaque jour. |

**Domaine** (fichier `config.yaml`) : **30°N-70°N, 65°O-10°E**. C'est l'extension vers l'ouest et le nord que la S5 a montrée nécessaire (meilleures cellules sur l'ancienne limite à 32°O). On le modifie en un seul endroit.

**Résolutions de stockage.** ERA5 et ECMWF HRES/AIFS à 0,5° (≈ 40-55 km, l'échelle de la grille du routeur) ; GEFS et ECMWF ENS à 1,0° (l'erreur de prévision a une échelle de corrélation de 450 à 650 km, mesurée en S5). Vent u/v à 10 m uniquement, en int16 (précision 0,005 m/s), NetCDF compressé.

## 2. Les sources, vérifiées le 13 novembre 2026

**ECMWF — faut-il un compte payant ?** Pour *accéder* à l'archive complète (MARS), oui : l'accès commercial coûte **3 000 € par an** (1 500 € avec la remise petite entreprise). Mais ce n'est pas nécessaire ici :

- **licence** : les données *historiques* de l'archive sont sous **CC-BY-4.0** depuis l'ouverture de l'archive (2020) ; depuis le **1er octobre 2025**, c'est aussi le cas de **tout le catalogue temps réel**. Toutes les prévisions ECMWF, passées comme présentes, sont donc réutilisables et redistribuables, y compris commercialement, avec attribution. Ce qui reste payant, c'est **l'accès** : l'interface MARS (3 000 € par an) et les services de diffusion à fort volume. Gratuitement, on n'obtient que ce qu'ECMWF met en libre accès (portail open data et ses copies cloud) ou ce que des tiers ont déjà extrait et republient sous CC-BY ;
- un **sous-ensemble gratuit** (dont 10u/10v, IFS, ENS et AIFS à 0,25°) est diffusé en accès libre et répliqué sur AWS (`s3://ecmwf-forecasts`), Azure et Google Cloud ; la profondeur réelle de l'historique sur AWS est à vérifier au premier passage (une source l'indique complète à partir d'avril 2024) — le script le détecte jour par jour ;
- **projets open source qui republient de l'ECMWF multi-annuel** :
  - **WeatherBench 2** (Google) : prévisions **IFS HRES 2016-2022**, 0,25°, 00/12 UTC, échéances 0-10 jours toutes les 6 h, avec le vent à 10 m, en Zarr public. **Licence à confirmer** : WB2 avertit que les données issues de TIGGE (dont son IFS ENS 2018-2022) sont réservées à la recherche ; le HRES vient de l'archive ECMWF (données historiques passées sous CC-BY-4.0), mais seul le fichier LICENSE du bucket fait foi : le script l'affiche et s'arrête tant qu'on ne l'a pas accepté ;
  - **dynamical.org** : archive **IFS ENS 0,25° (51 membres, 15 jours)** en Zarr sur AWS, à partir d'avril 2024, avec un **rapatriement depuis MARS en cours** pour 2016-2024, sous CC-BY-4.0 selon la fiche du jeu (une métadonnée de leur catalogue mentionne une licence non commerciale : à clarifier avec eux avant usage) ;
  - **Planette** : reprévisions ECMWF S2S **2006-2025**, 10 membres, 46 jours, mais à **1,5°** (trop grossier pour le routage ; utile éventuellement pour la valeur terminale à 1-4 semaines).
- À exclure pour un usage commercial : **TIGGE** et la base **S2S** distribuée par ECMWF (licences recherche).

**NOAA — gratuit ?** Oui. Les données NOAA diffusées via le programme NODD sont **ouvertes et utilisables librement** (attribution demandée, pas d'endossement implicite). Le **reforecast GEFSv12** couvre **2000-2019** (00 UTC, 5 membres, 11 membres une fois par semaine) ; le **GEFS opérationnel** est archivé sur AWS depuis **2017** (21 puis 31 membres, 4 runs par jour, 16 jours).

**ERA5** : gratuit, usage commercial autorisé (licence Copernicus). Copie publique **ARCO-ERA5** de Google en Zarr : pas de compte, pas de file d'attente, idéale pour une extraction automatique.

## 3. Volumes (mesurés lors des tests du 13 novembre 2026)

| Jeu | Contenu | Mesure | Volume total |
|---|---|---|---|
| ERA5 0,5° horaire | 1995-2025 | 21 Mo par mois | ≈ 7,8 Go |
| GEFSv12 reforecast 1° | 2000-2019, contrôle + 4, 0-240 h / 6 h | 1,6 Mo par jour | ≈ 12 Go |
| GEFS opérationnel 1° | 2021-2025, 00 UTC, contrôle + 10 | 3,5 Mo par jour | ≈ 6,4 Go |
| ECMWF IFS HRES 0,5° (open data) | depuis 2023 (00 et 12 UTC) | ≈ 2,3 Mo par jour | ≈ 0,85 Go par an |
| ECMWF IFS ENS 1° (open data) | depuis 2023, 00 UTC, contrôle + 20 | 6,2 à 6,6 Mo par jour | ≈ 2,4 Go par an |
| ECMWF AIFS 0,5° (open data) | depuis 2025 (grille régulière) | ≈ 2,3 Mo par jour | ≈ 0,85 Go par an |
| ECMWF HRES 2016-2022 (WB2) | si la licence est validée | — | ≈ 5 Go |
| **Total** | | | **≈ 40 à 45 Go**, puis ≈ 5 Go par an |

**Profondeur réelle de la copie AWS d'ECMWF (testée)** : IFS HRES et IFS ENS disponibles au moins depuis juin 2023 ; AIFS sur grille régulière à partir de 2025 (les fichiers AIFS plus anciens sont sur une grille gaussienne réduite, non pris en charge pour l'instant).

## 4. Où stocker, et comment les agents y accèdent sans vous coûter de jetons

**Constat technique.** L'environnement des agents n'a accès qu'à **GitHub** : AWS, Google Cloud, Copernicus, Hugging Face et Zenodo y sont bloqués (vérifié). Il faut donc que les données arrivent sur GitHub, et que le téléchargement depuis les sources se fasse ailleurs.

**Architecture retenue (gratuite) : un seul dépôt public, `circeo-meteo`.** Les données sont publiques et leurs licences autorisent la redistribution avec attribution (ERA5 : licence Copernicus ; ECMWF : CC-BY-4.0 ; NOAA : données ouvertes), reproduite dans les notes de chaque Release (`docs/release_notes.md`).

1. **Le code** (scripts d'extraction, workflows GitHub Actions) est dans la branche `main`. En dépôt public, les minutes d'Actions sont **illimitées**. Les serveurs de GitHub téléchargent depuis AWS et Google, découpent la zone et écrivent des fichiers compacts.
2. **Les données** sont publiées comme **pièces jointes de Releases du même dépôt** (une Release par jeu, un fichier par an ou par mois), avec le jeton intégré des workflows (`GITHUB_TOKEN`) : **aucun jeton personnel à créer**. GitHub ne limite ni le volume total ni la bande passante des Releases, seulement la taille de chaque fichier (< 2 Gio) — contrairement à Git LFS (10 Gio de stockage et 10 Gio de bande passante par mois en gratuit) et au dépôt git lui-même (< 1-5 Go recommandés). Un dépôt suffit ; inutile d'en faire un par année.
3. **Accès des agents** : une fois `circeo-meteo` rattaché à la session, l'agent télécharge ce dont il a besoin avec `client/fetch_meteo.py` (API REST de GitHub, vérification sha256, cache local ; testé depuis l'environnement des agents). Les données ne transitent jamais par la conversation : **aucun jeton consommé** pour les transferts, seulement pour l'analyse.
4. **Rien de confidentiel n'y entre** : ni polaire, ni résultat de routage, ni donnée client. Les analyses S6 restent dans le dépôt privé du routeur.

Ni votre machine ni votre quota Claude ne sont sollicités pour l'extraction : tout tourne sur GitHub Actions.

## 5. Mode opératoire pas à pas

**Étape 1 — Créer le dépôt (2 min, vous).** `circeo-meteo`, **public**, vide (avec un README d'une ligne pour qu'il ait une branche `main`). Dans Settings → Actions → General → *Workflow permissions*, choisir **Read and write permissions** (pour que les workflows puissent publier les Releases).

**Étape 2 — Rattacher le dépôt à la session et pousser le code (moi).** Dites-moi que le dépôt existe : je le rattache en écriture et je pousse le pipeline. Si la politique réseau m'empêche de pousser les fichiers `.github/workflows`, vous déposez l'archive par l'interface web (glisser-déposer).

**Étape 3 — Tests de fumée (≈ 30 min de calcul, un clic par jeu, vous ou moi).** Onglet *Actions* → *build* → *Run workflow* :

| Jeu | dataset | years | months | extra |
|---|---|---|---|---|
| ERA5 | `era5` | `[2015]` | — | `--months 1` |
| GEFS reforecast | `gefs_reforecast` | `[2015]` | `[1]` | — |
| GEFS opérationnel | `gefs_oper` | `[2023]` | `[1]` | `--days 1` |
| ECMWF HRES (WB2) | `ecmwf_hres_wb2` | `[2019]` | `[1]` | *(vide : le run s'arrête après avoir affiché la licence ; lire la licence, puis relancer avec `--accept-licence` si l'usage commercial est permis)* |
| ECMWF open data | `ecmwf_open` | `[2025]` | `[1]` | `--days 1` |

Je lis les journaux d'exécution via l'API GitHub, je corrige ce qui casse (les points d'accès n'ont pas pu être testés depuis mon environnement) et je compare le fichier ERA5 de janvier 2015 à notre cache actuel (contrôle de cohérence).

**Étape 4 — Extraction complète (≈ 1 à 2 jours de calcul, sans surveillance).** Lancer dans cet ordre :
1. `era5`, years `[1995, …, 2025]` (31 tâches en parallèle, 16 à la fois) ;
2. `gefs_reforecast`, années 2000-2019 par lots de 2 ou 3 ans (une matrice est limitée à 256 tâches) ;
3. `ecmwf_hres_wb2` 2016-2022 (après validation de la licence) ;
4. `gefs_oper` 2021-2025 ;
5. `ecmwf_open` depuis le premier mois disponible.

Les tâches ratées se relancent individuellement (`fail-fast: false`) ; une journée manquante est journalisée sans faire perdre le mois.

**Étape 5 — Agrégation quotidienne automatique (aucune action).** Le workflow `daily` tourne chaque jour à 09 h 17 UTC. Il revérifie les 3 derniers jours, ne télécharge que les runs manquants (ECMWF IFS HRES 00/12 UTC, IFS ENS 00 UTC contrôle + 20 membres, AIFS 00/12 UTC ; GEFS 00 UTC contrôle + 10 membres), les **fusionne dans un fichier par produit et par mois** et republie ces fichiers mensuels dans les Releases `ecmwf-open-uv10-v1` et `gefs-oper-uv10-1p0-v1` — les mêmes que celles de l'extraction historique, donc une série continue. Un jour manqué (panne, publication tardive) est rattrapé le lendemain ; pour combler un trou plus long, lancer `daily` à la main avec `lookback` = 30.

**Étape 6 — Utilisation en S6 (moi).** `client/fetch_meteo.py --list` donne l'inventaire ; les agents téléchargent par motif (`--pattern 'era5_uv10_0p5_20*.nc'`) ; chaque cas S6 est tracé dans MLflow avec l'identifiant de la Release et les sha256 (version des données).

## 6. Coûts et risques

- **Coût : 0 €** (GitHub gratuit, données ouvertes). Option payante non nécessaire : archive MARS ECMWF (3 000 € par an).
- **Licences à respecter** : attribution ECMWF (CC-BY-4.0) et Copernicus dans tout livrable qui utilise ces données ; licence du HRES WB2 à confirmer ; dynamical.org à clarifier avant usage ; pas de TIGGE ni de S2S ECMWF.
- **Risques techniques** : les points d'accès (AWS, GCS) n'ont pas pu être testés depuis mon environnement — d'où les tests de fumée ; le reforecast GEFS est volumineux à télécharger (fichiers globaux contenant toutes les échéances), ce que les minutes illimitées du dépôt public absorbent ; si `gh release download` était bloqué dans l'environnement des agents, repli sur des fichiers mensuels < 100 Mo commités dans une branche `data`.
- **Licence du HRES WeatherBench 2** : tant qu'elle n'est pas confirmée compatible avec la redistribution, ce jeu n'est pas publié (le workflow s'arrête avant).
