"""VLC MCP server — pilote le lecteur VLC en langage naturel.

Même patron que vocalbrain_server.py / reaper_server.py : pont MCP léger. Il
n'embarque AUCUNE dépendance VLC (pas de python-vlc / libvlc — celles-ci
piloteraient une instance *à elles*, pas le VLC de l'utilisateur). Il parle à
l'interface HTTP native de VLC (module `http` de VLC, auth Basic, utilisateur
vide) sur 127.0.0.1. Tout reste en local.

Prérequis côté VLC (une fois) : `extraintf=http`, `http-port`, `http-password`
dans ~/Library/Preferences/org.videolan.vlc/vlcrc — cf. scripts/setup-vlc-http.sh.
Sans ça, rien n'écoute et tous les outils renvoient le diagnostic « VLC down ».

Démarrage :
    python -m klody_mcp.vlc_server                        # stdio (défaut)
    VLC_MCP_TRANSPORT=http python -m klody_mcp.vlc_server # :8091

Outils exposés :
- etat_lecture()                  — ce qui joue, position, volume, plein écran
- demarrer_vlc()                  — lance VLC s'il ne tourne pas, attend l'iface
- lire(media)                     — joue un fichier/URL (remplace la lecture)
- ajouter_a_la_playlist(media)    — enfile sans interrompre
- pause() / stop()                — pause bascule, stop arrête
- suivant() / precedent()         — piste suivante / précédente
- chercher(position)              — seek ("90", "+30", "-10", "50%")
- regler_volume(pourcent)         — 0-200 % (256 = 100 % côté VLC)
- lister_playlist()               — éléments enfilés + celui en cours
- vider_playlist()                — vide la playlist
- plein_ecran()                   — bascule le plein écran
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP

from klody_mcp._pathguard import PathGuardViolation, safe_path  # ASI02

load_dotenv()

logger = logging.getLogger(__name__)

VLC_HTTP_HOST = os.getenv("VLC_HTTP_HOST", "127.0.0.1")
VLC_HTTP_PORT = int(os.getenv("VLC_HTTP_PORT", "8092"))
VLC_HTTP_PASSWORD = os.getenv("VLC_HTTP_PASSWORD", "")
HTTP_TIMEOUT = float(os.getenv("VLC_MCP_TIMEOUT", "5"))
# Par défaut on refuse de faire ouvrir à VLC une URL qui pointe vers le réseau
# privé / la loopback (anti-SSRF : le LLM ne doit pas transformer VLC en client
# HTTP vers l'API Klody ou le NAS). Mettre à 1 pour un serveur média LAN.
ALLOW_PRIVATE_URLS = os.getenv("VLC_ALLOW_PRIVATE_URLS", "0") == "1"

_BASE = f"http://{VLC_HTTP_HOST}:{VLC_HTTP_PORT}"
_AUTH = ("", VLC_HTTP_PASSWORD)  # VLC : utilisateur vide, mot de passe = http-password

mcp = FastMCP("VLC")

# ---------------------------------------------------------------------------- #
# Diagnostics DISTINCTS (ne jamais confondre « down » et « mal authentifié »)  #
# ---------------------------------------------------------------------------- #

_ERR_DOWN = (
    f"VLC injoignable ({_BASE}) — rien n'écoute. Soit VLC n'est pas lancé "
    "(essaie l'outil demarrer_vlc), soit son interface HTTP est désactivée "
    "(vlcrc : extraintf=http + http-password, cf. scripts/setup-vlc-http.sh)."
)
_ERR_AUTH = (
    f"VLC est VIVANT sur {_BASE} mais refuse l'authentification (401) — "
    "VLC_HTTP_PASSWORD (.env) ne correspond pas à http-password (vlcrc). "
    "Ce n'est PAS une panne de VLC."
)
_ERR_NOT_VLC = (
    f"Quelque chose écoute sur {_BASE} mais ce n'est pas l'interface HTTP de VLC "
    "(404 sur /requests/status.json) — port occupé par un autre service ?"
)
_ERR_NO_PASSWORD = (
    "VLC_HTTP_PASSWORD n'est pas défini dans .env — l'interface HTTP de VLC "
    "refuse toute connexion sans mot de passe. Lance scripts/setup-vlc-http.sh."
)


# ---------------------------------------------------------------------------- #
# Client HTTP de l'interface VLC                                               #
# ---------------------------------------------------------------------------- #


async def _vlc_get(path: str, params: dict | None = None) -> dict:
    """Appelle l'interface HTTP de VLC. Renvoie toujours un dict.

    En cas d'échec : {"error": "..."} — jamais d'exception qui remonte au LLM.
    Les trois modes d'échec sont distingués (down / auth / pas-VLC) : un 401
    veut dire VIVANT, le confondre avec « down » enverrait le diagnostic sur
    une fausse piste.
    """
    if not VLC_HTTP_PASSWORD:
        return {"error": _ERR_NO_PASSWORD}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, auth=_AUTH) as client:
            resp = await client.get(f"{_BASE}{path}", params=params)
    except httpx.ConnectError:
        return {"error": _ERR_DOWN}
    except httpx.TimeoutException:
        # Connecté mais muet : VLC figé / iface saturée. Distinct d'un « down ».
        return {"error": f"VLC joignable ({_BASE}) mais pas de réponse en {HTTP_TIMEOUT}s — VLC figé ?"}
    except httpx.HTTPError as exc:
        return {"error": f"erreur HTTP vers VLC ({_BASE}) : {exc}"}

    if resp.status_code == 401:
        return {"error": _ERR_AUTH}
    if resp.status_code == 404:
        return {"error": _ERR_NOT_VLC}
    if resp.status_code >= 400:
        return {"error": f"VLC a répondu {resp.status_code} sur {path}"}
    try:
        data = resp.json()
    except ValueError as exc:
        return {"error": f"réponse illisible de VLC ({path}) : {exc}"}
    return data if isinstance(data, dict) else {"result": data}


async def _command(
    command: str,
    *,
    attendre: Callable[[dict], bool] | None = None,
    essais: int = 8,
    delai: float = 0.3,
    **params: str,
) -> dict:
    """Envoie une commande de contrôle puis renvoie l'état RÉEL qui en résulte.

    Piège central de l'interface HTTP de VLC : elle applique la commande de
    façon ASYNCHRONE et renvoie dans la même réponse le status d'AVANT. Rendre
    ce status tel quel ferait mentir l'outil (`stop` répondait « playing »,
    `lire` répondait « stopped » alors que la piste démarrait). On relit donc
    l'état, en bouclant tant que `attendre` n'est pas satisfait.

    Le caller décide si un prédicat jamais satisfait est une erreur — ici on
    renvoie le dernier état observé, jamais une supposition.
    """
    raw = await _vlc_get("/requests/status.json", {"command": command, **params})
    if "error" in raw:
        return raw
    etat = _resume_etat(raw)
    for _ in range(essais):
        await asyncio.sleep(delai)
        suivant = await _vlc_get("/requests/status.json")
        if "error" in suivant:
            return suivant
        etat = _resume_etat(suivant)
        if attendre is None or attendre(etat):
            return etat
    return etat


# ---------------------------------------------------------------------------- #
# Normalisation des réponses VLC                                               #
# ---------------------------------------------------------------------------- #


def _meta(raw: dict) -> dict:
    """Extrait les métadonnées du média courant (structure VLC imbriquée)."""
    info = raw.get("information")
    if not isinstance(info, dict):
        return {}
    cat = info.get("category")
    if not isinstance(cat, dict):
        return {}
    meta = cat.get("meta")
    return meta if isinstance(meta, dict) else {}


def _pourcent_volume(raw_volume: Any) -> int | None:
    """VLC exprime le volume en 0-512 avec 256 = 100 %."""
    try:
        return round(float(raw_volume) / 256 * 100)
    except (TypeError, ValueError):
        return None


def _resume_etat(raw: dict) -> dict:
    """Réduit le status.json de VLC (très verbeux) aux champs utiles au LLM."""
    meta = _meta(raw)
    titre = meta.get("title") or meta.get("filename") or ""
    return {
        "etat": raw.get("state", "inconnu"),  # playing | paused | stopped
        "titre": titre,
        "artiste": meta.get("artist", ""),
        "album": meta.get("album", ""),
        "position_s": raw.get("time"),
        "duree_s": raw.get("length"),
        "progression_pct": round(float(raw.get("position", 0)) * 100, 1)
        if isinstance(raw.get("position"), (int, float))
        else None,
        "volume_pct": _pourcent_volume(raw.get("volume")),
        "plein_ecran": bool(raw.get("fullscreen")),
        "vitesse": raw.get("rate"),
        "aleatoire": bool(raw.get("random")),
        "boucle": bool(raw.get("loop")),
        "repeter": bool(raw.get("repeat")),
    }


def _feuilles(node: dict, out: list) -> None:
    """Aplatit l'arbre playlist de VLC en liste de pistes."""
    for child in node.get("children") or []:
        if not isinstance(child, dict):
            continue
        if child.get("type") == "leaf":
            out.append(
                {
                    "id": child.get("id"),
                    "nom": child.get("name", ""),
                    "uri": child.get("uri", ""),
                    "duree_s": child.get("duration"),
                    "en_cours": child.get("current") == "current",
                }
            )
        else:
            _feuilles(child, out)


