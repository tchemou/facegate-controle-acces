# Anti-spoofing: detection passive d'ecran (evaluation simulee)

Visages reels (LFW): 278 images. Attaques ecran **simulees** (3 niveaux de grille).

| | min | mediane | p95 | p99 | max |
|---|---|---|---|---|---|
| reels | 8.0 | 18.1 | 28.9 | 34.5 | 55.4 |
| ecran visible (0,80) | 65.0 | 185.4 | 283.3 | 313.9 | 334.3 |
| ecran discret (0,92) | 32.7 | 88.5 | 145.3 | 182.0 | 194.2 |
| ecran ecran HD (0,97) | 14.4 | 36.4 | 55.5 | 63.4 | 73.4 |

Seuil retenu (99e percentile des reels): **34.5** (faux rejet par image ~1 %).
- Faux rejets (reels > seuil): 1.1 %
- Ecran simule visible (0,80): 100.0 % detecte
- Ecran simule discret (0,92): 99.3 % detecte
- Ecran simule ecran HD (0,97): 58.9 % detecte

Limites : attaque simulee (pas de vraie captation), visages LFW 250 px et non webcam ;
a re-mesurer avec de vrais enregistrements avant toute conclusion de securite.
