---
name: librarybrain-unity
description: "Créer, diagnostiquer et livrer un prototype interactif Unity 6 : projet URP, import Blender/FBX, échelle et axes, matériaux, prefabs, animation, interactions C#, performances et build. Utiliser pour faire fonctionner un asset dans le moteur et vérifier le résultat sur la cible."
---

# Unity — transformer un asset en expérience jouable

Clarifier l’interaction à éprouver, la plateforme, la version exacte de Unity et le pipeline de rendu. Inspecter `ProjectSettings/ProjectVersion.txt`, `Packages/manifest.json`, `Packages/packages-lock.json`, les réglages Graphics/Quality et les scènes existantes. Conserver leurs conventions ; ne pas migrer un projet simplement parce qu’un autre éditeur est installé.

Sur ce poste, Unity **6000.6.0f1** et Blender **5.1.2** sont installés. Ce constat date du 10 septembre 2026 ; le revérifier si le contexte change. Un éditeur installé n’implique ni projet ouvert, ni module de build, ni serveur MCP disponible.

## Appliquer la bonne méthode

| Situation | Fiche |
|---|---|
| Définir le premier résultat et un prototype minimal | [U01 — périmètre testable](references/methodes.md#u01--construire-une-tranche-complète) |
| Asset Blender mal orienté, trop grand, animation perdue | [U02 — contrat d’import](references/methodes.md#u02--établir-le-contrat-dimport) |
| Matériaux roses, mauvais relief, transparence | [U03 — rendu et matières](references/methodes.md#u03--reconstruire-le-matériau-dans-le-pipeline-cible) |
| Remplacer un bloc par un asset sans casser la scène | [U04 — prefabs et réimport](references/methodes.md#u04--assembler-sans-perdre-les-références) |
| Mouvement, commandes, pause et reprise | [U05 — interaction et animation](references/methodes.md#u05--relier-interaction-état-et-animation) |
| Chutes de fluidité ou mémoire élevée | [U06 — mesure](references/methodes.md#u06--optimiser-le-coût-observé) |
| Livrer et vérifier une application | [U07 — build et preuve](references/methodes.md#u07--livrer-un-build-rejouable) |

Blender porte la géométrie, les UV, les textures sources et les clips ; Unity porte l’assemblage interactif, le shader cible, les commandes et le build. Utiliser `librarybrain_blender` pour la création d’assets. Conserver un contrat commun : mètres, orientation, pivots, noms stables, canaux de textures, clips et budget de la cible.

Klody dispose des MCP Blender et Unity. Unity utilise **MCP for Unity 10.2.0**, sur `http://127.0.0.1:8097/mcp`, installé dans le prototype `BlenderUnityPrototype` ; ce constat a été vérifié le 10 septembre 2026. Le serveur local démarre avec la session macOS et le package reconnecte l’éditeur à son ouverture. Pour un autre projet, installer le package dans ce projet puis vérifier sa connexion. Lire le [protocole MCP](references/mcp.md) avant de piloter l’éditeur.

Commencer par `find_tools("unity")`, puis `mcp__unity__manage_scene` avec `action="get_active"` et `get_hierarchy`. Les outils spécialisés se chargent avec `find_tools("Unity animation")`, `find_tools("Unity script")` ou leur nom exact. Préserver la scène ouverte et vérifier `isDirty` avant tout remplacement. Les opérations Editor ne doivent pas écraser une scène utilisateur ouverte ; préférer un projet de prototype isolé ou une scène explicitement ciblée.

Le fondement local principal est l’ouvrage d’Acampora (exemples Unity 6.3), complété par le manuel officiel 6.6 et par le prototype original Signal. Voir [sources, adaptations et limites](references/sources.md). Il s’agit d’un complément ciblé à Blender, pas d’une distillation exhaustive de tous les sujets Unity.
