# facerec : reconnaissance faciale fiable et rapide sur webcam

> **État du projet : prototype.** La porte est simulée et le système n'a pas été audité par un tiers. Les mesures d'attaques en conditions réelles (`docs/PROTOCOLE-ATTAQUES.md`) portent encore sur très peu d'essais : elles ne permettent pas de garantir la sécurité. La vidéo rejouée reste l'attaque la moins bien maîtrisée.

## Problématique

> Comment identifier de façon fiable des personnes sur un flux webcam ordinaire, en refusant les inconnus, **sans conserver aucune photo**, en **temps réel sur un simple processeur**, et en sachant mesurer honnêtement ce qui améliore vraiment les résultats ?

Un prototype qui « marche à 80 % » ne dit pas *pourquoi* il se trompe, ni quels choix comptent. Ici, chaque choix de conception est **testé séparément** (ablation) et validé sur des personnes qui n'ont pas servi à le régler.

## Résultats en bref

| | Version 1 (dlib) | Version 2 (YuNet + SFace) |
|---|---|---|
| Erreur d'identification (EER, personnes jamais vues) | 3,1 % | **1,1 %** |
| Temps par image (640×480, CPU) | 444 ms (≈ 2 img/s) | **25 ms (≈ 40 img/s)** |
| Rapport | | **~18× plus rapide** |

Conditions : sous-ensemble public LFW, 3 images d'inscription par personne, inconnus jamais inscrits, moyenne de 5 tirages ([détails](results/)). Le temps réel est mesuré, pas estimé : le goulot de la v1 était l'empreinte dlib (400 ms), remplacée par SFace (11 ms).

## Principe

```
webcam ─► détection (YuNet) ─► 68 points ─► filtre qualité ─► empreinte 128-d (SFace)
                                   │                                  │
                                   └─► clignement (anti-photo)        ▼
                                                      comparaison à la galerie (ensemble ouvert)
                                                                      │
                                    suivi du visage ─► vote sur 7 images ─► accès / refus + journal
```

| Brique | Rôle | Fichier |
|---|---|---|
| Détection + empreinte | YuNet / HOG pour détecter, SFace / dlib ResNet pour l'empreinte (au choix) | `engine.py` |
| Filtre qualité | écarte flou, trop sombre/clair, trop petit, de profil, coupé | `quality.py` |
| Galerie | stocke **uniquement des empreintes** ; refuse l'inconnu (seuil) et l'ambigu (marge) ; mémorise le modèle utilisé | `gallery.py` |
| Vote temporel | décision par majorité sur les dernières images | `smoothing.py`, `tracking.py` |
| Anti-photo | détection de clignement (EAR) | `liveness.py` |
| Anti-usurpation | défi aléatoire + détection d'écran (moiré) | `antispoof.py` |
| Évaluation | protocole open-set FNIR / FPIR, ablation, vitesse | `evaluation.py`, `scripts/` |

## Installation

Python ≥ 3.10 (testé sur 3.13, Windows).

```bash
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS : source .venv/bin/activate
pip install -e ".[dev]"
python scripts/download_models.py     # modèles OpenCV Zoo, vérifiés par SHA-256
```

`dlib-bin` fournit dlib précompilé : aucun compilateur C++ n'est nécessaire.

## Utilisation

```bash
facerec cameras                 # quelles caméras fonctionnent
facerec enroll Alice            # 20 empreintes, consentement demandé, aucune photo gardée
facerec run                     # reconnaissance en direct (q pour quitter)
facerec run --liveness --log logs/acces.csv    # exige un clignement, journalise les décisions
facerec run --challenge --log logs/acces.csv   # défi aléatoire (clignement / tourner la tête) + détection d'écran
facerec run --challenge --door sim --policy config/policy.example.json --zone lab   # porte simulée + droits
facerec run --challenge --door gpio --gpio-pin 17 --zone lab   # vraie gâche sur Raspberry Pi (non testé sur matériel)
facerec list
facerec delete Alice            # droit à l'effacement
```

