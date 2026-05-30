# Étude de cas — construire un agent de code IA *100 % local* fiable

> Comment transformer un modèle local (sous la frontière) en agent de code fiable —
> par l'**orchestration, la sécurité et la discipline de tests**, pas par la force brute.
> Projet solo, ~25k LOC, 699 tests, en quelques semaines.

Ce document détaille les **décisions d'ingénierie** derrière Klody Code AI. Il s'adresse
aux personnes qui veulent juger la qualité du raisonnement, pas seulement le résultat.

---

## 1. Le problème et les contraintes

**Objectif** : un agent de code autonome qui rivalise avec un agent cloud — mais qui tourne
**entièrement en local** (Apple Silicon / MLX), sans envoyer code ni données vers le cloud.

**La contrainte qui définit tout** : le cerveau est un modèle local (`Qwen3.6-35B-A3B`, MoE
~3B actifs) — rapide et privé, mais **matériellement moins capable** qu'un modèle frontière.
On ne peut pas le ré-entraîner. Donc **tout le travail d'ingénierie consiste à compenser ce
plafond par le harnais autour du modèle** : routage, boucle, retrieval, garde-fous, tests.

C'est le cœur de la démonstration : *un bon système peut faire d'un modèle moyen un agent
fiable*. Voici comment.

---

## 2. War story — la régression « le code s'arrête au milieu »

**Symptôme** : sur des tâches lourdes (« crée une voiture en 3D »), Klody générait du HTML/JS
**tronqué au milieu d'une fonction** (`createCar()` coupée net). Intermittent, donc piégeux.

**Fausses pistes écartées** : parsing des tool calls, bug du sandbox, timeout réseau.

**Cause racine** : `max_tokens` n'était **pas défini** côté serveur MLX → coupure par défaut
à ~500 tokens. Le modèle produisait un long fichier, MLX le tronquait, et `write_file`
recevait du code à moitié écrit. Le modèle, lui, « croyait » avoir fini.

**Le correctif** n'a pas été qu'une ligne (`max_tokens=8192`). La leçon a façonné l'architecture :
- **Parsers de tool calls tolérants** : XML lenient pour les blocs tronqués, multi-call, réparation de JSON dans les docstrings — pour ne plus *jamais* qu'une sortie imparfaite casse la boucle.
- **Fallback text-to-action** : si le modèle répond en texte au lieu d'appeler un outil (fréquent sur un modèle local), on extrait les blocs ` ```html `/` ```js ` et on invoque `preview_code` nous-mêmes.
- **Golden test de non-régression** : un scénario `05_max_tokens_truncated_regression` **fige** ce bug pour toujours.

**Leçon transférable** : sur un agent, les bugs les plus coûteux ne sont pas dans le modèle,
mais dans la **plomberie** (limites, troncatures, parsing). Le harnais doit être *défensif*.

---

## 3. Décision — un routeur adaptatif plutôt qu'un prompt géant

**Problème** : un modèle local sur-raisonne ou cale si on lui donne un méga-prompt « fais
tout bien ». Et toutes les tâches ne méritent pas le même budget.

**Décision** : un **routeur** classe chaque requête en **3 difficultés × 6 types de tâche**
(`edit / refactor / bug_fix / feature / explain / self_dev`) et en déduit le budget
d'itérations, le system prompt et l'activation du Best-of-N.

