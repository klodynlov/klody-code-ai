"""DreamX-World MCP server — génération de vidéo (world model) 100% locale sur MPS.

Pont MCP léger (même patron que vocalbrain_server.py) : il N'embarque PAS torch.
Il lance le wrapper offline `dreamx_generate.py` (dans ~/dreamx-spike, venv isolé)
en arrière-plan, puis expose le suivi. Klody appelle ces outils comme un bras
moteur (cf. generer_chanson).

⚠️ Le rendu est LONG (offline batch, pas temps réel) : ~15-30 s/step selon la
résolution sur M5 Max. Un clip court (25 frames / 15 steps / 480×832) ≈ 5-8 min.
DreamX-World-5B-Cam exige une IMAGE de départ (image+texte+caméra → vidéo).

La génération est ASYNCHRONE (un rendu à la fois, GPU partagé) :
  1. generer_video_monde(prompt, image, ...) -> {job_id, status:"lancé"}
  2. statut_video_monde(job_id)              -> {status, progress, video} (répéter)

Démarrage :
    python -m klody_mcp.dreamx_server                          # stdio (défaut)
    DREAMX_MCP_TRANSPORT=http python -m klody_mcp.dreamx_server   # :8089
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

logger = logging.getLogger(__name__)

DREAMX_DIR = Path(os.getenv("DREAMX_DIR", str(Path.home() / "dreamx-spike")))
DREAMX_PY = Path(os.getenv("DREAMX_PY", str(DREAMX_DIR / ".venv" / "bin" / "python")))
DREAMX_GEN = DREAMX_DIR / "dreamx_generate.py"
REPO = DREAMX_DIR / "DreamX-World"
JOBS = DREAMX_DIR / "jobs"
GATEWAY = os.getenv("KLODY_GATEWAY_URL", "http://127.0.0.1:8090")

mcp = FastMCP("DreamX")


# ---------------------------------------------------------------------------- #
# Helpers                                                                       #
# ---------------------------------------------------------------------------- #


def _render_running() -> bool:
    """Un rendu DreamX tourne-t-il déjà ? (GPU = un seul à la fois)."""
    try:
        r = subprocess.run(["pgrep", "-f", "inference_dreamx5b"],
                           capture_output=True, text=True, timeout=5)
        return bool(r.stdout.strip())
    except Exception:
        return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _tail_progress(log_path: Path) -> str:
    """Dernière ligne de barre de progression (NN/MM steps) du log."""
    if not log_path.exists():
        return ""
    try:
        txt = log_path.read_text(errors="replace").replace("\r", "\n")
        for line in reversed(txt.splitlines()):
            if "/" in line and ("it/s" in line or "s/it" in line or "%|" in line):
                return line.strip()[:120]
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------- #
# Outils MCP                                                                    #
# ---------------------------------------------------------------------------- #


@mcp.tool()
def etat_systeme() -> dict:
    """État du moteur DreamX-World local (portage MPS dans ~/dreamx-spike).

    Returns:
        {"pret", "venv", "poids", "rendu_en_cours", "image_demo"} ou {"error": "..."}.
    """
    try:
        weights = {
            "wan_vae": (REPO / "Wan2.2-TI2V-5B" / "Wan2.2_VAE.pth").exists(),
            "t5": (REPO / "Wan2.2-TI2V-5B" / "models_t5_umt5-xxl-enc-bf16.pth").exists(),
            "dreamx_cam": (REPO / "DreamX-World-5B-Cam").is_dir(),
        }
        demo = REPO / "demo" / "36_Tilt_Down.png"
        return {
            "pret": DREAMX_PY.exists() and DREAMX_GEN.exists() and all(weights.values()),
            "venv": DREAMX_PY.exists(),
            "wrapper": DREAMX_GEN.exists(),
            "poids": weights,
            "rendu_en_cours": _render_running(),
            "image_demo": str(demo) if demo.exists() else None,
            "note": "offline batch (~5-8 min/clip court). DreamX-Cam exige une image de départ.",
        }
    except Exception as exc:
        logger.error("etat_systeme: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
def generer_video_monde(
    prompt: str,
    image: str,
    actions: str = "wk",
    speeds: str = "6",
    frames: int = 25,
    steps: int = 15,
    height: int = 480,
    width: int = 832,
    dtype: str = "bfloat16",
) -> dict:
    """Génère une vidéo navigable depuis une image + texte + caméra — NON bloquant.

    DreamX-World-5B-Cam (world model) tourne en arrière-plan sur MPS (M5 Max).
    LONG : ~5-8 min pour un clip court. Suis l'avancement avec
    statut_video_monde(job_id). Libère la RAM Klody (coder) automatiquement.

    Args:
        prompt: Description visuelle en anglais (réutiliser style_prompt d'une chanson, p.ex.).
        image: Chemin ABSOLU d'une image de départ (requis — seed image).
        actions: Commandes caméra (liste séparée par virgules) — wk,w,wj,s,a,d, etc.
        speeds: Vitesses par action (liste d'entiers, même longueur qu'actions).
        frames: Nombre d'images (25 ≈ 1.5s @16fps ; 81 ≈ 5s).
        steps: Pas de diffusion (15 = brouillon rapide, 50 = qualité).
        height: Hauteur (multiple de 16 ; 480 rapide, 704 natif).
        width: Largeur (multiple de 16 ; 832 rapide, 1280 natif).
        dtype: bfloat16 (recommandé) | float16 | float32.

    Returns:
        {"job_id", "status", "note"} ou {"error": "..."}.
    """
    if not prompt or not prompt.strip():
        return {"error": "prompt requis"}
    if not image or not Path(image).expanduser().is_file():
        return {"error": f"image de départ introuvable : {image} (DreamX-Cam exige une seed image)"}
    if not DREAMX_PY.exists() or not DREAMX_GEN.exists():
        return {"error": f"portage DreamX absent : {DREAMX_DIR} (lancer le spike d'abord)"}
    if _render_running():
        return {"error": "un rendu DreamX tourne déjà — un seul à la fois (GPU). Réessaie après."}

    job_id = uuid.uuid4().hex[:8]
    job_dir = JOBS / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    spec = [{
        "image_path": str(Path(image).expanduser()),
        "caption": prompt.strip(),
        "action_seq": [a for a in actions.split(",") if a],
        "action_speed_list": [int(s) for s in speeds.split(",") if s.strip()],
    }]
    spec_path = job_dir / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False))
    log_path = job_dir / "run.log"
    result_path = job_dir / "result.json"

    cmd = [
        str(DREAMX_PY), str(DREAMX_GEN),
        "--spec", str(spec_path),
        "--output-dir", str(job_dir),
        "--result-file", str(result_path),
        "--frames", str(frames), "--steps", str(steps),
        "--height", str(height), "--width", str(width),
        "--dtype", dtype, "--free-ram", "--gateway", GATEWAY,
    ]
    try:
        with open(log_path, "w") as lf:
            p = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT,
                                 cwd=str(DREAMX_DIR), start_new_session=True)
        (job_dir / "pid").write_text(str(p.pid))
    except Exception as exc:
        logger.error("generer_video_monde: %s", exc, exc_info=True)
        return {"error": str(exc)}

    return {
        "job_id": job_id,
        "status": "lancé",
        "note": "Rendu offline en arrière-plan (LONG). Suis-le avec statut_video_monde(job_id).",
        "reglages": {"frames": frames, "steps": steps, "res": f"{height}x{width}", "dtype": dtype},
    }


@mcp.tool()
def statut_video_monde(job_id: str) -> dict:
    """Avancement d'un rendu lancé avec generer_video_monde.

    Args:
        job_id: id renvoyé par generer_video_monde.

    Returns:
        {"status", "progress", "video", "elapsed_s"} ou {"error": "..."}.
        status ∈ en cours | done | error.
    """
    job_dir = JOBS / job_id
    if not job_dir.is_dir():
        return {"error": f"job inconnu : {job_id}"}
    result_path = job_dir / "result.json"
    if result_path.exists():
        try:
            res = json.loads(result_path.read_text())
        except Exception as exc:
            return {"error": f"result illisible : {exc}"}
        outs = res.get("outputs", [])
        video = outs[0]["video"] if outs and outs[0].get("exists") else None
        return {
            "status": "done" if res.get("ok") else "error",
            "video": video,
            "elapsed_s": res.get("elapsed_s"),
            "reglages": res.get("settings"),
        }
    # pas encore de résultat : process vivant ?
    pid_file = job_dir / "pid"
    alive = pid_file.exists() and _pid_alive(int(pid_file.read_text().strip() or 0))
    if alive:
        return {"status": "en cours", "progress": _tail_progress(job_dir / "run.log")}
    return {"status": "error", "note": "process terminé sans résultat — voir run.log",
            "log_extrait": _tail_progress(job_dir / "run.log") or "(vide)"}


@mcp.tool()
def lister_videos(limit: int = 10) -> dict:
    """Liste les rendus récents (job_id, statut, vidéo).

    Args:
        limit: nombre max (1-50).

    Returns:
        {"jobs": [{"job_id", "status", "video"}, ...]} ou {"error": "..."}.
    """
    n = max(1, min(int(limit), 50))
    if not JOBS.is_dir():
        return {"jobs": []}
    dirs = sorted(JOBS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:n]
    out = []
    for d in dirs:
        if not d.is_dir():
            continue
        rp = d / "result.json"
        status, video = "en cours", None
        if rp.exists():
            try:
                r = json.loads(rp.read_text())
                status = "done" if r.get("ok") else "error"
                o = r.get("outputs", [])
                video = o[0]["video"] if o and o[0].get("exists") else None
            except Exception:
                status = "error"
        out.append({"job_id": d.name, "status": status, "video": video})
    return {"jobs": out}


# ---------------------------------------------------------------------------- #
# Entrée principale                                                            #
# ---------------------------------------------------------------------------- #


def main() -> None:
    transport = os.getenv("DREAMX_MCP_TRANSPORT", "stdio").lower()
    port = int(os.getenv("DREAMX_MCP_PORT", "8091"))  # 8089 déjà pris par un autre serveur MCP local
    host = os.getenv("DREAMX_MCP_HOST", "127.0.0.1")

    if transport == "http":
        logger.info("DreamX MCP HTTP : http://%s:%d", host, port)
        mcp.run(transport="http", host=host, port=port)
    else:
        logger.info("DreamX MCP stdio")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
