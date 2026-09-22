# Sources et couverture Blender

Inventaire figé le 2026-09-09 : 138 sources candidates dans 25555 entrées du catalogue. Recherche FTS : `blender* OR bpy OR "geometry nodes" OR bmesh` ; recherche complémentaire dans les titres, chemins et catégories. **3 livres dédiés + 8 compléments retenus ; 21 méthodes ; 179 passages référencés, identité comprise.**

Tous les ouvrages dédiés repérés sont représentés. Les chapitres ont été cartographiés et les passages utiles sélectionnés ; ce relevé ne prétend pas qu’intégralité du texte et des figures a été lue. Les titres seuls, sommaires et digests ne sont pas des preuves de méthode.

## Sources retenues

| ID LibraryBrain | Source / auteur | Édition et rôle | Méthodes |
|---|---|---|---|
| 400 | La 3D libre avec Blender — Olivier Saraja | 3e édition, 2008, Blender 2.46 ; core | B02, B03, B04, B10, B15, B16, B17, B18 |
| 8408 | 3D Environment Design with Blender 5 — Abdelilah Hamdani | 2e édition, mars 2026 ; core | B01, B02, B04, B05, B06, B09, B10, B11, B12, B13, B14, B15, B18 |
| 26489 | 3D Game Development with Blender 5 and Unity 6 — Paolo Acampora | mars 2026, exemples Blender 5.1 / Unity 6.3 ; core | B01, B02, B04, B06, B07, B08, B09, B17, B19, B21 |
| 6193 | Interactive Web Development With Three.js and A-Frame — Alessandro Straccia | 2024 ; complement | B07, B12, B16, B17, B19 |
| 6116 | Learning Three.js – the JavaScript 3D Library for WebGL — Jos Dirksen | 2e édition, 2015 ; API historiques ; complement | B04, B19 |
| 6174 | 3D Web Development with Three.js and Next.js — Andrei Tazetdinov | édition non résolue ; pagination EPUB indexée ; complement | B01, B14, B19 |
| 6190 | How to Design 3D Games with Web Technology – Book 01 — Jordi Josa | édition non résolue ; anciens exporteurs JSON ; complement | B19 |
| 6114 | 3D Data Science with Python — Florent Poux | édition non résolue ; pagination EPUB indexée ; complement | B20 |
| 355 | Pratique de l’impression 3D — Collectif Make, compilé par Anna Kaziunas France | métadonnée auteur RTT corrigée ici depuis le frontispice, sans modifier le catalogue ; complement | B20 |
| 6181 | Conquering JavaScript: Three.js — Sufyan bin Uzayr | édition non résolue ; complement | B19 |
| 2262 | Cinematic Algorithms — James Hutson et Andrew Allen Smith | 1re édition, 2025 ; complement | B14, B15 |

## Couverture des trois livres dédiés

### La 3D libre avec Blender

- **1–2 : prise en main et interface** → B01, B21. Principes d’inspection ; gestes 2.46 historiques.
- **3–4 : premier objet et techniques de modélisation** → B02, B03. Volume, normales, subdivision et sculpture ; pas toutes les variantes de commandes.
- **5 : matériaux, textures et UV** → B04, B05. Principes ; shaders du moteur interne non transposés.
- **6 : éclairage** → B14. Famille couverte prioritairement par l’ouvrage récent ; radiosité ancienne non reprise.
- **7 : animation fondamentale et particules** → B10, B16, B18. Clés, interpolation, poids ; recettes particules historiques.
- **8 : animation avancée** → B16, B17, B18. NLA, rigging, cloth, fluides ; implémentations anciennes à adapter.
- **9 : rendu et composition** → B15. Séquence d’images, montage, passes ; anciens codecs et boutons écartés.
- **Annexes, index et DVD** → hors méthode. Repérage/identité seulement ; pas de reproduction des listes de raccourcis ou contenu du DVD.

### 3D Environment Design with Blender 5

- **1 : échelle, photo et biseaux** → B01, B02. Dimensions d’exemple contextualisées.
- **2 : matériaux réalistes** → B05, B06. Maps, coordonnées, procédural et affichage.
- **3 : UV** → B04. Dépliage et contrôle de texture.
- **4 : plantes et dispersion** → B10, B12. Masques, instances et variation.
- **5 : éclairage** → B14. Soleil, température, ciel, HDRI.
- **6 : paysages** → B11. Terrain, échelle, densité, orientation des normales.
- **7 : eau et animation** → B13. Approximation visuelle et limite de la boucle de bruit.
- **8–9 : boue et masques de paysage** → B06, B11. Groupes, paramètres et masques.
- **10 : rochers** → B04, B12. UV et optimisation d’un asset répétitif.
- **11–12 : fleurs et arbres** → B09, B12. Plans alpha, pivots et résolution.
- **13 : dispersion du décor** → B10. Familles d’assets et zones.
- **14 : rendu et compositing** → B14, B15. Échantillons, débruitage, coût, couleur, glare.
- **15, index et contenus promotionnels** → hors méthode. Hors méthodes.

### 3D Game Development with Blender 5 and Unity 6

- **1–2 : Blender et Unity** → B19. Interopérabilité et coordonnées ; installation Unity hors scope.
- **3 : prototype du niveau** → B01. Blocking comme référence ; ProBuilder/C# hors scope du skill Blender.
- **4 : personnalisation** → B09, B21. Collections, assets, opérateurs ; pas de modification globale imposée.
- **5 : Geometry Nodes** → B08. Décomposition, silhouette, paramètres.
- **6 : assemblage** → B08, B09, B19, B21. Réemploi et export ; auto-exécution non imposée.
- **7 : matériaux** → B05, B07, B19. UV générées, textures et packing spécifique.
- **8 : baking des détails** → B07, B08. Instances, UV et projection entre meshes.
- **9 : gameplay et animation** → B16, B17, B19. Retenir armature simple et animation ; contrôleur Unity hors scope.
- **10, index et contenus promotionnels** → hors méthode. Hors méthodes.

## Sélection et limites

Répartition : complement : 8, core : 3, excluded_bibliography : 6, excluded_derivative : 1, excluded_homonym : 93, excluded_incidental : 27.

Le relevé complet, avec IDs, décisions, motifs et passages de repérage, est conservé dans le rapport local `reports/blender/source-inventory.json` du projet LibraryBrainSkills. Les homonymes culinaires, médicaux, LLM-Blender et Blenderbot ont été écartés ; le digest Three.js n’est pas compté comme source indépendante. Les correspondances exactes de checksum sont contrôlées, mais ce contrôle n’est pas une détection exhaustive de toutes les éditions dérivées.

Les ouvrages récents détaillent surtout les environnements et le transfert vers des moteurs. Sculpture, rigging, cloth et fluides tirent une part importante de leurs principes du livre de 2008. Retopologie avancée, cheveux modernes, simulation de production, Grease Pencil et motion tracking ne disposent pas ici d’une couverture suffisante pour en annoncer la maîtrise.

## Retrouver un passage

Ouvrir `~/library_brain.db` avec SQLite `mode=ro` ; sélectionner `chunks.text` pour le `id` enregistré dans [provenance.json](provenance.json). Vérifier `book_id` et SHA-256 UTF-8 avant réutilisation. Une réindexation peut changer les identifiants : retrouver alors le livre et le passage, puis refaire la vérification. **Page indexée ≠ pagination imprimée**, particulièrement pour les EPUB.

Les fiches sont une reformulation originale. Le skill contient des localisateurs et empreintes, pas une copie des livres. Les contrôles et arbitrages ajoutés sont décrits dans chaque fiche et dans [compatibilite.md](compatibilite.md).
