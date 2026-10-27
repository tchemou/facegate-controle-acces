# Guide d'utilisation FaceGate

Pour les personnes qui administrent le système au quotidien. Aucune compétence technique n'est requise après l'installation.

## 1. Se connecter

Ouvrez l'adresse fournie par l'installateur et entrez votre identifiant et votre mot de passe. Au premier accès, le mot de passe
temporaire doit être remplacé. Après 5 essais ratés, la connexion est suspendue 15 minutes.

Activez la **double authentification** dans *Mon compte* : une application (Google Authenticator, Aegis…) affiche un code à 6 chiffres.
Conservez les **codes de secours** hors de l'ordinateur.

## 2. Les trois rôles

| Rôle | Peut faire |
|---|---|
| **Lecteur** | Voir le tableau de bord et le journal des accès |
| **Gestionnaire** | + gérer les personnes, zones et horaires, voir la vidéo en direct |
| **Propriétaire** | + gérer les administrateurs, le journal d'administration et la conformité |

Il doit toujours rester au moins un propriétaire actif.

## 3. Inscrire une personne

*Personnes → Inscrire une personne*.

1. Saisissez le nom et **cochez le consentement** : la personne doit avoir lu l'information et accepté (RGPD, donnée biométrique).
2. La personne se place devant la caméra de contrôle. Le système prend une vingtaine d'empreintes en variant légèrement l'angle.
3. Aucune photo n'est conservée : seules des empreintes numériques chiffrées sont enregistrées.

Pour **retirer** quelqu'un : *Désactiver* coupe l'accès immédiatement et garde l'historique ; *Supprimer* efface les empreintes et anonymise le journal
(à utiliser pour un droit à l'effacement).

## 4. Donner des droits d'accès

*Zones et horaires* : créez une zone (une porte ou un local), puis une règle « personne × zone × jours × heures ».
Par défaut, **tout est refusé** : une personne sans règle n'entre pas.

## 5. Surveiller

- **Tableau de bord** : accès autorisés/refusés, tentatives suspectes (écran détecté, défi non réussi), motifs de refus, fréquentation, activité récente.
- **Journal** : filtrable par personne, zone, décision, dates ; export CSV.
- **En direct** : image de la caméra annotée. Elle n'est enregistrée nulle part.
- **Ctrl + K** : recherche rapide de n'importe quelle page ou action.

La **posture de sécurité** du tableau de bord liste ce qui reste à faire (empreintes en clair, un seul propriétaire, 2FA absente…).

## 6. Conformité (propriétaire)

*Conformité* permet de fixer la durée de conservation du journal (purge automatique), d'exporter les données d'une personne (droit d'accès) et d'afficher
l'état du chiffrement. Les modèles d'analyse d'impact et de registre sont dans `docs/`.

## 7. Que faire si…

| Situation | Action |
|---|---|
| Une personne est refusée à tort | Vérifier ses droits et horaires ; si le journal indique « visage non reconnu », la réinscrire avec un bon éclairage |
| Message « service arrêté » | Relancer `facerec run …` ; la porte reste fermée tant que le service est arrêté |
| Mot de passe oublié | Un propriétaire le réinitialise dans *Administrateurs* |
| Propriétaire unique bloqué | `facerec admin create NOM --role owner` en ligne de commande, sur le serveur |
| Clé de chiffrement perdue | Les empreintes chiffrées sont irrécupérables : réinscrire tout le monde. **Sauvegardez `data/facegate.key` séparément de la base.** |

## 8. Limites à connaître

- Le système détecte les photos et les écrans, mais **aucune protection n'est absolue** : pour une zone critique, combinez-le avec un second facteur (badge, code).
- Il n'a pas été audité par un tiers. Les taux d'erreur réels dépendent de la caméra, de la lumière et des personnes ; mesurez-les sur site (`docs/PROTOCOLE-ATTAQUES.md`).
