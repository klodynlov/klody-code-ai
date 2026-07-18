# Connecteur MCP VLC

Klody pilote le lecteur **VLC** (lecture, playlist, volume, seek, plein écran)
via un serveur MCP maison : `klody_mcp/vlc_server.py`.

## Architecture

```
Klody (client MCP)  ──►  serveur MCP VLC :8091  ──►  interface HTTP native de VLC :8092  ──►  VLC.app
   MCPManager                 FastMCP                    module `http` de VLC
```

Le serveur MCP n'embarque **aucune** dépendance VLC. Surtout pas `python-vlc` /
`libvlc` : celles-ci piloteraient une instance *à elles*, pas le VLC ouvert par
l'utilisateur. On parle à l'interface HTTP de VLC, en local, en Basic auth
(utilisateur vide, mot de passe = `http-password`).

| Port | Quoi |
|------|------|
| 8091 | serveur MCP VLC (`VLC_MCP_PORT`) — consommé par Klody |
| 8092 | interface HTTP de VLC (`VLC_HTTP_PORT`) — **pas** 8080, réservé au MLX brain |

## Installation (une fois)

```bash
# VLC doit être FERMÉ (il réécrit vlcrc en quittant et écraserait la config)
./scripts/setup-vlc-http.sh
```

Le script sauvegarde `~/Library/Preferences/org.videolan.vlc/vlcrc`, y écrit
`extraintf=http`, `http-host`, `http-port`, `http-password` (mot de passe généré,
fichier en 600), et reporte `VLC_HTTP_*` dans le `.env` du projet. Le mot de
passe ne transite par **aucune ligne de commande** — il n'apparaît pas dans `ps`.

Puis brancher le serveur en permanence :

```bash
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.klody.vlc-mcp.plist
```

et déclarer le connecteur côté client (`.env`), puis **redémarrer l'API**
(`config.MCP_SERVERS` est lu à l'import + cache de découverte process-level) :

```bash
KLODY_MCP_SERVERS={...,"vlc":"http://127.0.0.1:8091/mcp"}
launchctl kickstart -k gui/$UID/com.klody.api
```

Vérification : `curl -s localhost:8000/health | jq .checks.mcp_detail.vlc` → `"ok"`.

## Outils exposés (13)

`etat_lecture`, `demarrer_vlc`, `lire`, `ajouter_a_la_playlist`, `lister_playlist`,
`pause`, `stop`, `suivant`, `precedent`, `chercher`, `regler_volume`,
`vider_playlist`, `plein_ecran` — sous `mcp__vlc__*`.

## Garde-fous

- **Chemins** (ASI02) : `lire`/`ajouter_a_la_playlist` passent par
  `klody_mcp/_pathguard.safe_path` — confinés à Music/Movies/Documents/Projets/tmp.
  `~/.ssh/id_ed25519` et `../../../etc/passwd` sont refusés.
- **Anti-SSRF** : les URL `http(s)` vers loopback / réseau privé / link-local sont
  refusées (sinon le LLM transforme VLC en client HTTP vers l'API Klody).
  `VLC_ALLOW_PRIVATE_URLS=1` pour un serveur média LAN légitime.
- **Schémas** : seuls chemins locaux et `http(s)` acceptés (`smb://`, `ftp://`,
  `file://` direct refusés — `file://` doit passer par le garde-fou chemins).
- **Volume** borné à 0-200 %, **seek** en allowlist stricte (`alnum` + `+-%`)
  pour fermer l'injection de paramètre dans l'URL de commande.

## Deux pièges vérifiés en vrai

**1. VLC applique ses commandes en ASYNCHRONE.** La réponse à
`?command=pl_stop` contient le status d'**avant** la commande. Rendre ce status
tel quel faisait mentir les outils : `stop` répondait `playing`, `lire` répondait
`stopped` alors que la piste démarrait. `_command()` **relit** donc l'état après
coup, en bouclant sur un prédicat (`attendre=`). Si le prédicat n'est jamais
satisfait, `lire` renvoie une **erreur nommée** (« VLC a accepté la commande mais
ne joue rien — média illisible, codec manquant ? ») plutôt qu'un faux succès.

**2. À froid, l'interface HTTP met ~15 s à répondre** après `open -a VLC`
(lancement de l'app + init du module http). `demarrer_vlc` attend jusqu'à 45 s ;
une attente de 10 s rendait un faux « VLC ne répond pas » alors qu'il arrivait.

## Diagnostic tri-état

Ne jamais confondre ces trois cas — c'est la famille de bugs « sonde qui ment » :

| Symptôme réseau | Sens réel | Message |
|---|---|---|
| `ConnectError` | rien n'écoute | VLC pas lancé **ou** `extraintf=http` absent de vlcrc |
| **401** | **VLC est VIVANT** | `VLC_HTTP_PASSWORD` (.env) ≠ `http-password` (vlcrc). Relancer VLC ne répare rien |
| 404 | ça répond mais ce n'est pas VLC | port 8092 occupé par un autre service |
| timeout | connecté mais muet | VLC figé — distinct d'un « down » |
