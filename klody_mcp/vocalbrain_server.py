"""VocalBrain MCP server — génération de chant (voix clonée) via local-suno.

Pont MCP léger (même patron que web_server.py / server.py) : il N'embarque PAS
torch/RVC. Il appelle le daemon local-suno (FastAPI, :8766) en HTTP et lit le
dossier des modèles sur disque. N'importe quel client MCP (Klody, Claude
Desktop, Cline…) peut s'y brancher pour générer des chansons avec une voix
clonée, suivre la génération et récupérer le mix + les stems.

La génération est ASYNCHRONE (file d'attente) :
  1. generer_chanson(...)        -> {session_id, status:"queued"}
  2. statut_generation(id)       -> {status, progress, step}   (répéter)
  3. resultat_generation(id)     -> {final_mix_url, stems, ...} (quand status=done)

Démarrage :
    python -m klody_mcp.vocalbrain_server                          # stdio (défaut)
    VOCALBRAIN_MCP_TRANSPORT=http python -m klody_mcp.vocalbrain_server   # :8086

Outils exposés :
- etat_systeme()                       — daemon up ? modèles chargés ? MPS ?
- lister_voix()                        — modèles RVC entraînés disponibles
- generer_chanson(paroles, style, …)   — lance une génération (non bloquant)
- generer_instrumental(style, …)       — morceau SANS voix (non bloquant)
- statut_generation(session_id)        — progression d'une génération
- resultat_generation(session_id)      — mix final + stems (URLs + chemins locaux)
- lister_sessions(limit)               — générations récentes
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP

from klody_mcp import song_structure as ss
from klody_mcp.health import register_health_route

load_dotenv()

logger = logging.getLogger(__name__)

LOCALSUNO_URL = os.getenv("LOCALSUNO_URL", "http://127.0.0.1:8766").rstrip("/")
LOCALSUNO_DIR = Path(os.getenv("LOCALSUNO_DIR", str(Path.home() / "local-suno")))
LOCALSUNO_PY = Path(os.getenv("LOCALSUNO_PY", str(LOCALSUNO_DIR / ".venv" / "bin" / "python")))
RVC_LOG_DIR = Path.home() / ".vocalbrain" / "rvc_logs"
HTTP_TIMEOUT = float(os.getenv("VOCALBRAIN_MCP_TIMEOUT", "30"))

# ASI02 : `nom_modele` (contrôlé par le LLM) sert de composant de chemin
# (`{nom}_train.log`, `{nom}.pth`, `model_name` en aval) ET est injecté dans du
# code Python `-c`. Le repr protège la string, mais `../` ferait fuir le chemin
# du log/modèle. Allowlist stricte : alphanum + _ - (NI point NI séparateur, donc
# ni `..` ni extension), longueur bornée. Rejette tout le reste.
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _valid_model_name(nom: str) -> str | None:
    """Retourne le nom si conforme à l'allowlist, sinon None."""
    nom = (nom or "").strip()
    return nom if _MODEL_NAME_RE.match(nom) else None


mcp = FastMCP("VocalBrain")


# ---------------------------------------------------------------------------- #
# Helpers                                                                       #
# ---------------------------------------------------------------------------- #


def _abs_url(rel: str) -> str:
    """Transforme une URL relative du daemon en URL absolue récupérable."""
    if not rel:
        return ""
    return rel if rel.startswith("http") else f"{LOCALSUNO_URL}{rel}"


def _session_output_dir(session_id: str) -> Path:
    """Dossier de sortie local (le daemon nomme session_{8 premiers caractères})."""
    return LOCALSUNO_DIR / "output" / f"session_{session_id[:8]}"


async def _get(path: str, **kw) -> httpx.Response:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        return await client.get(f"{LOCALSUNO_URL}{path}", **kw)


