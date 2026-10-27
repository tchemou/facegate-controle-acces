# Ablation sur data\lfw (8 tirages)

| Configuration | EER | FNIR à FPIR ≤ 1 % | Images écartées |
|---|---|---|---|
| Référence naïve : 1 image d'inscription, sans filtre, sans marge | 7.2 ± 1.7 % | 26.9 ± 16.3 % | 0 % |
| + 3 images d'inscription | 3.7 ± 0.6 % | 20.1 ± 16.5 % | 0 % |
| + 8 images d'inscription | 2.5 ± 0.4 % | 11.7 ± 9.4 % | 1 % |
| + filtre qualité (8 images) | 2.2 ± 0.8 % | 10.0 ± 8.5 % | 32 % |
| + marge entre identités (8 images, filtre) | 2.1 ± 0.9 % | 9.9 ± 8.7 % | 32 % |
