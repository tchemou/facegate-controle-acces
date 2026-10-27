# Résultats d'évaluation

- Images : 1226, visage détecté : 1224, qualité suffisante : 996
- Identités connues / inconnues par tirage : 39 / 39
- Sondes légitimes ≈ 485, sondes imposteurs ≈ 624
- Inscription : 3 images par personne, moyenne sur 5 tirages aléatoires
- Motifs de rejet qualité : {'blurry': 162, 'not_frontal': 79, 'too_dark': 1, 'cut_off': 2, 'too_small': 1, 'no_face': 2}

FNIR = personnes connues non identifiées correctement. FPIR = inconnus acceptés à tort.
Taux calculés sur les images exploitables ; les images écartées sont comptées à part (« non acquises »).
En direct, une image écartée n'est pas une erreur : le système attend une meilleure image de la vidéo.

| Configuration | EER | FNIR à FPIR ≤ 1 % | FNIR / FPIR au seuil par défaut | Sondes non acquises |
|---|---|---|---|---|
| Sans filtre qualité | 0.7 ± 0.4 % | 1.0 % (seuil 1.06) | 99.9 % / 0.0 % | 1 / 485 |
| Avec filtre qualité | 0.5 ± 0.3 % | 0.4 % (seuil 1.07) | 99.7 % / 0.0 % | 112 / 485 |

![DET](det_curve.png)
