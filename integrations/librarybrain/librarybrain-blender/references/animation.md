# Animation et simulation

## B16 — Clés, interpolation et actions lisibles

**Quand / entrées.** Objet, caméra ou visage à animer ; poses, durée, cadence et résultat attendu.

**Procédure.** Définir poses ou valeurs principales et leur timing avant les détails. Animer seulement les propriétés utiles. Choisir interpolation constante pour des changements discrets, linéaire pour une vitesse constante du paramètre, courbe pour un mouvement nuancé ; inspecter les dépassements. Pour des shape keys, préserver la correspondance des sommets et la base de référence ; combiner des formes nommées et en contrôler les valeurs. Nommer les actions et leurs plages, puis assembler les clips avec leurs modes d’influence choisis. Tester poses intermédiaires, début/fin, raccord et export.

**Livrable / contrôle.** Animation évaluée sur plusieurs frames après réouverture, pas seulement des clés présentes dans le fichier.

**Limites.** Une interpolation linéaire d’un paramètre ne garantit pas une vitesse spatiale constante le long d’un chemin. Les IPO et instructions d’export d’une seule action des anciens livres sont historiques. L’API des actions a changé : consulter les structures de la version locale.

**Fondements.** Saraja 89283–89288, 89295–89296, 89434–89443 ; Straccia 900989, 901126. Vérification après réouverture et distinction vitesse/paramètre : adaptation.

## B17 — Rig : hiérarchie, contraintes et poids

**Quand / entrées.** Personnage ou mécanisme déformé incorrectement ; mesh, pose de repos, articulations et amplitudes nécessaires.

**Procédure.** Construire une hiérarchie de bones nommés autour des articulations utiles. Distinguer parentage rigide et déformation de mesh. Contrôler la pose de repos et l’espace des transformations avant le skinning. Utiliser les poids automatiques comme départ, puis regarder les plis aux poses extrêmes et corriger les groupes. Ajouter IK lorsqu’une extrémité doit piloter une chaîne ; borner la chaîne et ses degrés de liberté selon le mouvement. Examiner les contraintes dans leur ordre et leur espace d’évaluation, plutôt que compenser un conflit par des poids arbitraires.

**Livrable / contrôle.** Poses extrêmes acceptables, pas de région entraînée par un bone étranger, contrôleur prévisible et retour cohérent à la pose de repos.

**Limites.** Le chapitre récent couvre une armature simple, pas un rig facial ou anatomique de production. Les raccourcis et contraintes de 2.46 ne doivent pas être recopiés ; les tests de déformation restent nécessaires après décimation/export.

**Fondements.** Saraja 89419–89420, 89425–89428, 89431–89433 ; Acampora 1952000, 1952002 ; Straccia 900995. Batterie de poses et diagnostic des espaces : adaptation.

## B18 — Simuler une scène minimale avant d’augmenter la résolution

**Quand / entrées.** Chute, tissu, corps souple, liquide ou effets de particules ; échelle, plage de frames, collisions et budget.

**Procédure.** Choisir la famille selon le phénomène : corps rigide pour un mouvement sans déformation, cloth pour une étoffe, soft body pour une déformation élastique, fluide pour un écoulement volumique. Prototyper avec un objet et un obstacle. Contrôler dimensions, géométrie des collisions, état initial et chevauchements problématiques. Pour un tissu, définir les points fixés par groupes et tester contacts/auto-collisions. Pour un fluide, limiter le domaine au volume nécessaire et démarrer à faible résolution. Sauver le cache de l’essai, puis augmenter qualité ou sous-pas selon la cause observée. Après modification d’un paramètre pertinent, invalider/recalculer le cache concerné et vérifier la nouvelle animation.

**Livrable / contrôle.** Plusieurs instants inspectés, collisions plausibles, cache identifié et résultat reproductible après réouverture.

**Limites.** Les simulateurs et panneaux du livre de 2008 sont anciens. Ses recettes Boids, particules et moteur de jeu ne sont pas des procédures modernes validées. Les méthodes ici ne constituent pas une validation physique ; cheveux, fumée détaillée et simulation avancée nécessitent des sources récentes et un test ciblé.

**Fondements.** Saraja 89338, 89400–89401, 89448–89449 ; Hamdani 1252189 pour l’échelle. Protocole minimal, suivi du cache et choix de solveur : synthèse adaptée, à vérifier sur la version exécutée.
