# LaunchAgents

Définitions launchd des services dont le programme vit dans ce dépôt.

Avant, ces `.plist` n'existaient que dans `~/Library/LaunchAgents/`, hors
versionnement. Sur une machine neuve le script de service était donc présent et
l'agent qui le déclenche absent : **le service ne démarrait jamais, sans la
moindre erreur**. C'est le mode de panne qu'on ferme ici.

## Utilisation

```sh
scripts/install-launchagents.sh          # installe / met à jour, puis recharge
scripts/install-launchagents.sh --check  # signale les écarts, n'écrit rien
```

L'installation est **idempotente** : un agent dont le contenu rendu est déjà
identique à l'installé n'est ni réécrit ni rechargé. Sans cette règle, lancer le
script redémarrerait l'API en pleine session de travail.

`--check` sort en code 1 s'il trouve un écart — utilisable en CI ou en contrôle
manuel pour détecter la dérive entre le plist vivant et sa version au dépôt.

## Chemins

Les plists sont versionnés tels qu'installés, avec des chemins absolus. Le script
les réécrit pour la machine courante (racine du dépôt et `$HOME`), ce qui garde
les fichiers directement lisibles et diffables face à l'installé.

## Périmètre

Uniquement les agents dont le programme vit dans ce dépôt. Les autres agents
`com.klody.*` de la machine appartiennent à d'autres projets (`klody-core`,
`local-suno`, `vocalbrain`, `.claude/tools`) et **ne sont pas gérés ici** — les
verser ici serait une erreur d'attribution. Ils gardent le même angle mort ; à
traiter dans leurs dépôts respectifs.

## Un seul owner par service

Ces agents sont les **seuls** démarreurs de leurs services. L'app ne doit spawner
ni l'API `:8000`, ni MLX `:8080`, ni LibraryBrain `:8765` : un second démarreur
provoque des ports occupés, des « Backend indisponible » au redémarrage et des
états incohérents. `api-watchdog` respecte la règle — il relance via
`launchctl kickstart`, jamais en lançant l'API lui-même.