Une galerie est liée à un modèle d'empreinte : `facerec` refuse de mélanger des empreintes de modèles différents. `--detector hog|yunet` et `--embedder dlib|sface` permettent de comparer.

## Évaluation

### Protocole (open-set)

Les identités sont séparées en deux : des personnes **connues** (inscrites avec *n* images, le reste sert de tests) et des **inconnues** (jamais inscrites, toutes leurs images servent d'imposteurs). Un test est correct seulement s'il est accepté **et** attribué à la bonne personne.

- **FNIR** : part des personnes connues non identifiées correctement.
- **FPIR** : part des inconnus acceptés à tort.
- **EER** : point où les deux erreurs sont égales (plus bas = mieux).

Les seuils ont été réglés sur 80 personnes (`data/lfw`) et **validés sur 78 autres** (`data/lfw_val`).

### Comparaison des modèles (EER, 3 images d'inscription, avec filtre qualité)

| Détecteur + empreinte | Réglage | Validation | Temps / image |
|---|---|---|---|
| HOG + dlib (v1) | 3,4 % | 3,0 % | 444 ms |
| YuNet + dlib | 4,5 % | 3,2 % | 418 ms |
| HOG + SFace | 0,7 % | **0,5 %** | 53 ms |
| **YuNet + SFace (défaut)** | 1,2 % | 1,0 % | **25 ms** |

HOG + SFace est un peu plus précis, mais deux fois plus lent. YuNet + SFace est le compromis retenu : il détecte 100 % des visages (HOG en manque 4) et rejette moins d'images. Le choix se change avec `--detector hog`.
Voir [`results/speed.md`](results/speed.md), [`results/yunet_sface/`](results/yunet_sface/), [`results/hog_dlib/`](results/hog_dlib/).

### Ce qui améliore vraiment la fiabilité (ablation, YuNet + SFace, validation)

| Configuration | EER | FNIR à FPIR ≤ 1 % |
|---|---|---|
| 1 image d'inscription | 2,1 ± 0,8 % | 2,7 ± 1,2 % |
| 3 images | 1,1 ± 0,4 % | 1,4 ± 0,7 % |
| 8 images | 0,7 ± 0,2 % | 0,8 ± 0,3 % |
| + filtre qualité | 0,6 ± 0,3 % | 0,5 ± 0,3 % |

1. **Le nombre d'images d'inscription est le levier principal** avec les deux modèles (dlib : 6,5 % → 2,5 % de 1 à 8 images). C'est pourquoi `facerec enroll` prend 20 empreintes.
2. **Le filtre qualité aide peu sur des images fixes** et sa marge d'erreur recouvre le gain, alors qu'il écarte ~21 % des images. En vidéo, une image écartée est remplacée par la suivante ; son effet réel reste à mesurer sur des séquences webcam.
3. **Le flou et la petite taille expliquent plus d'erreurs que la pose** ([`failure_analysis.md`](results/hog_dlib/failure_analysis.md)) : le seuil de pose, trop strict au départ, a été assoupli et le seuil de flou relevé.

### Anti-usurpation (`--challenge`)

Deux couches, car aucune ne suffit seule ([`antispoof.py`](src/facerec/antispoof.py)) :

1. **Défi aléatoire actif** : après l'identification, la personne doit exécuter 3 actions tirées au hasard (par défaut 3 rotations de la tête, gauche ou droite de l'écran, répétitions possibles : 8 séquences ; le clignement est disponible via `challenge_types` mais désactivé car peu fiable en test réel), avec retour de face entre deux rotations et 8 s maximum par action (compte à rebours affiché ; la raison d'un refus est écrite dans le journal). Une photo ne bouge pas ; une vidéo préenregistrée ne peut pas deviner l'ordre. Les mouvements sont jugés **par rapport à la pose de repos de la personne** (mesurée sur ~10 images avant le défi) : une caméra décentrée lit un visage de face à +0,45 au lieu de 0 (observé en test réel), et un clignement est une fermeture à 75 % de l'ouverture des yeux de la personne.
2. **Détection passive d'écran** : énergie périodique haute fréquence (moiré de la grille de pixels) dans le visage, lissée sur 10 images.

Mesure ([`results/antispoof.md`](results/antispoof.md), `scripts/antispoof_eval.py`) : seuil réglé pour ~1 % de faux rejets sur de vrais visages LFW ; sur des **attaques écran simulées**, 100 % (grille visible), 99 % (discrète) mais **seulement 59 %** pour un écran haute définition. La détection passive est donc un filet, pas une protection ; le défi aléatoire est la défense principale.

Ce que ça ne couvre pas : un attaquant en direct (deepfake temps réel, masque 3D animé) ou une vidéo qui réagit au défi. Les attaques sont **simulées** : il faut les re-mesurer avec de vrais enregistrements avant de parler de sécurité.

### Porte et droits d'accès

La reconnaissance dit *qui* ; la politique ([`policy.py`](src/facerec/policy.py), exemple `config/policy.example.json`) dit si cette personne peut ouvrir *cette* porte *maintenant* (zone, jours, horaires, fenêtres de nuit). **Refus par défaut** : une personne, une zone ou un horaire non listé est refusé, et le journal indique `policy:not_authorized` ou `policy:outside_schedule`.

La porte ([`door.py`](src/facerec/door.py)) s'ouvre 5 s après un accord, puis se referme ; elle se referme aussi si le programme s'arrête (jamais de gâche laissée alimentée). `--door sim` affiche « PORTE OUVERTE / FERMEE » à l'écran ; `--door gpio` pilote un relais Raspberry Pi avec la même logique. Le pilote GPIO est testé avec un faux `gpiozero`, **pas sur du matériel réel**.

### Application web d'administration (FaceGate)

```bash
pip install -e ".[web]"
facerec admin create NOM                       # mot de passe demandé (12 caractères, majuscules, chiffre)
facerec import-gallery                         # copie les personnes de la galerie dans la base
facerec web                                    # http://127.0.0.1:8000
facerec run --challenge --door sim --db data/facegate.db --zone lab   # les accès alimentent le tableau de bord
python scripts/seed_demo.py                    # base fictive pour une démonstration (data/demo.db)
```

Phase 1 : base SQLite (personnes = empreintes seulement, zones, droits, événements), connexion sécurisée, tableau de bord (accès, refus et leurs raisons, passages par heure) et API JSON en lecture. Sécurité ([`web/app.py`](src/facerec/web/app.py)) : mots de passe scrypt, sessions côté serveur (seul un hash du jeton est stocké), cookie HttpOnly + SameSite=Strict, jeton CSRF + contrôle de l'en-tête Origin, limitation à 5 échecs / 15 min, réponse identique pour un utilisateur inconnu, CSP stricte sans script ni style en ligne, pas de documentation d'API publique. À écouter sur `127.0.0.1` ; hors de la machine, derrière HTTPS avec `--secure-cookies`.

Phase 2 : **personnes** (activer, désactiver, supprimer en tapant le nom ; consentement affiché), **zones et horaires** (règles « toujours » ou jours + plage horaire, plages de nuit, refus par défaut), **journal** (filtres décision / personne / zone / dates, pagination, export CSV protégé contre l'injection de formules), **journal d'administration** (qui a modifié quoi). Chaque action est protégée par CSRF, validée côté serveur et tracée.

Le service caméra lit la base : `facerec run --db data/facegate.db --zone lab` charge les personnes actives et les règles, et **les recharge dans les 2 secondes** quand l'interface web les modifie (désactiver quelqu'un coupe son accès sans redémarrer). Sans `--policy`, les règles viennent de la base ; tant qu'aucune zone n'existe, toute personne reconnue est autorisée (un message l'indique) ; dès qu'une zone existe, le refus par défaut s'applique. Inscription dans la base : `facerec enroll NOM --db data/facegate.db`.

Phase 3 : **vidéo en direct**, **inscription depuis le navigateur**, **fenêtre caméra refaite**, animations.

- *En direct* : le service caméra (qui garde la webcam pour lui seul) publie l'image annotée et son état dans un dossier ; l'interface les relaie en MJPEG aux administrateurs connectés (3 flux simultanés au plus). **Opt-in** : `facerec run --live-dir data/live` et `facerec web --live-dir data/live`. Une seule image existe sur le disque, constamment écrasée et supprimée à l'arrêt ; aucune vidéo n'est enregistrée. Une image de visage est une donnée personnelle : n'activez cette option que si vous en avez besoin.
- *Inscription* (Personnes → « Inscrire une personne ») : nom + consentement coché → une demande est mise en file ; le service caméra prend 20 empreintes (un seul visage, qualité contrôlée, échantillons espacés), refuse un visage **déjà inscrit sous un autre nom** (personnes désactivées comprises), et la page suit la progression en direct. Le texte de consentement et l'administrateur qui l'a recueilli sont conservés avec la personne. Une seule inscription à la fois, annulable ; un service arrêté en cours de route ne laisse pas la page en attente.
- *Fenêtre caméra* (`overlay.py`) : texte avec accents (Pillow), pastille d'état placée hors du HUD et du bandeau de porte, flèche + étapes + compte à rebours pour le défi, bandeau de porte avec cadenas et anneau, panneau d'inscription. `python scripts/overlay_preview.py out.png` en donne un aperçu sans caméra.
- *Animations* : apparition des cartes, compteurs, barres du graphique, porte (cadenas qui s'ouvre, anneau), anneau de progression, coche de fin ; toutes coupées si le système demande « réduire les animations ».
- `python scripts/simulate_service.py` simule le service caméra (images synthétiques, inscriptions avec des empreintes **aléatoires**) pour essayer l'interface sans caméra. Ce n'est pas de la reconnaissance.

Phase 4 : **chiffrement, comptes, conformité, déploiement.**

- *Chiffrement des empreintes* (AES-256-GCM, [`crypto.py`](src/facerec/crypto.py)) : nonce aléatoire par valeur, empreinte liée à sa personne (un échange entre deux personnes est détecté), clé hors de la base (`FACEGATE_KEY` ou `--key-file`), garde-fou : une clé perdue n'est **jamais** remplacée en silence si des empreintes chiffrées existent. Effacement sécurisé des pages libérées (sans cela, SQLite laissait d'anciennes empreintes en clair dans le fichier). `facerec db status | encrypt | rotate-key | backup | purge`.
- *Comptes* : rôles **lecteur** (tableau de bord, journal) < **gestionnaire** (personnes, zones, inscription, vidéo, export) < **propriétaire** (administrateurs, journal d'administration, conformité). Mot de passe temporaire à changer à la première connexion, changement de mot de passe (ferme les autres sessions), **double authentification TOTP** (RFC 6238, QR code, 8 codes de secours à usage unique, rejeu refusé), comptes désactivables, dernier propriétaire protégé. Création : `facerec admin create NOM --role manager`.
- *Conformité* (page « Conformité », propriétaires) : durées de conservation réglables (journal des accès 90 j, administration 365 j par défaut) avec **purge automatique toutes les 6 h** ; supprimer une personne efface ses empreintes et **anonymise son nom** dans le journal des accès et dans le journal d'administration ; **export JSON des données d'une personne** (droit d'accès) ; état du chiffrement. Modèles : [AIPD](docs/AIPD-modele.md), [registre des traitements](docs/registre-des-traitements.md).
- *Documents* : [`docs/SECURITE.md`](docs/SECURITE.md) (ce qui est protégé **et ce qui ne l'est pas**), [`docs/DEPLOIEMENT.md`](docs/DEPLOIEMENT.md).
  Aussi : [`docs/GUIDE-UTILISATEUR.md`](docs/GUIDE-UTILISATEUR.md), [`docs/PROTOCOLE-ATTAQUES.md`](docs/PROTOCOLE-ATTAQUES.md) (mesurer la fraude), [`docs/LINKEDIN.md`](docs/LINKEDIN.md).
- *Docker et CI* : `Dockerfile` (images `web`, sans dlib ni caméra, et `service`), `docker-compose.yml`, `.github/workflows/ci.yml`. **Écrits et validés comme YAML, jamais construits ni exécutés** (Docker absent de la machine de développement, aucune exécution sur GitHub) : la première construction est le vrai test.

Vérification : **443 tests**, et les protections critiques (permissions, 2FA, rejeu, chiffrement, effacement, export, purge) ont été contrôlées par **mutation** : on casse volontairement le code et on vérifie que les tests échouent (une soixantaine de défauts injectés ; les trous trouvés ont été comblés).

Pas encore fait : **tests sur de vraies attaques** (photo, vidéo, masque) et sur du matériel (Raspberry Pi), **mesure des biais**, audit externe, certification.

### Limites connues

- **LFW n'est pas une webcam**, et les modèles pré-entraînés ont pu voir des célébrités de LFW pendant leur entraînement : les chiffres sont probablement optimistes. Ils servent à comparer des choix, pas à prédire la performance sur ton matériel. La mesure qui compte : `facerec collect` puis `facerec evaluate` sur des volontaires.
- Écarts-types élevés à FPIR ≤ 1 % (peu d'imposteurs par tirage) : comparer plutôt les EER.
- Les biais démographiques doivent être mesurés avant tout usage réel.
- Le clignement seul arrête une photo, **pas** une vidéo rejouée ni un masque ; `--challenge` rend la vidéo préenregistrée beaucoup plus difficile mais pas impossible (voir ci-dessus). Ce projet n'est pas encore un système de sécurité.

## Vie privée

- Seules des empreintes (128 nombres) sont conservées, jamais de photo. Une empreinte reste une donnée biométrique personnelle (RGPD) : elle doit être protégée comme telle.
- Consentement demandé à l'inscription ; `facerec delete` supprime une personne.
- Le journal contient l'heure, la décision et le nom, sans image.
- `data/`, `gallery/`, `logs/` sont exclus de git.

## Reproduire les résultats

```bash
python scripts/fetch_lfw.py                                    # réglage : 80 personnes
python scripts/fetch_lfw.py --offset 80 --out data/lfw_val    # validation : 80 autres
facerec evaluate data/lfw --out results/yunet_sface
facerec evaluate data/lfw_val --out results/yunet_sface_val
facerec evaluate data/lfw --out results/hog_dlib --detector hog --embedder dlib
python scripts/ablation.py data/lfw --features results/yunet_sface/features_yunet_sface.npz
python scripts/benchmark_speed.py --out results/speed.md
pytest
```

Pour mesurer sur ton matériel : `facerec collect NOM` (images de volontaires consentants, gardées sur ton ordinateur) puis `facerec evaluate data/volunteers`.

## Structure

```
src/facerec/   engine, quality, gallery, smoothing, tracking, liveness, evaluation, live, cli
scripts/       download_models.py, fetch_lfw.py, ablation.py, analyze_failures.py, benchmark_speed.py
tests/         443 tests (logique, sécurité de l'interface, boucle caméra simulée, bout en bout sur visages réels si LFW est présent)
results/       un dossier par configuration (rapports, courbes), vitesse
```
#   f a c e g a t e - c o n t r o l e - a c c e s  
 