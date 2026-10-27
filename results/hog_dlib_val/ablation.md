# Ablation sur data\lfw_val (8 tirages)

| Configuration | EER | FNIR à FPIR ≤ 1 % | Images écartées |
|---|---|---|---|
| Référence naïve : 1 image d'inscription, sans filtre, sans marge | 6.5 ± 1.5 % | 30.6 ± 14.4 % | 0 % |
| + 3 images d'inscription | 4.0 ± 1.3 % | 23.3 ± 14.1 % | 0 % |
| + 8 images d'inscription | 2.5 ± 1.0 % | 12.2 ± 8.3 % | 0 % |
| + filtre qualité (8 images) | 2.7 ± 1.1 % | 13.1 ± 9.3 % | 34 % |
| + marge entre identités (8 images, filtre) | 2.3 ± 1.4 % | 12.3 ± 10.2 % | 34 % |
