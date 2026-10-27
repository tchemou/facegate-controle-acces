# Déploiement

Deux programmes, qui partagent une base SQLite et un dossier :

```
┌────────────────────────┐   base SQLite    ┌─────────────────────────┐
│ Service de reconnaissance │◄──────────────►│ Application web (FaceGate) │
│  (garde la caméra)        │  dossier « live » │  administrateurs, journal   │
│  facerec run              │ (image courante)  │  facerec web                │
└────────────────────────┘                   └─────────────────────────┘
```

- Le **service** a besoin de la caméra, de dlib et de la **clé de chiffrement**.
- L'**application web** n'a besoin ni de la caméra ni de la clé : elle ne lit jamais les empreintes.

## 1. Sur un poste (Windows, Linux, macOS)

```bash
python -m venv .venv && .venv\Scripts\activate         # Linux/macOS : source .venv/bin/activate
pip install -e ".[dev]"
python scripts/download_models.py                       # modèles OpenCV Zoo, vérifiés par SHA-256

facerec admin create NOM                                # mot de passe demandé dans le terminal
facerec import-gallery                                  # (si vous aviez une galerie .npz) ; crée la clé au besoin
facerec web --port 8765 --live-dir data/live            # fenêtre 1
facerec run --challenge --door sim --db data/facegate.db --zone lab --live-dir data/live   # fenêtre 2
```

Au premier usage, une **clé de chiffrement** est créée (`data/facegate.key`). Ensuite :

1. **Sauvegardez-la ailleurs** que la base. Sans elle, les empreintes sont perdues.
2. Mieux : passez-la par la variable `FACEGATE_KEY` plutôt que par un fichier à côté de la base.
3. `facerec db status` indique si toutes les empreintes sont chiffrées ; `facerec db encrypt` chiffre les anciennes.

Un port « interdit » sous Windows (erreur 10013) : essayez `--port 8765`.

## 2. Docker

> **Non testé sur la machine de développement** (Docker n'y était pas disponible). Construisez, puis faites les
> vérifications ci-dessous avant d'y faire confiance.

```bash
docker compose build
docker compose up -d web
docker compose run --rm -it web python -m facerec.web create-admin NOM
# puis http://127.0.0.1:8000
```

Le service de reconnaissance (`--profile camera`) ne fonctionne que sous **Linux** avec une caméra
(`--device /dev/video0`) : Docker Desktop sous Windows ou macOS ne sait pas relayer une webcam. Sur ces systèmes,
lancez le service directement sur le poste (§1) et gardez l'application web dans Docker si vous le souhaitez.

À vérifier après le premier `docker compose build` :

- [ ] l'image `web` démarre et `http://127.0.0.1:8000/healthz` répond `ok`
- [ ] `docker compose exec web id` : l'utilisateur n'est pas root
- [ ] `docker compose exec web touch /app/x` échoue (système de fichiers en lecture seule)
- [ ] les données (`/data`) survivent à `docker compose down` puis `up`
- [ ] l'image `camera` trouve ses modèles : `docker compose run --rm camera python -c "import facerec.engine"`

## 3. HTTPS (obligatoire hors du poste local)

Placez un proxy TLS devant l'application web, puis `FACEGATE_SECURE_COOKIES=1`. Exemple avec Caddy
(certificat automatique) :

```
facegate.exemple.fr {
    reverse_proxy 127.0.0.1:8000
}
```

N'exposez **pas** l'application sur Internet : réseau interne ou VPN uniquement.

## 4. Sauvegarde et restauration

```bash
facerec db backup sauvegardes/facegate-2026-10-27.db     # copie cohérente, même pendant l'utilisation
```

La sauvegarde contient les empreintes **chiffrées** : elle est inutilisable sans la clé, qu'il faut donc
conserver séparément (et la tester : restaurez sur une machine de test avec la clé avant d'en avoir besoin).

Restauration : arrêtez les deux programmes, remplacez `data/facegate.db`, remettez la clé, relancez.

## 5. Changer de clé

```bash
facerec db rotate-key data/nouvelle.key        # rechiffre tout, atomiquement ; l'ancienne clé ne sert plus à rien
```

Remplacez ensuite `FACEGATE_KEY` (ou le fichier) par la nouvelle clé, et détruisez l'ancienne.

## 6. Mise en service : liste de contrôle

- [ ] AIPD réalisée et validée (`docs/AIPD-modele.md`), fiche de registre remplie
- [ ] alternative non biométrique prévue, information des personnes (panneau + consentement)
- [ ] au moins **deux propriétaires**, double authentification activée
- [ ] HTTPS devant l'interface ; non exposée sur Internet
- [ ] clé sauvegardée séparément et **restauration testée**
- [ ] taux d'erreur **mesuré sur le matériel et la population réels** (faux rejets, usurpations réelles)
- [ ] durée de conservation du journal réglée (Conformité)
