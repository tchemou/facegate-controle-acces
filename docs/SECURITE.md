# Sécurité : ce qui est protégé, comment, et ce qui ne l'est pas

Ce document dit ce que le logiciel fait **et ce qu'il ne fait pas**. Il vaut mieux connaître les limites avant
qu'un tiers ne les trouve.

## Ce qui est en place

| Menace | Protection | Où |
|---|---|---|
| Vol d'une copie de la base (sauvegarde, disque) | Empreintes chiffrées AES-256-GCM, nonce aléatoire par valeur, liées à leur propriétaire (échange entre deux personnes détecté), effacement sécurisé des pages libérées | `crypto.py`, `store.py` |
| Perte de la clé | Une nouvelle clé n'est **jamais** créée en silence si la base contient déjà des empreintes chiffrées | `cli.py: open_store` |
| Mot de passe volé | Double authentification TOTP (norme RFC 6238), codes de secours à usage unique, rejeu d'un code refusé | `web/totp.py`, `web/accounts.py` |
| Force brute | 5 essais / 15 min (mot de passe et 2ᵉ étape séparément), scrypt, réponse identique pour compte inconnu / mauvais mot de passe / compte désactivé | `web/security.py`, `web/accounts.py` |
| Vol de session | Jeton aléatoire côté serveur (seul son hash est stocké), cookie HttpOnly + SameSite=Strict, fermeture de toutes les autres sessions au changement de mot de passe ou à la désactivation | `web/app.py`, `store.py` |
| CSRF | Jeton par session sur chaque action + contrôle de l'en-tête Origin (`null` refusé) | `web/app.py` |
| XSS | Échappement systématique, CSP stricte (`script-src 'self'`, aucun script ni style en ligne), jamais d'`innerHTML` | `web/` |
| Abus de droits | Rôles lecteur < gestionnaire < propriétaire contrôlés avant tout le reste ; le dernier propriétaire actif ne peut pas être retiré ; on ne modifie pas son propre compte | `web/app.py`, `web/accounts.py` |
| Fuite par le journal | Export CSV protégé contre l'injection de formules | `web/app.py` |
| Image de visage sur disque | Vidéo en direct **désactivée par défaut** ; une seule image, écrasée, supprimée à l'arrêt | `channel.py` |
| Porte laissée ouverte | Fermeture à l'arrêt ou en cas de plantage (échec fermé) | `live.py`, `door.py` |

Chaque ligne a des tests, et les protections critiques ont été vérifiées par **mutation** : on casse volontairement
le code et on s'assure que les tests échouent.

## Ce qui n'est PAS protégé (à connaître)

- **Quelqu'un qui a à la fois la base et la clé**, ou qui exécute du code sur la machine pendant que le service
  tourne. Gardez la clé hors de la base (variable d'environnement, coffre).
- **Les noms, les textes de consentement et le journal des accès ne sont pas chiffrés** par le logiciel : utilisez
  le chiffrement de disque (BitLocker, LUKS).
- **Le secret TOTP** est stocké en clair dans la base (comme dans la plupart des applications) : le chiffrement de
  disque et la protection du fichier de base s'appliquent.
- **L'anti-usurpation n'est validé qu'en simulation** : un attaquant en direct (deepfake temps réel, masque) ou une
  vidéo qui réagit au défi peut passer. Ce n'est pas un système de sécurité certifié.
- **Les biais** (taux d'erreur selon le profil des personnes) **n'ont pas été mesurés**.
- **Aucune certification** (ni ANSSI, ni autre). Pas d'audit externe.
- Sous Windows, les droits du fichier de clé (`0600`) ne s'appliquent pas : placez-le dans un dossier protégé.

## Déploiement

1. **HTTPS obligatoire** dès que l'interface n'est pas sur le poste local : placez-la derrière un proxy TLS
   (Caddy, nginx, Traefik) et lancez-la avec `--secure-cookies`.
2. Ne l'exposez pas sur Internet. Limitez-la au réseau interne ou à un VPN.
3. Créez au moins **deux propriétaires** (l'un peut réinitialiser l'autre) et activez la **double authentification**.
4. Sauvegardez la **clé séparément** de la base (`facerec db backup`).
5. Mesurez le taux d'erreur sur **votre matériel et votre population** avant la mise en service.
6. Fournissez une **alternative non biométrique** et informez les personnes (panneau + texte de consentement).

## Signaler une faille

Décrivez le problème et la manière de le reproduire au responsable du projet. Ne publiez pas de détails
exploitables avant correction.
