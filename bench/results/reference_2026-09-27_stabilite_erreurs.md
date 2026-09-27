# ❌ Résultat négatif — le compteur `(N×)` des erreurs ne casse pas le cache de préfixe en pratique

Relevé du 2026-09-27. Script : `scripts/mesure_stabilite_erreurs.py`. Comptes :
`reference_2026-09-27_stabilite_erreurs.json`.

## Question

`ErrorMemory.format_for_prompt` rend `- **{sig}** ({n}× vus récemment)`, injecté
dans le prompt système à chaque message (`Orchestrator._inject_system_prompt`,
`err_section`). Le cache de préfixe de mlx_lm ne réutilise qu'un préfixe exact
(#270 : 11,2 s contre 0,55 s à 69 outils). Chaque récidive d'une erreur déjà
récurrente change `n`, donc le système : c'est le motif de compteur que #270 a
retiré du profil. Combien de paires de vrais messages consécutifs cela touche-t-il ?

## Protocole

- Sessions : celles que `scripts/etat_pollue.py` **garde** dans `~/.klody/data`
  (257), dont 116 ont au moins deux VRAIS messages utilisateur (`timestamp` non
  nul — les relances de l'orchestrateur ont `timestamp: None`). **305 paires**,
  du 2026-06-09 au 2026-09-22.
- Erreurs : les deux seuls `.klody/errors.json` de la machine (`find ~ -maxdepth 6`) —
  `~/Projets/` (PROJECT_ROOT en dur jusqu'en juillet) et `~/Projets/klody-code-ai/`
  (cwd du service API depuis). Aucun n'a atteint la rotation à 100 entrées :
  l'historique est complet.
- Pour chaque message, la section est recalculée **par le code du dépôt** à
  l'instant du message (entrées antérieures, fenêtre de 24 h comptée depuis lui).
  Chaque source est rejouée séparément sur toutes les paires.
- Horloges vérifiées sur l'incident `bpy` du 2026-08-05 : message `tool` de la
  session à `05:29:11.142801`, entrée `errors.json` à `05:29:11.142265`.

## Résultat

| source | entrées | section non vide | section **changée** | dont compteur | dont apparition/sortie |
|---|---|---|---|---|---|
| `~/Projets/.klody/errors.json` | 20 | 18/305 | 1/305 | **0** | 1 |
| `~/Projets/klody-code-ai/.klody/errors.json` | 3 | 2/305 | 0/305 | **0** | 0 |

À chaque échec enregistré (indépendamment des sessions), la section juste après
diffère de celle juste avant : 23 échecs ⇒ **2 changements de compteur**, 3
apparitions, 18 sans effet (signature sous le seuil de 3).

La session de l'incident `bpy` le montre : 2 échecs pendant le message de
05:26 (sous le seuil), le 3ᵉ pendant « execute le », dernier message de la
session. Aucun message suivant n'a jamais vu la section changer.

## Témoin — l'instrument sait rougir

`errors.json` synthétique posé sur une vraie session : 3 échecs avant un message,
1 récidive entre lui et le suivant ⇒ `compteur: 1`. 2 échecs avant, le 3ᵉ
entre les deux ⇒ `ensemble: 1`. Le zéro ci-dessus est une mesure, pas un
instrument aveugle.

## Décision

**Le compteur reste.** Principe directeur n°2 : aucune modification sans gain
chiffré, et le gain mesurable ici est de 0 paire sur 305. Le seul changement
réel (1/305) est une apparition de signature ; figer la section pour la session
la corrigerait, mais masquerait l'erreur devenue récurrente PENDANT la session —
c'est-à-dire exactement quand l'avertissement sert.

Borne haute du coût tant que ce régime tient : 2 récidives en quatre mois ⇒ au
plus 2 préfixes ratés, ~120 s chacun avec les ~297 outils MCP de l'API de prod.
À comparer aux skills et au retrieval, qui changent le système sur ~91 % des
paires (#270).

## Ce que ça n'établit PAS

- La rareté vient du **débit d'échecs sandbox** (23 en quatre mois), pas du
  format. Si l'auto-check se mettait à enregistrer beaucoup plus (plus de tâches
  de code via l'API, autre racine), la conclusion serait à rejouer :
  `python scripts/mesure_stabilite_erreurs.py`.
- Les sessions CLI lancées depuis un autre dépôt lisent `<ce dépôt>/.klody/errors.json` ;
  aucun autre n'existe sur la machine au 2026-09-27, mais un fichier supprimé ne
  se voit pas.
