# Méthodes Unity

## U01 — Construire une tranche complète

**Quand / entrées.** Nouvelle expérience ; utilisateur, interaction centrale, cible et critère observable.

**Procédure.** Choisir une seule interaction qui exprime la valeur du projet. La faire traverser toute la chaîne : asset simple, scène, commande, réaction et application lancée. Dessiner ou bloquer les volumes avant le détail. Dans un projet neuf, choisir un template compatible avec la cible ; URP convient au prototype Signal, mais ne justifie pas de convertir un projet HDRP ou Built-in existant. Figer les versions réellement résolues et éviter d’ajouter des packages sans besoin identifié.

**Contrôle.** Une personne peut déclencher l’interaction et comprendre son effet sans ouvrir l’éditeur. Documenter ce que l’essai tranche et ce qu’il laisse ouvert.

**Limites / fondements.** Acampora 1951727–1951729, 1951765–1951769 : projet URP, plan et volumes testés avant le détail. La tranche incluant le build et l’interaction minimale est notre adaptation. Le prototype ne valide ni l’intérêt commercial ni la qualité d’un jeu complet.

## U02 — Établir le contrat d’import

**Quand / entrées.** Objet Blender à importer ; unités, pivots, orientation, déformations et clips attendus.

**Procédure.** Garder le `.blend` source hors du dossier Unity `Assets` et exporter un FBX explicite pour ce pipeline. Sélectionner les objets livrés, préparer les modificateurs et les UV, fixer les axes de l’exporteur. Utiliser un repère asymétrique ou un mouvement vertical pour contrôler le sens, pas seulement un cube symétrique. Réimporter dans Unity et mesurer les dimensions en espace monde, la hiérarchie, les UV et plusieurs instants d’un clip. Un cube de 1 m dans Blender doit garder cette dimension avec les paramètres convenus. Ne pas cumuler une rotation compensatrice de parent avec celle de l’exporteur sans mesure.

**Contrôle.** Import sans assets inattendus ; dimension et mouvement évalués dans Unity ; forme, normales et matériaux visibles dans le player.

**Limites / fondements.** Acampora 1951738, 1951741–1951743, 1951917–1951919 ; manuel officiel des formats propriétaires. Le livre utilise aussi l’import direct `.blend` et un parent tourné. Notre contrat préfère FBX explicite pour maîtriser la conversion, conformément à la recommandation de production du manuel. Un réimport Blender ou un nombre de sommets égal ne suffit pas : coutures UV et normales peuvent dédoubler des sommets.

## U03 — Reconstruire le matériau dans le pipeline cible

**Quand / entrées.** Asset importé, pipeline réellement actif, maps et convention des canaux.

**Procédure.** Lire le pipeline par défaut et son éventuelle surcharge dans Quality ; un champ Graphics vide ne prouve pas Built-in. Assigner un shader du pipeline actif. Reconstituer couleur, rugosité/smoothness, métal, normales et transparence depuis des entrées explicites. Pour une carte de rugosité, convertir vers smoothness uniquement si le shader attend cette convention. Déclarer une normal map comme telle à l’import. Cuire les graphes Blender non transportables. Vérifier la réponse à un éclairage témoin ; comparer à exposition et transformation couleur fixées.

**Contrôle.** Aucun shader manquant, normales orientées correctement, transparence choisie volontairement, maps présentes dans le build. Tester aussi les coutures et le tiling.

**Limites / fondements.** Acampora 1951757, 1951905–1951907, 1951917–1951919 ; manuel URP Lit. Le packing SOH de l’ouvrage est propre à son shader, pas un format universel. La vérification Graphics + Quality vient du diagnostic réel de Signal. Un transfert des noms de matériaux ne prouve pas une équivalence du rendu.

## U04 — Assembler sans perdre les références

**Quand / entrées.** Asset réutilisé, remplacement d’un bloc, mise à jour de modèle.

