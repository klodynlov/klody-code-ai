# Matériaux et transfert de détails

## B05 — Diagnostiquer un matériau par ses composantes

**Quand / entrées.** Matière trop plastique, relief incohérent ou apparence différente à l’export ; maps, échelle et éclairage de référence.

**Procédure.** Tester séparément couleur de base, métal/rugosité, normale et relief. Lire une map couleur dans son espace déclaré ; lire rugosité et normales comme données (Non-Color). Passer une normal map par un nœud Normal Map adapté à son espace, pas directement comme une couleur de surface. Ajuster l’échelle du motif avant son intensité. Employer bump/normal pour les détails d’ombrage ; choisir displacement lorsque contour ou géométrie doivent réellement changer, avec densité et moteur compatibles. Comparer sous un éclairage fixe puis un éclairage rasant.

**Livrable / contrôle.** Matériau dont chaque canal a un rôle identifié ; relief à la bonne échelle sans altération involontaire de la couleur.

**Limites.** « PBR » ne garantit ni qualité des maps ni réalisme. Une lumière trompeuse ou un changement de transformation d’affichage peut masquer une erreur. Le choix d’AgX/ACES relève du pipeline, pas d’un verdict universel.

**Fondements.** Hamdani 1252228, 1252231, 1252255, 1252425–1252427. Mise à jour d’export : manuel officiel glTF 5.1 cité dans compatibilite.md.

## B06 — Construire un matériau procédural contrôlable

**Quand / entrées.** Bois, boue, sol ou variation répétable ; référence de matière, échelle du motif et paramètres utiles.

**Procédure.** Décomposer le résultat en structures : forme générale du motif, répartition des matières, petites variations et relief. Construire un masque lisible en noir et blanc avant de mélanger les couleurs ou shaders. Orienter les coordonnées selon la matière (fil du bois, stratification, altitude). Régler rugosité et relief en fonction de cette structure, sans brancher le même bruit partout par défaut. Organiser le graphe en groupes nommés et exposer seulement les contrôles utiles : taille, proportion, variation, rugosité, force du relief. Tester leurs extrêmes sur un objet simple avant le décor.

**Livrable / contrôle.** Groupe réutilisable, variation reproductible et matière cohérente sur plusieurs dimensions d’objet.

**Limites.** Les variations ne remplacent pas une référence physique ou artistique. Un graphe Blender ne se transfère pas intégralement à un moteur externe : prévoir B07/B19.

**Fondements.** Hamdani 1252412, 1252425, 1252439–1252440, 1252445–1252448 ; Acampora 1951903. Décomposition et test des extrêmes : synthèse adaptée.

## B07 — Transférer les détails par baking

**Quand / entrées.** Modèle trop dense ou shader procédural destiné au jeu/web ; version détaillée, version légère, UV, maps et logiciel cibles.

**Procédure.** Conserver les originaux. Aligner les versions détaillée et légère. Préparer une image de destination et un nœud Image Texture actif dans chaque matériau concerné. Pour un transfert entre meshes, sélectionner la source puis rendre la cible légère active ; régler Selected to Active et une cage ou des distances de projection proportionnées au relief. Cuire un petit essai, inspecter trous, projections voisines, coutures et sens des normales. Régler la marge selon UV/résolution/mipmaps. Pour une albedo indépendante de l’éclairage, exclure les contributions lumineuses correspondantes. Sauver les images produites, reconstruire un matériau simple et vérifier dans le moteur cible.

**Livrable / contrôle.** Asset léger, textures enregistrées, silhouette conservée, rendu comparable à la distance prévue.

**Limites.** Une normal map ne restitue pas le contour. La marge nulle de l’exemple d’Acampora n’est pas une règle. Le packing SOH de son shader Unity n’est pas le packing ORM glTF : documenter chaque canal et convertir la rugosité en douceur uniquement si le shader destinataire l’exige. Ne pas cuire l’éclairage dans la couleur d’un objet qui doit être rééclairé dynamiquement.

**Fondements.** Straccia 901112, 901114–901117 ; Acampora 1951905, 1951907, 1951960, 1951963 ; manuel officiel baking 5.1. Le protocole de comparaison et les conditions de marge sont notre adaptation.
