# Ablation sur data\lfw (8 tirages)

| Configuration | EER | FNIR à FPIR ≤ 1 % | Images écartées |
|---|---|---|---|
| Référence naïve : 1 image d'inscription, sans filtre, sans marge | 2.2 ± 1.0 % | 3.5 ± 1.8 % | 0 % |
| + 3 images d'inscription | 1.2 ± 0.4 % | 1.3 ± 0.7 % | 0 % |
| + 8 images d'inscription | 0.7 ± 0.2 % | 0.8 ± 0.3 % | 0 % |
| + filtre qualité (8 images) | 0.9 ± 0.3 % | 0.9 ± 0.3 % | 21 % |
| + marge entre identités (8 images, filtre) | 0.9 ± 0.4 % | 0.9 ± 0.4 % | 21 % |
