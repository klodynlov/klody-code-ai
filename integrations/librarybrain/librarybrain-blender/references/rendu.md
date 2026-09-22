# Éclairage, rendu et image finale

## B14 — Éclairer pour révéler la matière et l’intention

**Quand / entrées.** Image plate, reflets peu lisibles ou intégration à une photo ; caméra, référence et moteur cible.

**Procédure.** Stabiliser exposition et transformation d’affichage. Construire une lumière principale avec direction, taille apparente et contraste choisis, puis doser l’environnement et les lumières de soutien. Tester un soleil/ciel ou une HDRI selon le contexte ; tourner l’environnement pour placer ombres et reflets. La température de couleur fournit un réglage cohérent, à confronter à la référence. Pour une incrustation, faire correspondre direction, douceur, teinte et ombres de contact aux images réelles. Recontrôler les matériaux avec un éclairage simple si la source du défaut reste incertaine.

**Livrable / contrôle.** Sujet lisible, relief et matière observables, cohérence entre objet et fond.

**Limites.** Les températures, intensités et looks des exemples ne sont pas des constantes. Une HDRI de prévisualisation différente du World de la scène peut expliquer un écart de rendu. Ne pas changer les couleurs des matériaux pour compenser une exposition erronée sans diagnostic.

**Fondements.** Hamdani 1252327, 1252343 ; Hutson/Smith 282755 ; Tazetdinov 892677. Procédure de diagnostic sous lumière simple : adaptation.

## B15 — Converger avec de petits rendus, puis composer

**Quand / entrées.** Bruit, rendu lent, animation à livrer ou compositing ; résolution, cadence, durée et critère visuel.

**Procédure.** Choisir le moteur selon le résultat à vérifier. Rendre une petite image, puis un crop critique à la résolution finale. Comparer sans/avec débruitage : surfaces fines, transparences, reflets et détail peuvent se dégrader. Augmenter échantillons ou corriger la cause du bruit selon ce constat. Chronométrer des frames représentatives, incluant une frame coûteuse ; estimer la durée totale avec chargement, préparation, écriture et composition. Pour l’animation, préférer une séquence d’images reprenable avant encodage. Composer les passes utiles avec un effet à la fois ; garder un avant/après et vérifier les zones sombres et hautes lumières. Employer le séquenceur pour le montage temporel.

**Livrable / contrôle.** Rendu témoin approuvé, estimation documentée, images sauvegardées, montage sans image manquante ni décalage de cadence.

**Limites.** Une limite de rendu de cinq minutes n’est pas une durée totale exacte par frame. 720 × 5 min = 60 h ne couvre pas toute la chaîne. Le débruitage ne prouve pas la convergence ; une animation peut scintiller malgré des images fixes convaincantes. Glare/color grading ne corrigent pas une géométrie ou un éclairage mal établis.

**Fondements.** Hamdani 1252560–1252562, 1252571, 1252574 ; Saraja 89469–89471, 89484, 89501 ; Hutson/Smith 282755. Estimation avec surcoûts, crops et test temporel : adaptation.
