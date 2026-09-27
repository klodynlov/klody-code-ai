# 2026-09-27 — l'extraction de faits casse le prompt système, et le figer la rend neutre

Instrument : `scripts/mesure_stabilite_memoire.py --sessions 45`
(JSON : `reference_2026-09-27_stabilite_memoire.json`, comptes seuls — aucun
contenu de fait ni de message n'en sort).

## La question

La PR qui répare `agent/memory_extractor.py` fait de nouveau écrire des faits
dans `LongTermMemory` après CHAQUE message WebSocket (`api/server.py::
_extract_memory_bg`). `Orchestrator._inject_system_prompt` injecte
`format_for_prompt()` dans le prompt SYSTÈME, et le cache de préfixe de mlx_lm ne
réutilise qu'un préfixe EXACT (#270 : un token changé ⇒ `cached=0`). La
docstring de `_cible()` le notait « coût INDIRECT, non mesuré ».

## Protocole

- Les 45 sessions réelles les plus récentes à ≥ 2 tours (depuis le 2026-07-17),
  parmi celles que `scripts/etat_pollue.py` garde ; ordre chronologique.
- Tours utilisateur = rôle `user` à `timestamp` non nul : les relances de
  l'orchestrateur (« Ton budget d'itérations… ») sont posées à `timestamp: None`.
- Après chaque tour sauf le dernier : la VRAIE `extract_and_save` (même prompt,
  même troncature) contre `brain` via le gateway — la cible de la PR —, en-têtes
  `X-Klody-App: klody-ai` et `X-Klody-Source: system`.
- État de départ : COPIE de `long_term.json` (68 faits, dernier écrit le
  2026-07-18 — date de la mort de l'extraction, donc chronologiquement cohérent
  avec l'échantillon) et de `user_profile.json`, en dossier temporaire supprimé
  même sur SIGTERM ; miroir sémantique coupé.
- Juge : égalité octet pour octet de la section entre deux messages consécutifs
  d'une même session.

## Résultat

| | paires |
|---|---|
| paires de messages consécutifs | 93 |
| dont précédées d'une extraction | 53 |
| appels d'extraction aboutis / échecs | 53 / 0 |
| extractions écrivant ≥ 1 fait | **11/53 (21 %)**, IC 95 % [12 ; 34] |
| section mémoire changée | **11/93 (12 %)**, IC 95 % [6,7 ; 20] — figée : **0/93** |
| section profil changée | 5/93 (5 %) |

| système (profil + mémoire) identique | |
|---|---|
| sans extraction — la production depuis le 2026-07-18 | 88/93 (95 %) |
| **extraction à chaque message** — la PR seule | **77/93 (83 %)** |
| **extraction + section figée** — ce correctif | **88/93 (95 %)** |

Chaque extraction qui écrit change la section : aucune « retouche identique »
(0), 22 clés neuves, 5 contenus modifiés. Blocs en cause : `Projets en cours` 9,
`Utilisateur` 6, `Contexte général` 6, `Préférences` 4 — les petites catégories
(6, 1 et 2 entrées, sous le plafond de 15) prennent une ligne par fait neuf, et
`context` (59 entrées) décale sa fenêtre des 15 plus récents.

Le figeage atteint le plafond exact du bras « sans extraction » : aucun autre
remède (extraire moins souvent, en fin de session) ne peut faire mieux sur cet
axe, et tous perdraient de la couverture d'extraction.

## Ce que coûte un raté

Pas re-mesuré ici — deux mesures intra-run existantes :

- 69 outils (~11,5 k tokens) : 11,2 s contre 0,55 s en cache (#270) ;
- prompt de ~92 k tokens avec les schémas MCP : 154,9 s contre 32,7 s, même
  run, même session (branche `claude/contexte-tour-utilisateur`, WIP).
  L'API de prod découvrait **297 outils MCP** à son dernier démarrage
  (2026-09-21, `logs/agent.log`) : c'est ce second régime.

## Ce que ça n'établit PAS

- **Aujourd'hui l'effet est masqué** : skills et retrieval, placés AVANT
  `lt_section`, changent déjà le système sur ~91 % des paires (#270). Le gain
  n'apparaît que lorsqu'ils sortiront du prompt système.
- Le taux dépend de ce que les conversations contiennent : 21 % des extractions
  écrivent sur cet échantillon ; des sessions plus longues ou plus
  personnelles en écriraient davantage. Le chiffre est un ordre de grandeur,
  n=53.
- La durée médiane d'une extraction (0,45 s, prompt médian 557 tokens) a été
  lue sur un gateway partagé avec un banc concurrent : non comparable (CLAUDE.md).
- Le banc ne peut PAS voir ce changement : chaque tâche tourne dans un état neuf
  (`--child-data-dir`), donc `long_term.json` vide, et un seul message.

## Banc de non-régression

`python -m bench.run --repeat 1 --label memoire_figee` : **34/35**.
`python -m bench.gate` : `Δ −3,3 %`, pas de régression significative, notice
`discovery/config_precedence (1/1 → 0/1)`.

| `config_precedence` | |
|---|---|
| cette branche, run complet + rejeu `--repeat 3` | 0/4 |
| `origin/main` (`a87f5d2`), même `.env`, même jour | 2/3 |
| branche de #288 (sans ce changement), même jour | 1/3 |

Même signature partout : `argparse type=int` ⇒ `SystemExit` là où le README
exige `ValueError`. Le taux seul ne tranche pas (Fisher unilatéral p ≈ 0,17) ;
le mécanisme, si : rejoué dans les conditions du fils du banc (état jetable,
`PROJECT_ROOT` = fixture, retrieval neutralisé), le prompt système est
**identique octet pour octet** entre `main` et cette branche — même sha
`69a2529b72e5`, 65 395 caractères, 0 fait chargé, mêmes 12 skills. Même entrée,
verdicts différents : variance d'échantillonnage sur une tâche instable ce jour.

⚠️ Premier témoin raté, à ne pas refaire : un worktree créé HORS de
`~/Projets/klody-code-ai` ne trouve aucun `.env` (`load_dotenv()` remonte
l'arborescence) ⇒ `BACKEND=ollama` ⇒ ❌ en 3,5 s par tâche, agent jamais lancé.
