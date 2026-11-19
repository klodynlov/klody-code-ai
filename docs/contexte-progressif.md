# Chargement progressif du contexte

Activé par défaut avec `CONTEXT_ROUTING_ENABLED=true`. L'orchestrateur prépare
les outils au début de chaque demande, avant le premier appel au modèle.

- Les noms d'applications, les groupes de capacités et les noms exacts d'outils
  sélectionnent les définitions utiles. Seuls les serveurs MCP sélectionnés
  sont contactés ; leur découverte est mise en cache et bornée par un délai.
- Une relance courte, par exemple « change la couleur », reprend le sujet
  depuis les messages utilisateur récents, y compris après rechargement d'une
  session. Un nouveau sujet explicite remplace le précédent.
- `find_tools(query)` ajoute des capacités à la prochaine inférence du même
  tour de travail. Une requête vide donne le catalogue sans contacter de serveur.
  Un serveur inaccessible est signalé et pourra être réessayé.
- Les préférences `utilisateur_*`, les règles `conventions_*`, la mémoire à
  long terme et le profil gardent leur traitement habituel. Les autres skills
  sont présentés par notices ; `read_skill(slug)` retourne leur texte intégral,
  références comprises. Les questionnaires interactifs actifs restent complets.
- Les tâches portant sur une application externe ne lancent plus l'indexation
  sémantique du code source de Klody. Les tâches de code la conservent.

Le filtrage repose sur des alias explicites : il peut manquer une formulation
inhabituelle. Le catalogue, `find_tools`, `list_skills` et `read_skill` permettent
de retrouver les capacités. Il ne change ni les permissions d'exécution ni les
fichiers des compétences. Il ne raccourcit pas l'historique des conversations.

Placement et cache de préfixe : le catalogue des capacités ne dépend que de la
configuration, il reste dans le prompt système (stable sur la session). Les
notices de skills, les pistes de fichiers et le guide propre à une application
suivent le tour utilisateur (`_contexte_tour`). Changer de sujet change la liste
d'outils, donc le préfixe ; une liste de 8 à 27 k tokens se recalcule vite.

`/api/siri` coupe ce routage : sa liste blanche (`_outils_siri`) doit rester la
seule source d'outils.

Mesure du 19/11/2026 (Orchestrator réel, gateway réel, `max_tokens=1`) :

| Requête | Outils avant → après | Tokens d'entrée après |
|---|---:|---:|
| Inspecter la scène Blender | 405 → 28 | 12 699 |
| Lire mes emails | 405 → 17 | 9 690 |
| Quelle heure est-il ? | 405 → 9 | 8 154 |
| Composer une chanson dans REAPER | 405 → 97 | 26 992 |
| Corriger un bug Python | 405 → 30 | 11 678 |

Avant : 89 481 tokens d'entrée pour chaque requête, 165 s de prefill à froid.
Après : 6 à 10 s à froid pour 8 à 13 k tokens, moins d'une seconde quand le
préfixe est en cache.

Pour revenir au comportement historique, définir `CONTEXT_ROUTING_ENABLED=false`
dans l'environnement puis redémarrer l'API. Les compteurs `[context]` des logs
indiquent la sélection initiale et sa taille, sans recopier les préférences.

Validation : `tests/test_context_selection.py`, `tests/test_mcp_bridge.py`, plus
les tests de routage modèle, de skills, de mémoire, de retrieval et de rejeu
orchestrateur. Les mesures locales avant/après sont conservées hors dépôt dans
`~/.klody/reports/context-2026-09-10/` ; elles incluent un prompt contenant des
préférences privées et ne doivent pas être publiées.