# ---------------------------------------------------------------------------- #
# Garde-fou entrée média (ASI02 chemins + anti-SSRF URLs)                      #
# ---------------------------------------------------------------------------- #


def _url_privee(hote: str) -> bool:
    """True si l'hôte résout vers loopback / réseau privé / link-local."""
    if not hote:
        return True
    try:
        infos = socket.getaddrinfo(hote, None)
    except OSError:
        return True  # non résolvable → on refuse plutôt que de laisser passer
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
            return True
    return False


def _valider_seek(position: str) -> tuple[str | None, str | None]:
    """Valide une position de seek → (valeur, erreur). Allowlist stricte.

    `val` part dans l'URL de commande VLC : n'accepter que alphanum + `+-%`
    (couvre "90", "+30", "-10", "50%", "1h20m") ferme toute injection de
    paramètre supplémentaire.
    """
    val = (position or "").strip()
    if not val:
        return None, 'position vide — ex. "90", "+30", "-10", "50%", "1h20m".'
    if not all(c.isalnum() or c in "+-%" for c in val):
        return None, f'position invalide : {position!r} — ex. "90", "+30", "50%".'
    return val, None


def _valider_volume(pourcent: Any) -> tuple[str | None, str | None]:
    """Valide un volume en pourcent → (valeur VLC 0-512, erreur).

    VLC compte en 0-512 avec 256 = 100 % ; on borne l'entrée à 0-200 % pour ne
    pas laisser le LLM saturer les enceintes.
    """
    try:
        pct = int(pourcent)
    except (TypeError, ValueError):
        return None, f"pourcent invalide : {pourcent!r} — entier entre 0 et 200."
    if not 0 <= pct <= 200:
        return None, f"pourcent hors bornes ({pct}) — attendu entre 0 et 200."
    return str(round(pct * 256 / 100)), None


