# Préparer et modeler

## B01 — Référence, échelle et caméra avant les détails

**Quand / entrées.** Une scène à créer, un décor à reproduire ou des proportions peu crédibles ; référence, destination, au moins une dimension connue si une échelle réelle est nécessaire.

**Procédure.** Décomposer le sujet en volumes et repères mesurables. Choisir les unités et construire une ébauche avec primitives. Pour une photo architecturale, repérer horizon et familles de lignes parallèles ; employer fSpy si disponible et adapté pour estimer caméra et perspective, puis régler l’échelle à partir de la dimension connue. Une photo seule ne donne pas automatiquement une taille absolue. Fixer cette caméra avant les détails qui dépendent de la projection. Comparer coins, silhouette et espaces négatifs à la référence.

**Livrable / contrôle.** Ébauche et vue de caméra superposables à la référence ; dimensions explicites, lignes structurantes alignées. Sans référence métrique, indiquer l’échelle supposée.

**Limites.** Une image très distordue ou sans repères fiables demande un autre calage. Les dimensions de cabane du livre sont celles d’un exemple, sans valeur de norme architecturale.

**Fondements.** Hamdani, chunks 1252188–1252191 ; Acampora 1951818–1951819 ; Tazetdinov 892677. Le contrôle des espaces négatifs et le traitement explicite de l’incertitude métrique sont notre adaptation.

## B02 — Silhouette, topologie et modificateurs réversibles

**Quand / entrées.** Objet mécanique, architecture ou mesh dont le lissage paraît faux ; destination et distance de vue connues.

**Procédure.** Construire le volume avec extrusion, révolution ou symétrie selon sa structure. Séparer les éléments qui n’ont pas besoin d’être soudés. Contrôler doublons involontaires, faces dégénérées, orientation des normales et transformations. Conserver les modificateurs éditables ; vérifier leur ordre sur une copie si le résultat change. Un biseau ne doit être ajouté que si son arrondi est utile au contour ou au reflet ; le dimensionner à l’échelle de l’objet. Comparer un éclairage rasant avant/après. Ajouter une subdivision là où la silhouette le justifie, en gardant la résolution de travail économique.

**Livrable / contrôle.** Mesh éditable, contour voulu, reflets sans cassure parasite, absence de pincement sous subdivision. Une échelle non uniforme peut déformer les biseaux : diagnostiquer avant d’appliquer les transformations, surtout si le modèle est animé ou lié.

**Limites.** Smooth modifie l’ombrage, pas le contour. Des quads partout, un biseau de 2 cm et une pile identique pour tous les objets ne sont pas des exigences universelles.

**Fondements.** Saraja 88943, 88945, 88986, 89013 ; Hamdani 1252210–1252211 ; Acampora 1951819–1951820. Contrôle sur copie et arbitrage selon le livrable : adaptation.

## B03 — Sculpter des masses vers le détail

**Quand / entrées.** Forme organique ; silhouette de référence et budget de géométrie.

**Procédure.** Établir les grandes masses à faible résolution, les formes secondaires au niveau suivant, puis les détails locaux. Employer la multirésolution lorsque la base doit rester exploitable. Isoler une zone si la densité empêche de travailler confortablement. Revenir régulièrement au niveau grossier et à la distance de livraison. Pour un mesh animé ou exporté, décider séparément de la retopologie et du transfert de détails par B07.

**Livrable / contrôle.** Sculpture lisible sans texture et aux résolutions inférieures ; détail utile à la vue finale, coût mesuré.

**Limites.** Saraja décrit le Sculpt de 2.46 : ses restrictions sur l’ajout de sommets ne décrivent pas les outils actuels. Choisir Multires, remeshing ou topologie dynamique selon la version et la conservation requise des UV/shape keys. La retopologie de production n’est pas couverte en profondeur par ces sources.

**Fondements.** Saraja 89038–89041. Le passage vers retopologie/baking est notre articulation avec les sources d’export.

## B04 — Déplier selon la texture et le futur usage

**Quand / entrées.** Texture étirée, coutures visibles ou préparation au baking ; mesh, résolution cible et type de texture.

**Procédure.** Choisir les coutures comme les coupes d’un patron. Pour un cylindre, séparer les extrémités et ouvrir longitudinalement la paroi. Déplier, afficher un damier ou des cases numérotées, corriger étirement et orientation. Contrôler densité relative des texels et espacement des îlots. Smart UV/Cube Projection sont des points de départ rapides pour certains objets, à inspecter sur leur texture réelle. Pour un motif répétitif, le recouvrement peut être intentionnel ; pour une texture peinte ou un baking unique, réserver des îlots distincts.

**Livrable / contrôle.** Damier régulier et matériau à la bonne échelle ; UV présentes après les modificateurs et l’export.

**Limites.** Une texture raccordable ne garantit pas l’absence de couture entre projections différentes. Sur Geometry Nodes, stocker les UV sur le domaine approprié, traiter les faces créées par extrusion et éviter d’écraser les UV existantes.

**Fondements.** Saraja 89160 ; Hamdani 1252263–1252264, 1252266, 1252269, 1252467–1252468 ; Dirksen 862719, 862722 ; Acampora 1951917, 1951919. Contrôles au damier et de densité : adaptation de la procédure.
