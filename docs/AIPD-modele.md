# Analyse d'impact relative à la protection des données (AIPD) : modèle

> **Ce document est un modèle de travail, pas un avis juridique.** La reconnaissance faciale est un traitement de
> données biométriques (art. 9 du RGPD) : l'AIPD est **obligatoire** avant la mise en service. À faire relire par
> votre délégué à la protection des données (DPO) ou un juriste. Les parties « À compléter » dépendent de votre
> organisation ; les parties « Ce que fait FaceGate » décrivent le logiciel tel qu'il est écrit.
> Référence utile : le *règlement type de la CNIL sur la biométrie sur les lieux de travail*.

## 1. Description du traitement

| Élément | À compléter |
|---|---|
| Responsable du traitement | *Nom, adresse de l'organisation* |
| DPO / contact | *Nom, e-mail* |
| Finalité (une seule, précise) | Contrôler l'accès à *[zone précise : salle serveurs, laboratoire…]* |
| Personnes concernées | *Catégories (salariés habilités, prestataires…), nombre approximatif* |
| Lieux et horaires | *Portes concernées, plages horaires* |
| Durées de conservation | Voir §6 |

**Ce que fait FaceGate**

- Une caméra est dirigée vers la zone d'accès. Pour chaque personne qui se présente, le système calcule une
  **empreinte numérique** (128 nombres) et la compare à celles des personnes inscrites.
- **Aucune photo n'est conservée.** L'image courante n'existe que dans la mémoire du service ; la vidéo en direct
  de l'interface web est **désactivée par défaut** et, activée, ne garde qu'une image constamment écrasée,
  supprimée à l'arrêt.
- Chaque décision (autorisé / refusé, raison, zone, heure, nom éventuel) est écrite dans un journal.
- Les empreintes sont **chiffrées au repos** (AES-256-GCM) avec une clé distincte de la base.

## 2. Nécessité et proportionnalité

*À argumenter par l'organisation, la CNIL vérifie ces points en priorité.*

- [ ] **Pourquoi la biométrie ?** Un badge, un code ou une clé ne suffisent pas parce que : *…*
- [ ] **Zone réellement sensible** (salle serveurs, laboratoire, stockage de produits dangereux…), pas un accès
      de bureau courant ni un pointage d'horaires (la CNIL le déconseille).
- [ ] **Une alternative non biométrique existe** et est proposée sans désavantage (badge + code, accueil…).
- [ ] **Minimisation** : empreinte numérique seulement, jamais de photo ; une seule zone par dispositif.
- [ ] Consentement ou autre base légale : *à qualifier avec le DPO. Dans une relation employeur-salarié, un consentement
      est difficile à considérer comme libre ; c'est l'une des raisons pour lesquelles l'alternative non biométrique compte.*

## 3. Base légale (à qualifier)

| Question | Réponse de l'organisation |
|---|---|
| Base légale (art. 6) | *…* |
| Exception à l'interdiction de principe (art. 9 §2) | *…* |
| Texte de consentement affiché | Celui de l'interface (« Inscrire une personne »), conservé avec la personne et l'administrateur qui l'a recueilli |

## 4. Risques pour les personnes et mesures

| Risque | Gravité × probabilité (à évaluer) | Mesure dans FaceGate | Mesure organisationnelle (à compléter) |
|---|---|---|---|
| Fuite de la base d'empreintes | | Chiffrement AES-256-GCM des empreintes ; clé hors de la base ; effacement sécurisé des pages libérées | Clé dans un coffre ; sauvegardes chiffrées ; chiffrement du disque |
| Accès non autorisé à l'interface | | Mots de passe scrypt, double authentification (TOTP), codes de secours, limitation des essais, sessions serveur, CSRF, CSP stricte, rôles (lecteur / gestionnaire / propriétaire) | Liste des administrateurs revue périodiquement ; HTTPS obligatoire hors poste local |
| Usurpation (photo, vidéo) | | Défi aléatoire (tourner la tête), détection d'écran | Porte à double validation pour les zones critiques |
| Faux rejets / faux accords | | Seuils mesurés (taux d'erreur publiés dans le README), refus par défaut | Procédure de secours ; mesure sur le matériel réel avant mise en service |
| **Biais** (taux d'erreur différents selon le profil des personnes) | | **Non mesuré à ce jour** | Mesurer sur la population concernée avant déploiement |
| Détournement de finalité | | Journal d'administration (qui a modifié quoi), un journal des accès par zone | Charte d'usage ; contrôle périodique |
| Conservation excessive | | Purge automatique du journal (durée configurable) ; suppression d'une personne = effacement de ses empreintes et anonymisation de son historique | Durées fixées au §6 |
| Perte de la clé de chiffrement | | Garde-fou : une nouvelle clé n'est jamais créée silencieusement si des empreintes chiffrées existent | Sauvegarde de la clé séparée de celle de la base |

## 5. Droits des personnes

| Droit | Comment l'exercer avec FaceGate |
|---|---|
| Information | Texte affiché à l'inscription ; *panneau d'information à l'entrée de la zone : à fournir* |
| Accès | Export des données d'une personne (interface : Conformité) |
| Effacement | Personnes → Supprimer : efface ses empreintes et ses règles, anonymise son historique |
| Retrait du consentement / opposition | Désactiver ou supprimer la personne ; proposer l'alternative |
| Limitation | Désactivation de la personne (accès coupé immédiatement, données conservées) |

## 6. Durées de conservation

| Donnée | Durée proposée | Mécanisme |
|---|---|---|
| Empreintes | Tant que la personne est habilitée ; effacées à la suppression | Suppression dans l'interface |
| Journal des accès | *À fixer (par défaut 90 jours)* | Purge automatique, réglable dans l'interface |
| Journal d'administration | *À fixer* | Conservé ; ne contient pas d'empreintes |
| Sauvegardes | *À fixer* | Les empreintes y restent chiffrées |

## 6 bis. Sous-traitants et transferts

FaceGate fonctionne **sur place, sans cloud** : aucune donnée n'est envoyée à un tiers par le logiciel.
*À compléter : hébergeur du serveur éventuel, prestataire de maintenance.*

## 7. Avis et validation

| | Nom | Date | Avis |
|---|---|---|---|
| DPO | | | |
| Responsable du traitement | | | |
| Représentants du personnel (le cas échéant) | | | |

*Si un risque élevé subsiste malgré les mesures : consultation préalable de la CNIL (art. 36 du RGPD).*
