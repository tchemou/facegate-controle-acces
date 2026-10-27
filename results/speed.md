# Latence par étape (image 640x480, moyenne de 40 passages, CPU)

| Détecteur | Empreinte | Détection | 68 points | Empreinte | Total | Images/s |
|---|---|---|---|---|---|---|
| hog | dlib | 41.2 ms | 2.7 ms | 399.8 ms | 443.7 ms | 2 |
| hog | sface | 40.9 ms | 1.4 ms | 11.0 ms | 53.3 ms | 19 |
| yunet | sface | 12.6 ms | 2.3 ms | 10.0 ms | 24.8 ms | 40 |
| yunet | dlib | 12.8 ms | 2.0 ms | 402.8 ms | 417.5 ms | 2 |
