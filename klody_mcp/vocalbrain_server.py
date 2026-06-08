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
- statut_generation(session_id)        — progression d'une génération
- resultat_generation(session_id)      — mix final + stems (URLs + chemins locaux)
- lister_sessions(limit)               — générations récentes
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

logger = logging.getLogger(__name__)

LOCALSUNO_URL = os.getenv("LOCALSUNO_URL", "http://127.0.0.1:8766").rstrip("/")
LOCALSUNO_DIR = Path(os.getenv("LOCALSUNO_DIR", str(Path.home() / "local-suno")))
LOCALSUNO_PY = Path(os.getenv("LOCALSUNO_PY", str(LOCALSUNO_DIR / ".venv" / "bin" / "python")))
RVC_LOG_DIR = Path.home() / ".vocalbrain" / "rvc_logs"
HTTP_TIMEOUT = float(os.getenv("VOCALBRAIN_MCP_TIMEOUT", "30"))

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
    duree_sec: int = 30,
    modele_voix: str = "klody",
    transpose: int = 0,
    bpm: int | None = None,
) -> dict:
    """Lance la génération d'une chanson chantée (voix clonée) — NON bloquant.

    Le pipeline (ACE-Step -> RVC -> mix) tourne en arrière-plan. Récupère ensuite
    l'avancement avec statut_generation(session_id), puis le mix final avec
    resultat_generation(session_id) une fois le statut "done".

    Args:
        paroles: Paroles complètes à chanter.
        style: Tags de style en anglais (genre, instruments, ambiance, bpm).
        duree_sec: Durée cible en secondes.
        modele_voix: Nom du modèle de voix clonée (voir lister_voix).
        transpose: Transposition en demi-tons.
        bpm: BPM cible (optionnel).

    Returns:
        {"session_id", "status", "note"} ou {"error": "..."}.
    """
    if not paroles or not paroles.strip():
        return {"error": "paroles requises"}
    body: dict = {
        "prompt": (style or paroles[:60] or "chanson"),
        "duration_sec": int(duree_sec),
        "rvc_transpose": int(transpose),
        "rvc_model": modele_voix or "klody",
        "custom_lyrics": paroles,
        "style_prompt": style or None,
    }
    if bpm:
        body["bpm"] = int(bpm)
    try:
        resp = await _post("/generate", body)
        if resp.status_code == 429:
            return {"error": "file d'attente pleine — réessaie dans un moment."}
        resp.raise_for_status()
        d = resp.json()
        return {
            "session_id": d.get("session_id"),
            "status": d.get("status", "queued"),
            "note": "Suis l'avancement avec statut_generation(session_id), "
            "puis resultat_generation(session_id) quand status=done.",
        }
    except httpx.ConnectError:
        return {"error": _UNREACHABLE}
    except Exception as exc:
        logger.error("generer_chanson: %s", exc, exc_info=True)
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
    nom = (nom_modele or "").strip()
    if not nom:
        return {"error": "nom_modele requis"}
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
        "import sys; sys.path.insert(0, %r)\n"
        "from training.rvc_trainer import train_rvc_model\n"
        "train_rvc_model(model_name=%r, total_epoch=%d)\n"
    ) % (str(LOCALSUNO_DIR), nom, int(epochs))
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
    nom = (nom_modele or "").strip()
    if not nom:
        return {"error": "nom_modele requis"}
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

    if transport == "http":
        logger.info("VocalBrain MCP HTTP : http://%s:%d", host, port)
        mcp.run(transport="http", host=host, port=port)
    else:
        logger.info("VocalBrain MCP stdio")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
