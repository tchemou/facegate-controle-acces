# Kit LinkedIn : présenter FaceGate

À compléter avec **vos chiffres mesurés** (remplacez les `[...]`). Ne publiez pas de chiffre que vous n'avez pas mesuré.

## Texte du post (version courte)

> J'ai construit **FaceGate**, un système de contrôle d'accès par reconnaissance faciale pensé pour une entreprise française : le visage ouvre la porte, mais seulement si c'est bien une personne vivante, autorisée, au bon endroit et à la bonne heure.
>
> Ce que j'ai voulu résoudre :
> 🔒 Une photo ou un écran ne doit pas ouvrir la porte → défi aléatoire (« tournez la tête ») + détection d'écran
> 📜 Les données biométriques sont sensibles → empreintes chiffrées (AES-256), aucune photo conservée, journal à durée limitée, export et effacement RGPD
> 👥 Une équipe sécurité a besoin d'un outil, pas d'un script → console web avec rôles, double authentification, tableau de bord et journal
>
> Ce que j'ai mesuré (protocole ISO/IEC 30107-3, [N] présentations par scénario) :
> • Attaques acceptées : [x] % (intervalle à 95 % : [a – b] %)
> • Vrais utilisateurs refusés : [y] % (intervalle : [c – d] %)
>
> Limites assumées : prototype testé sur [matériel], porte simulée, pas d'audit externe. La suite : [serrure réelle / multi-caméras / audit].
>
> Je cherche un stage en robotique / automatisme / mécatronique à partir de mai 2027. Si ce sujet vous parle, échangeons !
>
> #Robotique #Mécatronique #VisionParOrdinateur #Cybersécurité #RGPD #Stage2027

## Vidéo de démonstration (60 à 90 secondes)

| Temps | Plan | Message |
|---|---|---|
| 0–8 s | Écran de connexion animé | « FaceGate : contrôle d'accès par visage, conforme RGPD » |
| 8–25 s | Caméra : vous entrez, défi « tournez la tête », porte « ouverte » | Le cas normal, en quelques secondes |
| 25–40 s | Même caméra : photo sur téléphone, puis vidéo | Refusés : « écran détecté » ou défi échoué |
| 40–55 s | Tableau de bord : les refus apparaissent dans l'activité en direct | La supervision |
| 55–70 s | Inscription (consentement), zones et horaires | Les droits, par zone et par heure |
| 70–90 s | Score de sécurité, chiffres mesurés, limites | La transparence |

Conseils : filmez l'écran avec la caméra de contrôle visible ; sous-titrez (beaucoup regardent sans le son) ; terminez par vos chiffres.

## Captures d'écran à publier (carrousel)

1. Connexion avec le visage animé
2. Tableau de bord (courbe, anneau des refus, carte de chaleur)
3. Vue « En direct » avec la décision à l'écran
4. Posture de sécurité
5. Tableau des résultats d'attaques (APCER / BPCER avec intervalles)

Floutez les noms de personnes réelles : utilisez la base de démonstration (`scripts/seed_demo.py`).

## Questions d'entretien à préparer

- *Pourquoi un défi aléatoire ?* Une vidéo enregistrée ne peut pas deviner la consigne tirée au hasard.
- *Que se passe-t-il si le service tombe ?* La porte reste fermée (comportement par défaut).
- *Comment gérez-vous le RGPD ?* Consentement, aucune photo, chiffrement, rétention + purge, export, effacement, modèles AIPD/registre.
- *Quelles sont les limites ?* Voir le paragraphe « Limites assumées » : c'est votre meilleur argument de crédibilité.
