# Livrer un asset utilisable

## B19 — Exporter et mesurer dans la destination

**Quand / entrées.** Unity, Three.js/web ou autre moteur ; format accepté, unités/axes, shader, animations et budget d’affichage.

**Procédure.** Créer un petit asset de référence asymétrique avec une dimension connue et une animation courte. Choisir FBX pour le pipeline Unity qui le requiert, glTF/GLB pour un pipeline web compatible. Préparer seulement les objets à livrer ; conserver le `.blend` source. Vérifier pivot, hiérarchie et conversion d’axes une seule fois : le parent tourné de l’exemple Unity est une convention de projet, à ne pas cumuler aveuglément avec la conversion de l’exporteur. Cuire les matières non transportables et expliciter les canaux. Après export, rouvrir dans la destination : taille, orientation, matériaux, UV, transparence, clips et textures doivent être effectivement présents.

Pour le web, mesurer temps de chargement, mémoire, triangles, appels de dessin et fluidité sur la cible. Réduire le coût qui domine : géométrie, textures, transparence ou nombre d’objets. Utiliser les motifs répétés quand ils conviennent ; ne pas sacrifier la silhouette pour une réduction de triangles sans bénéfice mesuré. Si compression, vérifier le décodeur côté client. Tester les interactions prévues et l’accès clavier lorsqu’elles font partie du livrable.

**Livrable / contrôle.** Asset chargé dans le logiciel cible, dimensions/animation confirmées, comparaison visible et mesures de coût.

**Limites.** Le compte de sommets exporté peut augmenter aux coutures UV/normales ; le nombre du mesh source n’est pas un invariant. Un PNG/JPEG léger peut consommer beaucoup de mémoire une fois décodé : RGBA8 4096² vaut 64 Mio sans mipmaps ni compression GPU. Les pipelines JSONLoader/anciens plugins Three.js sont historiques. Un réimport Blender est un contrôle utile, mais ne prouve pas le rendu Unity/Three.js.

**Fondements.** Acampora 1951737, 1951741–1951743, 1951905–1951907, 1951917–1951919 ; Straccia 900989–900995, 901104–901105, 901126 ; Josa 899291, 899313 ; Dirksen 862687, 862719–862722 ; Uzayr 895709 ; Tazetdinov 892864 ; manuel glTF 5.1. Calcul mémoire, test asymétrique et non-cumul des conversions : adaptations explicites.

## B20 — Préparer un mesh pour la fabrication

**Quand / entrées.** Impression d’un objet ou d’un scan ; procédé, matière, dimensions, contraintes du trancheur/imprimante.

**Procédure.** Vérifier l’échelle d’un objet de dimensions connues. Examiner frontières ouvertes, normales, intersections, épaisseur et petits détails. Pour un scan, contrôler d’abord bruit et orientation des normales ; ne pas masquer une reconstruction défectueuse par une simplification agressive. Réparer uniquement les ouvertures qui doivent être fermées, et conserver les cavités fonctionnelles. Choisir orientation et supports selon le procédé. Exporter le format accepté, rouvrir dans le trancheur et inspecter plusieurs couches, les dimensions et les parois fines.

**Livrable / contrôle.** Mesh et aperçu de tranchage cohérents avec les dimensions et contraintes fournies.

**Limites.** Un mesh étanche ne suffit pas à garantir une impression. Ne pas imposer une épaisseur minimale universelle ni déclarer la pièce fabricable sans contrôler le procédé. Un asset destiné à l’affichage n’a pas nécessairement besoin d’être fermé.

**Fondements.** Collectif Make, compilé par Anna Kaziunas France, 98277 ; Poux 861525–861526. Inspection des couches et traitement des cavités fonctionnelles : adaptation.