def _resoudre_media(media: str) -> tuple[str | None, str | None]:
    """Valide `media` (contrôlé par le LLM) → (uri, erreur).

    - http(s):// : autorisé, mais pas vers la loopback / le réseau privé
      (anti-SSRF) sauf VLC_ALLOW_PRIVATE_URLS=1.
    - chemin local : confiné aux racines de _pathguard, converti en file://.
    - tout autre schéma (smb://, ftp://, file://…) : refusé.
    """
    media = (media or "").strip()
    if not media:
        return None, "media vide — donne un chemin de fichier ou une URL http(s)."

    parsed = urlparse(media)
    if parsed.scheme in ("http", "https"):
        if not ALLOW_PRIVATE_URLS and _url_privee(parsed.hostname or ""):
            return None, (
                f"URL refusée ({parsed.hostname}) : pointe vers la loopback ou un réseau "
                "privé. Mets VLC_ALLOW_PRIVATE_URLS=1 dans .env pour un serveur média local."
            )
        return media, None
    if parsed.scheme and len(parsed.scheme) > 1:  # 'C:' sous Windows n'est pas un schéma
        return None, (
            f"schéma « {parsed.scheme}:// » non autorisé — seuls les chemins locaux "
            "et les URL http(s) sont acceptés."
        )

    try:
        chemin = safe_path(media, must_exist=True)
    except PathGuardViolation as exc:
        return None, str(exc)
    except FileNotFoundError as exc:
        return None, str(exc)
    except OSError as exc:
        return None, f"chemin illisible : {exc}"
    return f"file://{quote(str(chemin))}", None


# ---------------------------------------------------------------------------- #
# Outils — lecture d'état                                                      #
# ---------------------------------------------------------------------------- #


async def _etat() -> dict:
    """Implémentation partagée (les outils décorés ne sont plus appelables directement)."""
    raw = await _vlc_get("/requests/status.json")
    return raw if "error" in raw else _resume_etat(raw)


@mcp.tool()
async def etat_lecture() -> dict:
    """Ce que VLC est en train de lire : titre, artiste, position, volume, état.

    Returns:
        {"etat", "titre", "artiste", "position_s", "duree_s", "volume_pct", …}
        ou {"error": "..."} si VLC est injoignable.
    """
    return await _etat()


