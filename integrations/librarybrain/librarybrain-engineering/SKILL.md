---
name: librarybrain-engineering
description: "Appliquer les méthodes distillées de LibraryBrain pour choisir entre instructions, RAG et fine-tuning, diagnostiquer une chaîne LLM ou concevoir son évaluation. Utiliser pour une décision ou une expérimentation IA, sans déclencher automatiquement un entraînement."
---

# Méthodes d’ingénierie et d’évaluation IA

Ces méthodes viennent de deux ouvrages de Chip Huyen, dont les passages et limites sont enregistrés dans la [provenance](references/provenance.json). Ils constituent une même filiation d’auteur, pas deux validations indépendantes. Appliquer la méthode au système et aux données réels de l’utilisateur.

## E1 — Choisir l’intervention à partir des échecs

1. Définir la tâche utile et examiner des réponses erronées ou insuffisantes.
2. Distinguer un manque d’information d’un problème de comportement : donnée absente/périmée, ou information présente mais format, consigne ou procédure mal suivis. Vérifier aussi la possibilité d’une erreur de recherche ou d’un contexte mal assemblé.
3. Établir une référence simple : modèle actuel, instructions et quelques exemples si appropriés. Pour les faits externes, tester une récupération documentaire simple. Pour un comportement persistant malgré les instructions, envisager des exemples supervisés puis une adaptation si elle est justifiée.
4. Mesurer le gain sur les mêmes cas et contextes avant de cumuler des techniques.

**Livrable :** catégories d’erreurs, intervention ciblée et comparaison. La distinction de Huyen oriente le diagnostic ; elle n’interdit pas qu’un entraînement apprenne des connaissances. RAG et fine-tuning peuvent se compléter sans garantir un gain. L’existence d’un gros corpus ne suffit pas à justifier un entraînement.

## E2 — Séparer la recherche de la réponse

1. Préparer des questions avec passages pertinents identifiés et critères de réponse.
2. Évaluer la récupération : part des passages retrouvés qui sont pertinents et part des passages pertinents de référence qui sont retrouvés. Nommer le périmètre de référence ; un rappel sur un petit jeu ne mesure pas le rappel de toute la bibliothèque.
3. Évaluer le générateur avec un contexte pertinent fixé : répond-il à la question, préserve-t-il les faits et cite-t-il un passage qui soutient réellement sa conclusion ? La fidélité au contexte et la vérité externe sont deux mesures distinctes.
4. Tester ensuite la chaîne réelle. Si le générateur réussit avec les bons passages mais échoue avec ceux de la recherche, examiner le chemin de récupération. S’il échoue dans les deux cas, examiner aussi instructions, contexte, modèle et critères.

**Livrable :** tableau recherche / génération à contexte fixé / chaîne complète, avec exemples d’échec. Les mesures viennent de Huyen ; l’expérience diagnostique en deux contextes est notre adaptation opérationnelle. Aucun seuil de réussite universel n’est repris d’un exemple de livre.

## E3 — Construire une comparaison qui résiste aux fuites

1. Définir une référence simple et une grille illustrée de réussite/échec adaptée au besoin. Faire clarifier les cas ambigus avant de compter un score.
2. Identifier les doublons, éditions proches et groupes corrélés avant de séparer les données. Conserver chaque groupe dans une seule division ; respecter la chronologie si l’usage consiste à prévoir le futur.
3. Utiliser la validation pour ajuster ; préserver un ensemble final de nouveaux cas pour la mesure finale. Recontrôler les recouvrements après les transformations et la génération de variantes.
4. Rapporter les effectifs, la nature des annotations et les régressions, avec la latence ou le coût si ces contraintes importent. Comparer au système simple ; une baisse de perte ou un meilleur format ne démontre pas une meilleure exactitude.

**Livrable :** protocole, groupes de séparation, grille et résultat reproductible. La déduplication, les groupes, le temps et les références simples viennent de Designing Machine Learning Systems ; la grille illustrée et sa validation humaine viennent d’AI Engineering. Leur application aux livres et aux exemples synthétiques est notre adaptation.

Pour une implémentation, vérifier le dépôt et ses versions, puis tester le changement demandé. Les principes distillés ne remplacent pas la documentation actuelle d’une API. Les cas de développement du présent ensemble ne sont pas une certification indépendante de ces skills.
