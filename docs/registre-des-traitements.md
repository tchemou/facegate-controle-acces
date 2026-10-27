# Fiche de registre des traitements (art. 30 du RGPD) : modèle

> Modèle de travail à compléter par le responsable du traitement. Pas un avis juridique.

| Rubrique | Contenu |
|---|---|
| **Nom du traitement** | Contrôle d'accès par reconnaissance faciale : *[zone]* |
| **Responsable du traitement** | *Nom, adresse, contact* |
| **DPO** | *Nom, contact* |
| **Finalité** | Restreindre l'accès à *[zone]* aux seules personnes habilitées |
| **Base légale** | *À qualifier avec le DPO (voir l'AIPD)* |
| **Catégories de personnes** | *Salariés habilités, prestataires…* |
| **Catégories de données** | Identité (nom ou pseudonyme) ; empreinte numérique du visage (donnée biométrique, 128 nombres, **aucune photo**) ; journal des accès (date, zone, décision, motif du refus) ; comptes d'administration (identifiant, rôle, journal d'administration) |
| **Destinataires** | Administrateurs de FaceGate selon leur rôle (propriétaire, gestionnaire, lecteur). Aucun transfert à un tiers. |
| **Transferts hors UE** | Aucun (logiciel sur place, sans cloud) |
| **Durées de conservation** | Empreintes : durée de l'habilitation. Journal des accès : *[90 jours par défaut]*. Journal d'administration : *[à fixer]* |
| **Mesures de sécurité** | Chiffrement des empreintes (AES-256-GCM, clé séparée) ; authentification forte (mot de passe scrypt + TOTP) ; rôles ; limitation des essais ; sessions serveur ; protection CSRF ; en-têtes de sécurité ; journal d'administration ; sauvegardes cohérentes ; purge automatique ; effacement sécurisé |
| **Analyse d'impact (AIPD)** | Réalisée le *[date]*, voir `AIPD-modele.md` complété |
| **Mise à jour** | *[date, auteur]* |
