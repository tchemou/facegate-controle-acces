# Protocole de test d'attaques de présentation

Objectif : mesurer **honnêtement** si le système se laisse tromper par une photo, une vidéo ou une autre personne, et
s'il laisse entrer la vraie personne. Durée : environ 45 minutes pour 4 scénarios × 20 essais.

## Ce qu'on mesure (ISO/IEC 30107-3)

| Mesure | Définition | Se lit comme |
|---|---|---|
| **APCER** | part des **attaques acceptées** | échec de **sécurité** (bas = bon) |
| **BPCER** | part des **vrais utilisateurs rejetés ou sans décision** | échec d'**usage** (bas = bon) |

Avec peu d'essais, un taux seul ne veut rien dire : **0 attaque réussie sur 20 est compatible avec un vrai taux
allant jusqu'à 16 %**. Le rapport affiche toujours l'intervalle de confiance à 95 % à côté de chaque taux.

## Matériel

- la webcam habituelle, dans la position où elle sera utilisée ;
- un téléphone (pour la photo et la vidéo) ;
- *(facultatif)* une photo imprimée de vous ;
- *(pour « imposteur »)* une personne **qui est d'accord**, jamais quelqu'un à son insu.

Ne photographiez ni ne filmez que **vous-même** ou des personnes consentantes.

## Préparation

1. Vous êtes inscrit·e : `facerec db status` indique les empreintes chiffrées, et `facerec list` votre nom.
2. Préparez, sur le téléphone : une **photo de vous** (de face, nette), et **deux vidéos de vous** :
   - *vidéo naïve* : vous regardez la caméra en bougeant naturellement ;
   - *vidéo ciblée* : vous y tournez la tête **à gauche, puis à droite, puis à gauche** (ce que l'attaquant
     devinerait du défi : 1 chance sur 8 qu'il corresponde à la séquence tirée au hasard).
3. Un fichier de résultats **par session**, daté. Ne modifiez, ne supprimez et n'écrasez jamais un fichier.

## Lancer la session

```
.venv\Scripts\python.exe -m facerec run --challenge --door sim --db data/facegate.db --zone lab --attack-log logs/attaques-2026-10-27.csv
```

La fenêtre indique `TEST : reel` en haut à gauche, et la liste des touches en bas à droite. **Annoncez le scénario
avant chaque série** en appuyant sur la touche correspondante :

| Touche | Scénario | Quoi présenter |
|---|---|---|
| `1` | **reel** | Vous, en direct |
| `2` | **photo** | La photo de vous sur l'écran du téléphone (plein écran), puis une version imprimée |
| `3` | **video** | La vidéo de vous (naïve, puis ciblée) sur le téléphone |
| `4` | **imposteur** | Une autre personne, en direct |
| `5` | **masque** | Toute autre attaque que vous voulez essayer |

`facerec run --attack-log` refuse de démarrer avec `--door gpio` : un test d'attaque ne doit **jamais** pouvoir
actionner une vraie porte.

## Une présentation (à répéter)

1. Mettez-vous hors du champ de la caméra.
2. Appuyez sur la touche du scénario (une seule fois par série).
3. Entrez dans le champ et **attendez la décision** (jusqu'à environ 20 secondes avec le défi). Ne l'aidez pas :
   si on vous demande de tourner la tête, tournez-la (pour *reel*) ; pour une attaque, présentez simplement le support.
4. **Sortez complètement du champ pendant au moins 2 secondes** : c'est ce qui sépare deux présentations.
5. Recommencez.

Une présentation sans accord ni refus (le système ne répond pas) est comptée **« sans décision »** : pour un vrai
utilisateur, c'est un échec d'usage (BPCER).

## Combien d'essais ?

- **Au moins 20 par scénario** (en dessous, les intervalles sont tellement larges qu'ils ne disent presque rien).
- Variez les conditions d'un essai à l'autre : distance (40 à 80 cm), lumière (jour, lampe, contre-jour), angle du
  téléphone, luminosité de son écran (minimum, maximum), photo plein écran ou non. **Notez-les** à côté du fichier.
- Pour la vidéo, faites **les deux** (naïve et ciblée) : seule la ciblée peut passer par hasard.

## Règles d'honnêteté

- Gardez **tous** les essais, y compris les attaques qui réussissent. Une session « propre » obtenue en supprimant
  les échecs ne vaut rien.
- Ne réglez pas le système *pendant* une session. Si vous le modifiez après un échec, ouvrez un **nouveau fichier**
  et comparez les deux : c'est une amélioration mesurée, pas un résultat truqué.
- Mentionnez toujours : un seul appareil, une seule personne, un seul lieu.

## Le rapport

```
.venv\Scripts\python.exe -m facerec attack-report logs/attaques-2026-10-27.csv --out results/attaques.md
```

Le rapport donne, par scénario : essais, acceptés, refusés, sans décision, taux d'erreur avec son intervalle, et
**pourquoi** les attaques ont été refusées (`challenge_timeout`, `screen_detected`, `unknown_person`…). Plusieurs
fichiers peuvent être combinés.

## Lire les résultats

| Constat | Ce que ça veut dire | Quoi faire |
|---|---|---|
| Une attaque passe | Un trou de sécurité réel, avec sa raison dans le journal | Le noter, corriger, **retester dans un nouveau fichier** |
| BPCER élevé (vous êtes souvent refusé·e) | Le système gêne les vrais utilisateurs | Regarder les raisons : seuils de défi, lumière, caméra |
| « Sans décision » nombreux | Qualité d'image insuffisante (flou, trop petit, mal éclairé) | Distance, éclairage, résolution de la caméra |
| Vidéo ciblée acceptée ~1 fois sur 8 | Attendu : la séquence est tirée au hasard parmi 8 | C'est la limite connue du défi (voir `docs/SECURITE.md`) |

## Ce qu'on peut dire publiquement

Une phrase honnête ressemble à : *« Sur 20 essais par scénario (une personne, un appareil), aucune photo ni vidéo
naïve n'a été acceptée (APCER ≤ 16 % à 95 %) ; la vidéo ciblée a été acceptée X fois sur 20 ; le taux de faux rejets de
l'utilisateur légitime est de Y %. »* Ne dites **jamais** « 0 % » ou « infaillible » : les chiffres ne le permettent pas.

## Vie privée

Le fichier contient des noms (celui de la personne reconnue). Il reste dans `logs/`, exclu de git : ne le publiez pas
tel quel, publiez le rapport.
