# Versions, divergences et portée

Les principes sont reformulés à partir du corpus local. Les corrections et protocoles ajoutés ne sont pas attribués aux auteurs. Le poste de validation possède **Blender 5.1.2**, build `ec6e62d40fa9`, observé le 9 septembre 2026. Ce constat ne fixe pas la version d’un autre poste.

| Source ou conseil | Décision de distillation |
|---|---|
| Saraja : Blender 2.46, Python 2.5, IPO, Blender Internal, ancien moteur de jeu, particules et anciens solveurs | Retenir modélisation, UV, clés, hiérarchies, poids, caches et composition ; vérifier séparément les procédures actuelles. Ne pas rejouer les anciens raccourcis/API. |
| Dirksen 2015 et Josa : exporteur JSON Three.js, ancienne gestion des animations | Retenir UV, pose de repos, limites des formats, textures répétées ; pour un pipeline actuel compatible, utiliser glTF avec contrôle du destinataire. |
| Hamdani : cabane limitée à 3 m, paysage autour de 300 m | Dimensions d’exemples ; rechercher ou obtenir celles du projet. |
| Hamdani : Cycles pour une boucle de déplacement de bruit | La répétition des courbes ne prouve pas un raccord visuel sans saut ; tester la périodicité de la matière. |
| Hamdani : 720 frames à cinq minutes = exactement 60 h | Arithmétique correcte pour le seul temps supposé par frame ; estimation totale à mesurer avec les autres étapes. |
| Hamdani : comparaison AgX / ACES | Choix d’affichage contextualisé ; ne pas attribuer une supériorité générale à un look. |
| Acampora : travailler avec un parent tourné pour Unity | Convention possible de son pipeline ; tester les axes et éviter une double conversion. |
| Acampora : marge de normal baking nulle et distances de projection fixes | Réglages locaux de son exemple ; adapter à la géométrie, aux UV et à la résolution. |
| Acampora : texture SOH | Packing propre à son shader ; n’est pas un format universel Unity ni le packing ORM glTF. |
| Acampora : import « Pack » et scripts auto-exécutés au chargement | Options/dépendances à vérifier dans la version et le projet. Ne pas confondre bibliothèque liée, copie et empaquetage ; ne pas activer des scripts globalement pour appliquer les méthodes. |
| Straccia : image JPEG 4096² d’environ 300 Ko pouvant atteindre 2 Mo en mémoire | Ne pas retenir cet ordre de grandeur : 4096 × 4096 × 4 = 67 108 864 octets, soit 64 Mio en RGBA8, hors mipmaps et sans compression GPU. |
| Uzayr : réduire les polygones pour optimiser | Mesurer d’abord : chargement, textures, transparence et appels de dessin peuvent dominer. Une simplification ne garantit pas un gain. |
| Images, captures et graphiques des livres | L’index texte ne permet pas leur examen intégral. Ne pas prétendre avoir validé visuellement tous les exemples. |

## Vérifications primaires externes

Ces sources complètent la distillation ; elles ne sont pas présentées comme des livres supplémentaires de LibraryBrain. Recherche consultée le 9 septembre 2026, version 5.1. Certains accès directs au manuel anglais ont échoué ; les résultats indexés versionnés ci-dessous ont été consultés et les commandes utiles testées localement.

- [Manuel glTF 2.0 de Blender 5.1](https://docs.blender.org/manual/en/5.1/addons/import_export/scene_gltf2.html) : compatibilité matérielle, normales tangent space, UV et conversion des meshes. Un export exige de contrôler ce que reconnaît l’exporteur.
- [Render Baking, manuel Blender 5.1](https://docs.blender.org/manual/de/5.1/render/cycles/baking.html) : image de destination active par matériau et cuisson de textures pour export.
- [Arguments de commande, manuel Blender 5.1](https://docs.blender.org/manual/de/5.1/advanced/command_line/arguments.html) : `--python-exit-code` rend détectable une exception Python des scripts lancés en ligne de commande.
- [Gestion des couleurs, manuel Blender 5.1](https://docs.blender.org/manual/sr/5.1/render/color_management/index.html) : gestion des espaces, affichages et pipeline colorimétrique.

## Validation à ne pas confondre

La provenance vérifie l’existence et l’empreinte des passages. Le contrôle de skill vérifie sa structure. Les tests d’intégration vérifient son chargement et sa sélection. Un essai Blender peut vérifier géométrie, animation, export et rendu sur un cas borné. Aucun de ces contrôles ne démontre que les 21 méthodes sont toutes validées en production, ni un gain de compétence du modèle sur un benchmark indépendant.
