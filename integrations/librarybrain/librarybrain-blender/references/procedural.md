# Geometry Nodes, assets et environnements

## B08 — Paramétrer une famille de modèles

**Quand / entrées.** Colonnes, arches, modules ou variantes ; dimensions, silhouette et plages utiles.

**Procédure.** Décomposer l’objet en parties dont les paramètres sont indépendants. Construire un premier graphe avec primitives, transformations et assemblage ; exposer largeur, hauteur, profil ou résolution. Distinguer paramètres de forme et paramètres de densité afin que changer la résolution préserve la silhouette. Nommer les entrées et organiser les liens. Tester la valeur courante, les bornes et une combinaison difficile. Pour une division, protéger les dénominateurs nuls ; définir un domaine valide plutôt que produire un mesh dégénéré.

**Livrable / contrôle.** Groupe générant des variantes cohérentes, avec UV/matériaux conservés où nécessaires.

**Limites.** Les types de sockets et API d’interface de groupe doivent être vérifiés dans la version locale. Réaliser les instances seulement lorsqu’une opération de mesh, un baking ou un export l’exige.

**Fondements.** Acampora 1951818–1951820, 1951835, 1951837, 1951942–1951944. Tests aux bornes et protection des divisions : adaptation d’ingénierie.

## B09 — Constituer des assets réutilisables

**Quand / entrées.** Même modèle dans plusieurs scènes ; identité, pivot, dépendances et politique de mise à jour.

**Procédure.** Séparer modèle de production, référence et objets de présentation. Nommer les collections et groupes ; marquer les éléments pertinents comme assets et les classer dans un catalogue. Choisir Link si les modifications de la bibliothèque doivent se propager, Append pour une copie indépendante. Placer le pivot là où l’objet doit se poser ou tourner. Sauver, ouvrir une autre scène de travail et tester l’insertion avec textures et dimensions.

**Livrable / contrôle.** Asset insérable avec comportement de mise à jour connu et ressources retrouvées.

**Limites.** Le catalogue ne remplace pas le fichier source. Ne pas confondre empaquetage de ressources et méthode d’import ; inspecter les options disponibles. Les réglages globaux de démarrage du tutoriel ne sont pas nécessaires pour créer une bibliothèque de projet.

**Fondements.** Acampora 1951791, 1951794–1951795 ; Hamdani 1252520. Contrôle dans une seconde scène : adaptation.

## B10 — Disperser selon des masques et une intention

**Quand / entrées.** Herbe, rochers, forêt ; surface, assets à l’échelle et zones de présence/exclusion.

**Procédure.** Préparer quelques assets légers. Définir un masque par famille : chemin vide, plantes au sol, rochers localisés. Tester d’abord le masque seul, puis la distribution des points et les instances. Faire varier orientation et taille dans des plages crédibles, avec graine stable pour comparer deux réglages. Contrôler pivot et axe de croissance : un tronc n’a pas forcément à suivre toute inclinaison de la surface. Ajuster densité et groupes avant de multiplier les variantes.

**Livrable / contrôle.** Répartition visible conforme aux masques, sans objets sur le chemin ni répétition flagrante ; coûts comparés sur une zone puis sur la scène entière.

**Limites.** Si l’asset « Scatter on Surface » du livre manque, construire l’équivalent avec distribution de points, instances et attributs disponibles. Ne pas présumer qu’un nom d’asset est un nœud intrinsèque installé. Préserver les instances tant que possible.

**Fondements.** Hamdani 1252306, 1252309–1252314, 1252531 ; Saraja 89338 pour le principe historique du contrôle par poids. Graine, axes et mesure par zone : adaptation.

## B11 — Terrain : forme d’abord, masques ensuite

**Quand / entrées.** Paysage avec relief, neige, roche, boue ou rivière ; cadre, dimensions et zones fonctionnelles.

**Procédure.** Générer ou modeler une ébauche à faible résolution. A.N.T. Landscape est une option si l’extension est disponible ; une grille déformée suffit pour éprouver le principe. Régler grandes masses et lit de rivière, puis dimensions. Construire séparément les masques de hauteur et d’orientation/pente : coordonnée Z et composante Z de la normale ne décrivent pas la même chose. Visualiser les masques, ensuite mélanger les matériaux via des groupes. Densifier selon la caméra et les déformations nécessaires.

**Livrable / contrôle.** Terrain lisible, transitions de matières explicables, résolution soutenable ; la rivière occupe le creux voulu.

**Limites.** Les 300 m et subdivisions 512 du livre sont des choix de scène. Le modèle est un décor artistique, sans garantie d’exactitude géologique ou hydrologique.

**Fondements.** Hamdani 1252350–1252356, 1252363, 1252371, 1252445–1252448. Séparer explicitement pente/altitude et offrir une grille de repli : adaptation.

## B12 — Assets naturels au coût adapté à la caméra

**Quand / entrées.** Rochers, fleurs et arbres en grand nombre ; taille à l’écran et distance minimale.

**Procédure.** Valider un exemplaire avant sa dispersion. Réduire subdivisions et résolution des branches tant que le contour reste acceptable. Pour des feuilles éloignées, comparer plans avec alpha et géométrie découpée ; modeler le volume qui contribue à la silhouette. Placer le pivot des branches à leur base, orienter les UV et contrôler l’apparence des deux faces. Comparer le coût de la géométrie et celui des surfaces transparentes qui se recouvrent.

**Livrable / contrôle.** Asset léger dont contour, transparence et texture tiennent à la distance prévue ; test sur plusieurs exemplaires.

**Limites.** Un plan alpha ne convient pas à toute vue rapprochée. Ne pas déduire la qualité d’un simple pourcentage de réduction ; les chiffres d’arbre du livre décrivent son exemple.

**Fondements.** Hamdani 1252467–1252468, 1252496–1252497, 1252512–1252514, 1252520 ; Straccia 900993, 900995. Comparaison du coût de transparence : adaptation pour le temps réel.

## B13 — Eau stylisée ou simulée : choisir le coût utile

**Quand / entrées.** Rivière ou surface animée ; vue, durée, cadence et présence d’interactions.

**Procédure.** Pour une surface sans interaction complexe, prototyper un shader d’eau et une variation de normales ou déplacement mesuré. Définir l’échelle et la direction de l’ondulation, puis animer les coordonnées. Pour une boucle, mesurer l’intervalle en frames et vérifier visuellement le passage fin→début ; un modificateur Cycles ne rend pas un bruit non périodique sans couture. Si nécessaire, employer un paramètre périodique ou une transition construite. Pour éclaboussures/collisions demandées, passer à B18.

**Livrable / contrôle.** Surface convaincante depuis la caméra et animation sans saut au raccord ; géométrie et ombrage distingués.

**Limites.** Le mélange de shaders du livre est explicitement une approximation visuelle. Deux clés aux frames 1 et 101 sont séparées de 100 intervalles ; ne pas confondre durée de l’intervalle et nombre d’images incluses à exporter.

**Fondements.** Hamdani 1252387, 1252393, 1252400–1252401. Contrôle de périodicité et des bornes temporelles : adaptation corrective.