- Mesuré : **F1 macro ≈ 0,85** sur un jeu étiqueté (`bench.router_eval`).
- **Hot-swap de prompts** : 6 prompts focalisés (~300-600 tokens) au lieu d'un seul de 1600+.
- **Contre-intuitif et clé** : simplifier `feature.md` de **1637 → 289 tokens** a *amélioré*
  les résultats. Les « RÈGLE ABSOLUE : … » paralysaient Qwen3 (il sur-raisonnait sur les
  règles au lieu d'agir). *Moins de prompt = plus d'action* sur un modèle local.

---

## 4. Décision — faire en sorte que la boucle aille au bout

**Problème rapporté** : « il faut constamment le relancer ». L'agent s'arrêtait avant la fin.

**Diagnostic** : routeur EASY → budget 3 itérations + arrêt dur `for…else` + aucun report
quand une continuation arrivait (« ok, vas-y » re-routait en EASY depuis zéro).

**Correctif en trois temps (A+B+C)** :
- **A — auto-continue** : si la tâche est actionnable et que des outils ont été appelés, la
  boucle se **prolonge** (jusqu'à 3×) au lieu de s'arrêter sèchement.
- **B — plafonds relevés** : easy 6 / medium 14 / hard 25 (au lieu de 3/8/10).
- **C — cliquet de routeur** : une continuation courte (« ok / vas-y / parfait ») **réutilise
  le routage précédent** au lieu de re-classer en EASY.

**Leçon** : l'UX d'un agent local se gagne autant sur le **contrôle de boucle** que sur le modèle.

---

## 5. Décision — devenir client MCP (extensibilité sans toucher au cœur)

**Problème** : ajouter des capacités (Gmail, web…) sans gonfler le cœur ni le coupler.

**Décision** : Klody devient **client MCP**. Il découvre et consomme n'importe quel serveur
MCP ; les outils sont exposés au LLM sous `mcp__<serveur>__<outil>`. Conséquence : ajouter
Gmail ou le web = **une ligne de `.env`**, zéro modification du cœur.

Détails d'ingénierie qui comptent :
- **Pont sync↔async** : la boucle ReAct est synchrone, le client FastMCP asynchrone, et
  l'API tourne déjà sous une boucle asyncio. Solution robuste : un **thread dédié + `asyncio.run`**
  (évite le `RuntimeError: asyncio.run() cannot be called from a running event loop`).
- **Cache de découverte au niveau processus** : l'orchestrateur est recréé à chaque message ;
  sans cache, on re-scannerait les serveurs (round-trip réseau) à chaque requête.
- **Résilience** : un serveur injoignable au boot est ignoré (log), pas un crash ; une erreur
  d'appel revient en **texte** lisible au lieu de remonter en exception.

Klody **s'expose aussi** comme serveur MCP — d'autres agents (Cline, Zed, Continue.dev) le consomment.

---

## 6. Sécurité — pensée pour un agent qui *agit*

Klody écrit des fichiers, lance des commandes, envoie des mails et lit le web. Le périmètre
d'attaque est réel et traité comme tel :

- **Sandbox fichiers multi-racines** : écriture autorisée sur `PROJECT_ROOT` + `ALLOWED_ROOTS`,
  `../`/symlinks bloqués, et **fichiers sensibles refusés *partout*** (`.env .key .pem`…) — même
  dans une racine autorisée.
- **Anti-SSRF sur le web** : GET seulement, http/https only, **toute IP privée/loopback/
  link-local refusée — y compris à chaque saut de redirection** (169.254.169.254 = cible
  métadonnées cloud, bloquée). Taille plafonnée, contenu binaire non extrait.
- **Le risque résiduel, nommé honnêtement** : web (lecture) + mail (envoi) + fichiers +
  terminal dans la même boucle ⇒ surface d'**injection de prompt**. Documenté, gardé en
  lecture seule côté web, et signalé comme la *vraie* limite (pas la connexion elle-même).
- **Chaîne logicielle** : `.env` gitignoré, secrets jamais loggés, **commits signés** (ED25519),
  branch protection sur `main`, CI avec bandit / gitleaks / pip-audit.

---

## 7. Discipline d'ingénierie

- **699 tests** (coverage 78 %, gate CI à 75 %) : unitaires, sécurité (path traversal,
  symlinks, null bytes, blocage `.env`), **non-régression comportementale** (replay LLM avec
  fakes + fixtures golden), **contrat** (snapshots MCP/OpenAPI), monitoring (`/health`, `/metrics`).
- **CI** : 5 jobs parallèles (cancel-in-progress), `requirements.lock` + drift check, CodeQL,
  Dependabot, bench nightly avec gate de régression.
- **Conventions** : Conventional Commits, commits **tous** signés (`G`), aucune dette de
  trailer, secrets hors du dépôt.

---

## 8. Limites assumées (parce qu'un bon ingénieur les connaît)

- **Le modèle est le plafond.** Un 35B local reste sous la frontière ; le harnais compense mais
  ne dépasse pas. Pour du raisonnement très dur, un modèle cloud gagnera.
- **Largeur vs profondeur.** La surface est large (code, audio, RAG, GitHub, MCP, mémoire) ;
  certains sous-systèmes sont plus aboutis que d'autres.
- **Injection de prompt.** Inhérente à un agent qui lit du contenu non fiable *et* agit ;
  mitigée (lecture seule, SSRF), pas éliminée.

---

## 9. Ce que ça démontre

Concevoir un **système** (orchestration + sécurité + tests + extensibilité) qui rend un modèle
local *fiable* — c'est exactement la compétence que demande tout déploiement d'IA **privé /
on-prem** en entreprise. Le code n'est pas une démo jouet : c'est une plateforme testée, sécurisée
et extensible, livrée seul et vite.

➡️ Déploiement en entreprise / accompagnement : voir [CONSULTING.md](CONSULTING.md).