**Procédure.** Séparer l’asset importé et l’objet de gameplay qui le référence : prefab parent ou variante appropriée. Placer les comportements, collisions et paramètres Unity dans cette couche. Définir le pivot avant de remplacer les blocs. Conserver les fichiers `.meta` et les identités existantes lors du déplacement ou du réimport. Tester une modification limitée de l’asset source, puis vérifier toutes ses instances, leurs overrides et leurs composants.

**Contrôle.** Le réimport conserve les références de scène et les commandes ; les pivots ne déplacent pas l’objet et les changements restent limités aux instances prévues.

**Limites / fondements.** Acampora 1951885 illustre le décalage de pivot lors du remplacement des colonnes ; manuel Prefabs. Séparation du modèle et du gameplay et essai de réimport : protocole d’intégration ajouté. Le projet Signal est un exemple minimal, pas une validation de tous les cas de variantes et prefabs imbriqués.

## U05 — Relier interaction, état et animation

**Quand / entrées.** Animation ou action commandée ; états attendus et périphériques cibles.

**Procédure.** Nommer les actions et leurs effets indépendamment du périphérique : pause, reprise, déplacement, sélection. Dans un projet Input System, relier les actions aux appareils prévus ; ne pas mélanger arbitrairement anciens et nouveaux systèmes. Choisir Generic, Humanoid ou autre mode selon le rig réel. Contrôler clips, durée, boucle et root motion sur le personnage importé. Comparer pause/reprise, limites et réinitialisation ; observer plusieurs frames évaluées, pas seulement la présence d’un asset AnimationClip.

**Contrôle.** L’utilisateur obtient un effet visible, la pause fige le bon état, la reprise évite un saut inattendu, la réinitialisation retrouve l’état prévu. Confirmer dans le player, pas seulement Scene View.

**Limites / fondements.** Acampora 1951979–1951983 pour actions et liaisons ; manuel d’import Rig. Signal emploie une animation d’objet importée et un échantillonnage explicite commandé par une interface IMGUI, adaptés à ce petit banc d’essai. Cela ne démontre pas un contrôleur Humanoid, du retargeting, du root motion ou la prise en charge manette/accessibilité complète.

## U06 — Optimiser le coût observé

**Quand / entrées.** Cible et scénario mesurables, budget de frame et mémoire, build de développement.

**Procédure.** Capturer une séquence stable après chargement. Identifier CPU, GPU, allocations, textures, géométrie ou appels de dessin comme coût dominant. Changer un facteur et comparer les distributions de temps de frame sur le même trajet/caméra. Considérer LOD, instances, textures et chargement selon ce diagnostic ; contrôler la qualité visuelle après réduction. Distinguer l’instrumentation du Profiler, le coût de l’éditeur et celui du player.

**Contrôle.** Mesure avant/après sur la cible et absence de régression visible. Rapporter résolution, matériel, version et scénario.

**Limites / fondements.** Manuel Unity Profiler 6.6 et notre protocole de comparaison. Pas de preuve locale d’optimisation de performance tirée du livre ; pas de gain FPS revendiqué pour Signal. Atteindre une limite VSync sur une scène vide ne prouve pas un budget suffisant pour un jeu.

## U07 — Livrer un build rejouable

**Quand / entrées.** Prototype à essayer ; scène d’entrée, cible et modules installés.

**Procédure.** Vérifier compilation et références de scène, puis construire avec un résultat de build explicite. Conserver les versions et les commandes. Lancer l’application produite, exercer ses commandes et examiner les erreurs runtime. Garder les fichiers de preuve et le projet éditable. Pour un script Editor en batch, utiliser un `executeMethod` ciblé, un log distinct et des assertions métier sur les assets importés. Éviter d’ouvrir le même projet simultanément dans deux éditeurs.

**Contrôle.** Build réussi, application ouverte, interaction visible et état relu. Une modification d’asset suivie d’un nouveau build doit être effectivement présente.

**Limites / fondements.** Manuel Unity CLI et expérimentation Signal : protocole de livraison ajouté, pas une promesse du corpus. Ne pas confondre build local, distribution signée/notarisée, version mobile et WebGL. Aucune publication ni installation MCP Unity implicite.