async def _playlist() -> dict:
    """Implémentation partagée (l'outil MCP et ajouter_a_la_playlist l'appellent).

    Un outil décoré @mcp.tool() n'est plus une fonction appelable directement —
    passer par ce helper évite de dépendre du détail d'emballage de FastMCP.
    """
    raw = await _vlc_get("/requests/playlist.json")
    if "error" in raw:
        return raw
    pistes: list[dict] = []
    _feuilles(raw, pistes)
    return {"pistes": pistes, "total": len(pistes)}


@mcp.tool()
async def lister_playlist() -> dict:
    """Liste les pistes de la playlist VLC et indique celle en cours.

    Returns:
        {"pistes": [{"id", "nom", "uri", "duree_s", "en_cours"}], "total"}
        ou {"error": "..."}.
    """
    return await _playlist()


@mcp.tool()
async def demarrer_vlc() -> dict:
    """Lance VLC s'il ne tourne pas, puis attend que son interface HTTP réponde.

    À utiliser quand un autre outil renvoie « VLC injoignable ». Ne fait rien si
    VLC répond déjà.

    Returns:
        {"demarre": bool, "etat": "..."} ou {"error": "..."}.
    """
    sonde = await _vlc_get("/requests/status.json")
    if "error" not in sonde:
        return {"demarre": False, "message": "VLC répondait déjà.", **_resume_etat(sonde)}
    if sonde["error"] in (_ERR_AUTH, _ERR_NOT_VLC, _ERR_NO_PASSWORD):
        return sonde  # relancer VLC ne réparerait pas un mot de passe faux

    app = Path(os.getenv("VLC_APP_PATH", "/Applications/VLC.app"))
    if not app.exists():
        return {"error": f"VLC introuvable ({app}) — installe-le ou règle VLC_APP_PATH."}
    try:
        # Chemin ABSOLU : le LaunchAgent met /opt/homebrew/bin en tête de PATH,
        # un `open` planté là serait exécuté à la place de celui du système.
        subprocess.run(
            ["/usr/bin/open", "-a", str(app)], check=True, capture_output=True, timeout=15
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return {"error": f"échec du lancement de VLC : {exc}"}

    # Mesuré à froid sur ce poste : l'interface HTTP met ~15 s à répondre après
    # `open -a VLC` (lancement de l'app + init du module http). Une attente de
    # 10 s rendait un faux « VLC ne répond pas » alors qu'il arrivait.
    for _ in range(90):  # jusqu'à ~45 s
        await asyncio.sleep(0.5)
        sonde = await _vlc_get("/requests/status.json")
        if "error" not in sonde:
            return {"demarre": True, **_resume_etat(sonde)}
        if sonde["error"] in (_ERR_AUTH, _ERR_NOT_VLC):
            return sonde  # VLC est bien là, le problème est ailleurs
    return {
        "error": "VLC a été lancé (le process tourne) mais son interface HTTP ne répond "
        "toujours pas après 45 s — extraintf=http absent de vlcrc ? "
        "Relance scripts/setup-vlc-http.sh (VLC fermé), puis rouvre VLC."
    }


# ---------------------------------------------------------------------------- #
# Outils — contrôle de la lecture                                              #
# ---------------------------------------------------------------------------- #


@mcp.tool()
async def lire(media: str = "") -> dict:
    """Lance la lecture. Sans argument : reprend la lecture en cours.

    Args:
        media: chemin d'un fichier local (confiné aux dossiers médias autorisés)
            ou URL http(s). Vide = reprendre/relancer la playlist actuelle.

    Returns:
        L'état de lecture résultant, ou {"error": "..."}.
    """
    def joue(e: dict) -> bool:
        return e["etat"] == "playing"

    # ~5 s d'attente (12 × 0,4 s) : ouvrir un fichier réseau ou un gros conteneur
    # n'est pas instantané, et VLC juste relancé met un moment avant sa 1re lecture.
    if not media:
        etat = await _command("pl_play", attendre=joue, essais=12, delai=0.4)
    else:
        uri, err = _resoudre_media(media)
        if err or uri is None:
            return {"error": err or "média non résolu"}
        etat = await _command("in_play", input=uri, attendre=joue, essais=12, delai=0.4)
    if "error" in etat:
        return etat
    if etat["etat"] != "playing":
        # VLC a accepté la commande (HTTP 200) mais ne joue pas : média illisible,
        # codec manquant, playlist vide. Renvoyer l'état brut ferait passer un
        # échec pour un succès — on le nomme.
        return {
            "error": (
                "VLC a accepté la commande mais ne joue rien "
                f"(état « {etat['etat']} ») — média illisible, codec manquant "
                "ou playlist vide ?"
            ),
            "etat_observe": etat,
        }
    return etat


@mcp.tool()
async def ajouter_a_la_playlist(media: str) -> dict:
    """Enfile un média SANS interrompre la lecture en cours.

    Args:
        media: chemin local (confiné) ou URL http(s).

    Returns:
        {"ajoute": uri, "total": n} ou {"error": "..."}.
    """
    uri, err = _resoudre_media(media)
    if err or uri is None:
        return {"error": err or "média non résolu"}
    avant = await _playlist()
    res = await _command("in_enqueue", input=uri)
    if "error" in res:
        return res
    apres = await _playlist()
    if "error" in apres:
        return apres
    if "error" not in avant and apres["total"] <= avant["total"]:
        return {
            "error": f"VLC a accepté l'ajout de {uri} mais la playlist n'a pas grossi "
            f"({apres['total']} piste(s)) — média illisible ?",
            "pistes": apres["pistes"],
        }
    return {"ajoute": uri, "total": apres["total"], "etat": res.get("etat")}


@mcp.tool()
async def pause() -> dict:
    """Bascule pause/lecture (VLC n'a pas de pause « absolue »).

    Returns:
        L'état résultant — regarder "etat" pour savoir si c'est playing ou paused.
    """
    avant = await _etat()
    if "error" in avant:
        return avant
    initial = avant["etat"]
    return await _command("pl_pause", attendre=lambda e: e["etat"] != initial)


@mcp.tool()
async def stop() -> dict:
    """Arrête la lecture (la playlist est conservée)."""
    return await _command("pl_stop", attendre=lambda e: e["etat"] == "stopped")


@mcp.tool()
async def suivant() -> dict:
    """Passe à la piste suivante de la playlist."""
    return await _command("pl_next")


@mcp.tool()
async def precedent() -> dict:
    """Revient à la piste précédente de la playlist."""
    return await _command("pl_previous")


@mcp.tool()
async def chercher(position: str) -> dict:
    """Déplace la tête de lecture.

    Args:
        position: secondes absolues ("90"), relatif ("+30", "-10"),
            pourcentage ("50%") ou horodatage ("1h20m", "3m30s").

    Returns:
        L'état résultant, ou {"error": "..."}.
    """
    val, err = _valider_seek(position)
    if err or val is None:
        return {"error": err or "position invalide"}
    return await _command("seek", val=val)


@mcp.tool()
async def regler_volume(pourcent: int) -> dict:
    """Règle le volume de VLC.

    Args:
        pourcent: 0 à 200 (100 = volume nominal ; au-delà VLC amplifie).

    Returns:
        L'état résultant (avec "volume_pct"), ou {"error": "..."}.
    """
    val, err = _valider_volume(pourcent)
    if err or val is None:
        return {"error": err or "volume invalide"}
    cible = int(pourcent)
    # Tolérance : VLC stocke en 0-512, l'aller-retour pourcent→VLC→pourcent
    # arrondit (40 % → 102 → 39,8 %).
    return await _command(
        "volume", val=val, attendre=lambda e: abs((e["volume_pct"] or -99) - cible) <= 2
    )


@mcp.tool()
async def vider_playlist() -> dict:
    """Vide la playlist de VLC (arrête la lecture)."""
    etat = await _command("pl_empty", attendre=lambda e: e["etat"] == "stopped")
    if "error" in etat:
        return etat
    playlist = await _playlist()
    reste = playlist.get("total")
    if reste:
        return {"error": f"playlist toujours non vide ({reste} piste(s)) après pl_empty",
                "pistes": playlist["pistes"]}
    return {"vide": True, **etat}


@mcp.tool()
async def plein_ecran() -> dict:
    """Bascule le plein écran.

    Returns:
        L'état résultant — "plein_ecran" indique le nouvel état.
    """
    avant = await _etat()
    if "error" in avant:
        return avant
    initial = avant["plein_ecran"]
    return await _command("fullscreen", attendre=lambda e: e["plein_ecran"] != initial)


# ---------------------------------------------------------------------------- #
# Entrée principale                                                            #
# ---------------------------------------------------------------------------- #


def main() -> None:
    transport = os.getenv("VLC_MCP_TRANSPORT", "stdio").lower()
    port = int(os.getenv("VLC_MCP_PORT", "8091"))
    host = os.getenv("VLC_MCP_HOST", "127.0.0.1")

    if transport == "http":
        logger.info("VLC MCP HTTP : http://%s:%d (VLC sur %s)", host, port, _BASE)
        mcp.run(transport="http", host=host, port=port)
    else:
        logger.info("VLC MCP stdio (VLC sur %s)", _BASE)
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