async def _post(path: str, json_body: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        return await client.post(f"{LOCALSUNO_URL}{path}", json=json_body)


_UNREACHABLE = f"daemon local-suno injoignable ({LOCALSUNO_URL}) — démarre-le : `localsuno serve`"

# Bornes du contrat /generate (storage.models.GenerationRequest côté local-suno).
# Hors bornes, le daemon répond 422 avec une erreur pydantic illisible pour le
# modèle : on borne ici et on DIT ce qui a été borné plutôt que de laisser passer.
# Une seule source dans le dépôt (`song_structure`), qu'un test compare au vrai
# contrat de local-suno : deux copies avaient déjà divergé (120 contre 600).
_DUREE_MIN, _DUREE_MAX = ss.DUREE_MIN_SEC, ss.DUREE_MAX_SEC
_BPM_MIN, _BPM_MAX = ss.BPM_MIN, ss.BPM_MAX

# Arrangement d'un morceau sans chant. Double rôle :
#   1. c'est la convention des données d'entraînement ACE-Step — le daemon écrit
#      exactement ce marqueur quand instrumental=True ;
#   2. le passer en `custom_lyrics` court-circuite le LLM de paroles du daemon
#      (gateway :8090). Pour un instrumental il n'écrirait que du texte que le
#      moteur jette ensuite — un aller-retour réseau et une panne possible en
#      moins, sur un morceau qui n'a aucune parole à écrire.
_MARQUEUR_INSTRUMENTAL = "[Instrumental]"


# ---------------------------------------------------------------------------- #
# Outils MCP                                                                    #
# ---------------------------------------------------------------------------- #


@mcp.tool()
async def etat_systeme() -> dict:
    """État du moteur de génération local-suno (daemon :8766).

    Returns:
        {"disponible", "modeles_charges", "mps", "queue", "url"} ou {"error": "..."}.
    """
    try:
        resp = await _get("/health")
        resp.raise_for_status()
        d = resp.json()
        return {
            "disponible": True,
            "modeles_charges": d.get("models_loaded"),
            "mps": d.get("mps_available"),
            "queue": d.get("queue_size"),
            "url": LOCALSUNO_URL,
        }
    except httpx.ConnectError:
        return {"disponible": False, "error": _UNREACHABLE}
    except Exception as exc:
        logger.error("etat_systeme: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
def lister_voix() -> dict:
    """Liste les voix clonées (modèles RVC) disponibles pour la génération.

    Lit le dossier des modèles local-suno (~/local-suno/models). Exclut les
    poids pré-entraînés et les fichiers de validation.

    Returns:
        {"voix": ["klody", "klody_e250", ...]} ou {"error": "..."}.
    """
    try:
        models_dir = LOCALSUNO_DIR / "models"
        if not models_dir.is_dir():
            return {"voix": [], "note": f"dossier modèles absent : {models_dir}"}
        voix = sorted(
            p.stem
            for p in models_dir.glob("*.pth")
            if "pretrained" not in p.stem and "validation" not in p.stem
        )
        return {"voix": voix}
    except Exception as exc:
        logger.error("lister_voix: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
async def generer_chanson(
    paroles: str,
    style: str = "emotional pop, piano, warm vocals",
    duree_sec: int | None = None,
    modele_voix: str = "klody",
    transpose: int = 0,
    bpm: int | None = None,
    forcer: bool = False,
) -> dict:
    """Lance la génération d'une chanson chantée (voix clonée) — NON bloquant.

    Le pipeline (ACE-Step -> RVC -> mix) tourne en arrière-plan. Récupère ensuite
    l'avancement avec statut_generation(session_id), puis le mix final avec
    resultat_generation(session_id) une fois le statut "done".

    ⚠️ Les paroles sont CONTRÔLÉES avant l'envoi : le moteur ne chante pas plus
    vite que ~2 mots/s, et au-delà de 120 s il génère la chanson en plusieurs
    segments qui se partagent les sections. Un texte trop dense pour la durée est
    tronqué ; un texte qui a moins de sections que de segments est re-chanté à
    l'identique dans les segments de fin. Les deux cas sont REFUSÉS ici plutôt
    que découverts à l'écoute d'une génération de plusieurs minutes.

    Pour une chanson complète, écris les paroles avec des en-têtes de section —
    [Couplet 1] / [Refrain] / [Couplet 2] / [Pont] / [Refrain] — ou au minimum une
    ligne vide entre chaque bloc. Ces en-têtes sont convertis ici au format du
    moteur ; ce sont eux qui portent la structure du morceau.

    Args:
        paroles: Paroles complètes à chanter, en sections (voir ci-dessus).
        style: Tags de style en anglais (genre, instruments, ambiance, bpm).
        duree_sec: Durée cible (10-600). NON FOURNIE = déduite des paroles à
            ~2 mots/s, ce qui est presque toujours le bon choix : une durée
            imposée trop courte fait couper du texte, trop longue fait étirer
            les syllabes.
        modele_voix: Nom du modèle de voix clonée (voir lister_voix).
        transpose: Transposition en demi-tons.
        bpm: BPM cible (60-180, optionnel).
        forcer: True = lance malgré un contrôle de couverture négatif. À n'utiliser
            que si la troncature est assumée (extrait, teaser).

    Returns:
        {"session_id", "status", "parametres", "couverture", "note"} ou
        {"error": "...", "couverture": {...}}.
    """
    if not paroles or not paroles.strip():
        return {"error": "paroles requises"}

    rapport = ss.controler_couverture(paroles, duree_sec)
    # Le contrôle rend un compte rendu chiffré ; on n'en renvoie que la partie
    # utile au modèle (l'arrangement complet ferait doubler la réponse).
    couverture = {k: v for k, v in rapport.items() if k not in ("arrangement", "structure")}
    if not rapport["couvrable"] and not forcer:
        return {"error": ss.message_de_refus(rapport), "couverture": couverture}

    duree = rapport["duree_sec"]
    body: dict = {
        "prompt": (style or paroles[:60] or "chanson"),
        "duration_sec": duree,
        "rvc_transpose": int(transpose),
        "rvc_model": modele_voix or "klody",
        # L'arrangement canonique, PAS le texte brut : c'est lui qui garantit que
        # chaque segment reçoit une section différente et que le refrain est balisé
        # comme un refrain.
        "custom_lyrics": rapport["arrangement"],
        "style_prompt": style or None,
    }
    bornes: list[str] = []
    if duree_sec is not None and duree != int(duree_sec):
        bornes.append(f"durée ramenée à {duree}s (bornes {_DUREE_MIN}-{_DUREE_MAX})")
    if bpm:
        borne_bpm = max(_BPM_MIN, min(int(bpm), _BPM_MAX))
        body["bpm"] = borne_bpm
        if borne_bpm != int(bpm):
            bornes.append(f"bpm ramené à {borne_bpm} (bornes {_BPM_MIN}-{_BPM_MAX})")

    try:
        resp = await _post("/generate", body)
        if resp.status_code == 429:
            return {"error": "file d'attente pleine — réessaie dans un moment."}
        if resp.status_code == 422:
            return {"error": f"paramètres refusés par le daemon : {resp.text[:300]}"}
        resp.raise_for_status()
        d = resp.json()
        alertes = list(rapport["avertissements"])
        if not rapport["couvrable"]:
            alertes.append("FORCÉ malgré : " + " ".join(rapport["problemes"]))
        return {
            "session_id": d.get("session_id"),
            "status": d.get("status", "queued"),
            "parametres": {
                "duree_sec": duree,
                "duree_deduite": duree_sec is None,
                "mots": rapport["mots"],
                "sections": rapport["sections"],
                "segments": rapport["segments"],
                "debit_mots_s": rapport["debit_mots_s"],
                "voix": body["rvc_model"],
            },
            "couverture": couverture,
            "note": "Suis l'avancement avec statut_generation(session_id), "
            "puis resultat_generation(session_id) quand status=done."
            + ("" if not bornes else " ⚠ " + " ; ".join(bornes))
            + ("" if not alertes else " ⚠ " + " ; ".join(alertes)),
        }
    except httpx.ConnectError:
        return {"error": _UNREACHABLE}
    except Exception as exc:
        logger.error("generer_chanson: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
async def generer_instrumental(
    style: str,
    duree_sec: int = 60,
    bpm: int | None = None,
    tonalite: str | None = None,
    lora_style: str | None = None,
) -> dict:
    """Lance la génération d'un morceau INSTRUMENTAL (aucune voix) — NON bloquant.

    À utiliser quand l'utilisateur veut une instru, un beat, une prod, une musique
    de fond : rien n'est chanté, la voix clonée n'entre pas en jeu. Le moteur
    (ACE-Step) rend directement l'instrumental — pas de démux, pas de RVC, pas de
    contrôle qualité des paroles. Pour un morceau CHANTÉ, utilise generer_chanson.

    Récupère ensuite l'avancement avec statut_generation(session_id), puis le mix
    et les stems avec resultat_generation(session_id) une fois le statut "done".

    Args:
        style: Tags de style en anglais (genre, instruments, ambiance) — c'est le
            SEUL conditionnement du moteur ici, donc sois précis :
            "boom bap hip hop, dusty rhodes, upright bass, brushed drums".
        duree_sec: Durée cible en secondes (10-600 ; au-delà du plafond de segment
            le daemon recolle des segments chevauchants).
        bpm: BPM cible (60-180). Non fourni : le daemon retient 90.
        tonalite: Tonalité, format anglais ("F minor", "C# major"). Non fournie :
            le daemon retient Am.
        lora_style: Nom d'un adaptateur de style LoRA du daemon (optionnel).
            "none" = modèle nu ; nom inconnu = retour au défaut de la config.

    Returns:
        {"session_id", "status", "parametres", "note"} ou {"error": "..."}.
    """
    if not style or not style.strip():
        return {"error": "style requis (tags en anglais : genre, instruments, ambiance)"}

    duree = max(_DUREE_MIN, min(int(duree_sec), _DUREE_MAX))
    body: dict = {
        "prompt": style.strip(),
        "duration_sec": duree,
        "style_prompt": style.strip(),
        "custom_lyrics": _MARQUEUR_INSTRUMENTAL,
        "instrumental": True,
    }
    bornes: list[str] = []
    if duree != int(duree_sec):
        bornes.append(f"durée ramenée à {duree}s (bornes {_DUREE_MIN}-{_DUREE_MAX})")
    if bpm:
        borne_bpm = max(_BPM_MIN, min(int(bpm), _BPM_MAX))
        body["bpm"] = borne_bpm
        if borne_bpm != int(bpm):
            bornes.append(f"bpm ramené à {borne_bpm} (bornes {_BPM_MIN}-{_BPM_MAX})")
    if tonalite and tonalite.strip():
        body["key"] = tonalite.strip()
    if lora_style and lora_style.strip():
        body["lora_style"] = lora_style.strip()[:64]

    try:
        resp = await _post("/generate", body)
        if resp.status_code == 429:
            return {"error": "file d'attente pleine — réessaie dans un moment."}
        if resp.status_code == 422:
            return {"error": f"paramètres refusés par le daemon : {resp.text[:300]}"}
        resp.raise_for_status()
        d = resp.json()
        return {
            "session_id": d.get("session_id"),
            "status": d.get("status", "queued"),
            "parametres": {
                "style": body["style_prompt"],
                "duree_sec": duree,
                "bpm": body.get("bpm", "défaut daemon (90)"),
                "tonalite": body.get("key", "défaut daemon (Am)"),
                "voix": None,
            },
            "note": ("Instrumental en génération (aucune voix). Suis l'avancement "
                     "avec statut_generation(session_id), puis "
                     "resultat_generation(session_id) quand status=done."
                     + (" ⚠ " + " ; ".join(bornes) if bornes else "")),
        }
    except httpx.ConnectError:
        return {"error": _UNREACHABLE}
    except Exception as exc:
        logger.error("generer_instrumental: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
async def statut_generation(session_id: str) -> dict:
    """Avancement d'une génération lancée avec generer_chanson.

    Args:
        session_id: id renvoyé par generer_chanson.

    Returns:
        {"status", "progress", "step", "error_message"} ou {"error": "..."}.
        status ∈ queued | generating | mixing | done | error.
    """
    try:
        resp = await _get(f"/sessions/{session_id}/status")
        if resp.status_code == 404:
            return {"error": f"session inconnue : {session_id}"}
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        return {"error": _UNREACHABLE}
    except Exception as exc:
        logger.error("statut_generation: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
async def resultat_generation(session_id: str) -> dict:
    """Récupère le résultat final d'une génération terminée (status=done).

    Args:
        session_id: id de la génération.

    Returns:
        {"final_mix_url", "final_mix_path", "stems": {nom: {url, path}}, "title",
         "bpm", "lyrics", "generation_time_sec"} ; sinon {"status"} (pas prête)
        ou {"error": "..."}.
    """
    try:
        resp = await _get(f"/sessions/{session_id}/result")
        if resp.status_code == 404:
            return {"error": f"session inconnue : {session_id}"}
        if resp.status_code == 202:
            return {"status": "en cours", "note": "génération pas encore terminée — réessaie."}
        resp.raise_for_status()
        d = resp.json()
        out_dir = _session_output_dir(session_id)
        mix_path = out_dir / "final_mix.wav"
        stems: dict[str, dict] = {}
        for name, url in (d.get("stems") or {}).items():
            p = out_dir / "stems" / f"{name}.wav"
            stems[name] = {"url": _abs_url(url), "path": str(p) if p.exists() else None}
        return {
            "session_id": session_id,
            "final_mix_url": _abs_url(d.get("final_mix_url", "")),
            "final_mix_path": str(mix_path) if mix_path.exists() else None,
            "stems": stems,
            "title": d.get("title"),
            "bpm": d.get("bpm"),
            "detected_language": d.get("detected_language"),
            "lyrics": d.get("lyrics"),
            "generation_time_sec": d.get("generation_time_sec"),
        }
    except httpx.ConnectError:
        return {"error": _UNREACHABLE}
    except Exception as exc:
        logger.error("resultat_generation: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
async def lister_sessions(limit: int = 10) -> dict:
    """Liste les générations récentes (id, titre, statut, date).

    Args:
        limit: nombre max de sessions (1-50).

    Returns:
        {"sessions": [{"session_id", "title", "status", "created_at"}, ...]}
        ou {"error": "..."}.
    """
    n = max(1, min(int(limit), 50))
    try:
        resp = await _get("/sessions", params={"limit": n, "offset": 0})
        resp.raise_for_status()
        d = resp.json()
        out = [
            {
                "session_id": s.get("id") or s.get("session_id"),
                "title": s.get("title"),
                "status": s.get("status"),
                "created_at": s.get("created_at"),
            }
            for s in d.get("sessions", [])
        ]
        return {"sessions": out}
    except httpx.ConnectError:
        return {"error": _UNREACHABLE}
    except Exception as exc:
        logger.error("lister_sessions: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
def entrainer_voix(nom_modele: str, epochs: int = 300) -> dict:
    """Lance l'entraînement d'une voix clonée RVC — LONG (~1-3 h), en arrière-plan.

    Entraîne un nouveau modèle sur les échantillons vocaux présents dans
    ~/local-suno/samples (acapellas importées au préalable). ⚠️ Très gourmand en
    CPU/MPS : à n'utiliser qu'à la demande explicite de l'utilisateur. Refuse de
    démarrer si un entraînement tourne déjà. Suis l'avancement avec
    statut_entrainement(nom_modele).

    Args:
        nom_modele: nom du modèle à produire (ex: 'ma_voix_v2').
        epochs: nombre d'epochs (qualité ; plus = mieux mais plus long).

    Returns:
        {"status", "modele", "epochs", "echantillons", "log"} ou {"error": "..."}.
    """
    nom = _valid_model_name(nom_modele)
    if not nom:
        return {"error": "nom_modele invalide (attendu : lettres/chiffres/_/-, "
                         "1-64 caractères, sans séparateur ni '..')"}
    samples_dir = LOCALSUNO_DIR / "samples"
    n_samples = len(list(samples_dir.glob("segment_*.wav"))) if samples_dir.is_dir() else 0
    if n_samples == 0:
        return {"error": f"aucun échantillon dans {samples_dir} — importe des acapellas d'abord."}
    if not LOCALSUNO_PY.exists():
        return {"error": f"venv local-suno introuvable : {LOCALSUNO_PY}"}
    # Garde-fou : ne pas lancer un 2e entraînement en parallèle (ils se tueraient mutuellement le CPU).
    try:
        running = subprocess.run(
            ["pgrep", "-f", "rvc_trainer|train.py|resume_klody"],
            capture_output=True, text=True, timeout=5,
        )
        if running.stdout.strip():
            return {"error": "un entraînement est déjà en cours — attends qu'il se termine."}
    except Exception:
        pass

    RVC_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RVC_LOG_DIR / f"{nom}_train.log"
    code = (
        f"import sys; sys.path.insert(0, {str(LOCALSUNO_DIR)!r})\n"
        "from training.rvc_trainer import train_rvc_model\n"
        f"train_rvc_model(model_name={nom!r}, total_epoch={int(epochs)})\n"
    )
    try:
        with open(log_path, "w") as lf:
            subprocess.Popen(
                [str(LOCALSUNO_PY), "-c", code],
                stdout=lf, stderr=subprocess.STDOUT,
                cwd=str(LOCALSUNO_DIR), start_new_session=True,
            )
    except Exception as exc:
        logger.error("entrainer_voix: %s", exc, exc_info=True)
        return {"error": str(exc)}
    return {
        "status": "lancé",
        "modele": nom,
        "epochs": int(epochs),
        "echantillons": n_samples,
        "log": str(log_path),
        "note": "Entraînement en arrière-plan (LONG). Suis-le avec statut_entrainement(nom_modele).",
    }


@mcp.tool()
def statut_entrainement(nom_modele: str) -> dict:
    """État d'un entraînement de voix (en cours ou terminé).

    Args:
        nom_modele: nom du modèle entraîné.

    Returns:
        {"modele", "termine", "log_extrait"} ou {"error": "..."}.
    """
    nom = _valid_model_name(nom_modele)
    if not nom:
        return {"error": "nom_modele invalide (attendu : lettres/chiffres/_/-, "
                         "1-64 caractères, sans séparateur ni '..')"}
    termine = (LOCALSUNO_DIR / "models" / f"{nom}.pth").exists()
    log_path = RVC_LOG_DIR / f"{nom}_train.log"
    extrait = ""
    if log_path.exists():
        txt = log_path.read_text(errors="replace")
        extrait = "\n".join(txt.splitlines()[-15:])
    return {
        "modele": nom,
        "termine": termine,
        "log_extrait": (extrait[-1500:] if extrait else "(pas de log pour ce modèle)"),
    }


# ---------------------------------------------------------------------------- #
# Entrée principale                                                            #
# ---------------------------------------------------------------------------- #


def main() -> None:
    transport = os.getenv("VOCALBRAIN_MCP_TRANSPORT", "stdio").lower()
    port = int(os.getenv("VOCALBRAIN_MCP_PORT", "8086"))
    host = os.getenv("VOCALBRAIN_MCP_HOST", "127.0.0.1")

    register_health_route(mcp, "com.klody.vocalbrain-mcp")
    if transport == "http":
        logger.info("VocalBrain MCP HTTP : http://%s:%d", host, port)
        mcp.run(transport="http", host=host, port=port)
    else:
        logger.info("VocalBrain MCP stdio")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
