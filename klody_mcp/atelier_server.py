"""Atelier MCP server — « écoute ce morceau » : analyse musicale complète via suite-musicale.

Bras MCP du domaine ANALYSE AUDIO (MISSION-D 6.1, 2026-09-15), sibling de
samplebrain_server.py (stdio, aucun modèle chargé ici). Il consomme l'organe
**suite-musicale** derrière son interface publique — `python -m core.cli hub-analyze`
dans SON venv (`~/suite-musicale/.venv`) — qui enchaîne : séparation (htdemucs +
mel-roformer voix) → batterie→MIDI (Onsets&Frames) → accords/clé (song2chords :8793)
→ BPM/mode/caption/instruments/sections (mu-bridge MOSS) → annexes (`drums.json`,
`loops.json`) → `hub_manifest.json` avec un bloc `summary` lisible d'un coup.

**Pourquoi un trio job.** Mesuré le 2026-09-15 : 30 s pour un extrait de 40 s
(séparation 9 s + MOSS 19 s), donc 2-3 min sur un morceau entier — au-delà du
timeout MCP de 60 s (`tools/mcp_bridge.py:_CALL_TIMEOUT`). Même patron que
`composer_demo → statut_demo → resultat_demo` : chaque outil répond en < 1 s.

**Idempotence.** `job_id` = sha256 du fichier + profil + a2a. Le résultat vit dans
`ATELIER_CACHE_DIR/<job_id>/` ; relancer le même morceau = cache hit immédiat
(gate 6.1 : « 2ᵉ appel même fichier < 1 s »). Un job en cours n'est jamais dupliqué.

**Frontière.** Le venv de l'agent n'importe rien de la suite (torch/mlx restent
derrière le sous-processus). Les chemins d'entrée passent `_pathguard.safe_path`
(racines audio + `_uploads` de l'API) : pas de lecture arbitraire.

Outils :
- analyser_morceau(chemin, a2a, profil) — lance (ou retrouve) l'analyse, NON bloquant.
- statut_analyse(job_id)               — running | done | error, temps écoulé, journal.
- resultat_analyse(job_id, detail)     — le `summary` (bpm, clé, accords, sections,
                                          caption MOSS, batterie, boucles) + chemins.
- lister_analyses(k)                    — analyses déjà en cache (réutilisables).
- statut_atelier()                      — sondable (C2) : venv suite + `hub-status`.

Démarrage :
    python -m klody_mcp.atelier_server                            # stdio (défaut)
    ATELIER_MCP_TRANSPORT=http python -m klody_mcp.atelier_server  # :8101
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess

# Lancé en stdio par `fastmcp.Client(<chemin du script>)` : `sys.path[0]` est alors
# `klody_mcp/`, pas la racine du dépôt, et `import klody_mcp` échoue (constaté au
# 1er e2e du 2026-09-15 : « No module named 'klody_mcp' », connexion fermée sans
# message côté client). Les autres serveurs stdio n'importent rien du paquet ;
# celui-ci a besoin du garde de chemins → on ajoute la racine, une fois.
import sys as _sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP

_RACINE = str(Path(__file__).resolve().parent.parent)
if _RACINE not in _sys.path:
    _sys.path.insert(0, _RACINE)

from klody_mcp._pathguard import AUDIO_ROOTS, PathGuardViolation, safe_path  # noqa: E402

load_dotenv()

logger = logging.getLogger(__name__)

SUITE_ROOT = Path(os.getenv("ATELIER_SUITE_ROOT", "~/suite-musicale")).expanduser()
SUITE_PYTHON = Path(os.getenv("ATELIER_PYTHON", str(SUITE_ROOT / ".venv" / "bin" / "python"))).expanduser()
CACHE_DIR = Path(os.getenv("ATELIER_CACHE_DIR", "~/.klody/atelier")).expanduser()
# Version de pipeline dans la clé de cache : une nouvelle version de la suite
# (ou de ce serveur) invalide les analyses précédentes au lieu de les resservir.
PIPELINE_VERSION = os.getenv("ATELIER_PIPELINE_VERSION", "hub-v1")
PROFILS = ("balanced", "hq", "mobile", "live")

_AUDIO_EXTS = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a", ".ogg"}

mcp = FastMCP("atelier")


# ----------------------------------------------------------------------------- #
# Helpers purs                                                                     #
# ----------------------------------------------------------------------------- #

def _roots() -> list[Path]:
    """Racines audio + dossier `_uploads` de l'API (fichiers joints par l'utilisateur)."""
    roots = list(AUDIO_ROOTS)
    try:
        import config  # racine du projet agent (UPLOADS_DIR) — optionnel hors agent
        roots.append(Path(config.UPLOADS_DIR).resolve())
    except Exception:
        pass
    return roots


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def job_id_for(sha: str, *, profil: str, a2a: bool) -> str:
    return f"{sha[:16]}-{profil}-{'a2a' if a2a else 'core'}-{PIPELINE_VERSION}"


def _job_dir(job_id: str) -> Path:
    # job_id vient soit de nous, soit du LLM : on refuse tout ce qui ressemble à un chemin.
    if not job_id or "/" in job_id or ".." in job_id or job_id.startswith("."):
        raise ValueError(f"job_id invalide : {job_id!r}")
    return CACHE_DIR / job_id


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


def _read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _tail(p: Path, n: int = 3) -> list[str]:
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [ln[:200] for ln in lines[-n:]]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _etat(job_dir: Path) -> dict[str, Any]:
    """État d'un job depuis le disque (source de vérité = manifeste, puis pid)."""
    meta = _read_json(job_dir / "job.json") or {}
    manifest = _read_json(job_dir / "hub_manifest.json")
    started = meta.get("started_ts")
    elapsed = round(time.time() - started, 1) if started else None
    if manifest is not None:
        # Durée réelle = écriture du manifeste − départ (pas « maintenant − départ », qui
        # grossirait à chaque consultation : constaté 675 s pour un job de 30 s).
        try:
            fin = (job_dir / "hub_manifest.json").stat().st_mtime
            elapsed = round(fin - started, 1) if started else None
        except OSError:
            pass
        return {"status": "done", "all_core_ok": manifest.get("all_core_ok"),
                "elapsed_sec": elapsed, "meta": meta}
    if _pid_alive(meta.get("pid")):
        return {"status": "running", "elapsed_sec": elapsed, "meta": meta,
                "journal": _tail(job_dir / "hub.log")}
    if meta:
        return {"status": "error", "elapsed_sec": elapsed, "meta": meta,
                "journal": _tail(job_dir / "hub.log", 8),
                "detail": "le sous-processus s'est terminé sans manifeste"}
    return {"status": "unknown", "detail": f"job inconnu : {job_dir.name}"}


def _lancer(job_dir: Path, audio: Path, *, profil: str, a2a: bool, sha: str) -> dict[str, Any]:
    """Démarre `hub-analyze` en sous-processus détaché (le serveur MCP reste réactif)."""
    job_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(SUITE_PYTHON), "-m", "core.cli", "hub-analyze", str(audio),
           "--out", str(job_dir), "--profile", profil, "--json"]
    if a2a:
        cmd.append("--a2a")
    log = job_dir / "hub.log"
    with log.open("ab") as fh:
        # stdin=DEVNULL : ce serveur tourne en stdio (JSON-RPC sur son stdin) ; un enfant qui
        # hériterait de ce descripteur garderait le canal ouvert et pourrait y lire.
        proc = subprocess.Popen(
            cmd, cwd=str(SUITE_ROOT), stdin=subprocess.DEVNULL, stdout=fh,
            stderr=subprocess.STDOUT, start_new_session=True)
    meta = {"job_id": job_dir.name, "input": str(audio), "sha256": sha, "profil": profil,
            "a2a": a2a, "pid": proc.pid, "started": _now(), "started_ts": time.time(),
            "cmd": cmd, "pipeline": PIPELINE_VERSION}
    (job_dir / "job.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


def _resume(job_dir: Path, manifest: dict) -> dict[str, Any]:
    out = Path(manifest.get("out_dir") or job_dir)
    mods = {k: {kk: v.get(kk) for kk in ("ok", "sec", "detail") if kk in v}
            for k, v in (manifest.get("modules") or {}).items()}
    summ = manifest.get("summary") or {}
    paths = {"hub_manifest": str(job_dir / "hub_manifest.json"),
             "analysis_json": summ.get("analysis_json") or str(out / "analysis.json"),
             "drums_json": str(out / "drums.json") if (out / "drums.json").is_file() else None,
             "loops_json": str(out / "loops.json") if (out / "loops.json").is_file() else None,
             "drums_midi": summ.get("drums_midi"),
             "stems": summ.get("stems")}
    return {"job_id": job_dir.name, "input": manifest.get("input"), "out_dir": str(out),
            "all_core_ok": manifest.get("all_core_ok"), "modules": mods,
            "summary": {k: v for k, v in summ.items() if k not in ("analysis_json", "stems")},
            "paths": paths}


# ----------------------------------------------------------------------------- #
# Outils                                                                           #
# ----------------------------------------------------------------------------- #

@mcp.tool()
def analyser_morceau(chemin: str, a2a: bool = False, profil: str = "balanced") -> dict:
    """Écoute un morceau : lance (ou retrouve) l'analyse complète — NON bloquant.

    Produit stems, BPM, tonalité, accords horodatés, sections, batterie (hits,
    groove 16 pas, microtiming, MIDI), boucles par section, caption/instruments
    MOSS. Suis avec statut_analyse(job_id) puis resultat_analyse(job_id) quand
    status=done (compte 1-3 min à froid ; immédiat si déjà analysé).

    Args:
        chemin: fichier audio (wav/aiff/flac/mp3/m4a/ogg) sous les racines audio ou
            joint par l'utilisateur via /api/upload.
        a2a: True = ajoute la transformation audio→audio (instrumental-clone :8797,
            ~2 min de plus). Défaut False.
        profil: profil de séparation : balanced (défaut) | hq | mobile | live.

    Returns:
        {"job_id", "status": "running"|"done", "cache": bool, ...} ou {"error": "..."}.
    """
    if profil not in PROFILS:
        return {"error": f"profil inconnu {profil!r} (attendu : {', '.join(PROFILS)})"}
    try:
        audio = safe_path(chemin, roots=_roots())
    except PathGuardViolation as exc:
        return {"error": f"chemin refusé (hors racines audio / _uploads) : {exc}"}
    except FileNotFoundError:
        return {"error": f"fichier introuvable : {chemin}"}
    if audio.suffix.lower() not in _AUDIO_EXTS:
        return {"error": f"extension non audio : {audio.suffix or '(aucune)'}"}
    if not SUITE_PYTHON.is_file():
        return {"error": f"venv suite-musicale introuvable : {SUITE_PYTHON} "
                         "(ATELIER_PYTHON / ATELIER_SUITE_ROOT)"}
    try:
        sha = _sha256(audio)
    except OSError as exc:
        return {"error": f"lecture impossible : {exc}"}
    job_id = job_id_for(sha, profil=profil, a2a=a2a)
    job_dir = _job_dir(job_id)
    etat = _etat(job_dir)
    if etat["status"] == "done":
        return {"job_id": job_id, "status": "done", "cache": True,
                "note": "déjà analysé — appelle resultat_analyse(job_id)"}
    if etat["status"] == "running":
        return {"job_id": job_id, "status": "running", "cache": False,
                "elapsed_sec": etat.get("elapsed_sec"),
                "note": "analyse déjà en cours — suis avec statut_analyse(job_id)"}
    try:
        meta = _lancer(job_dir, audio, profil=profil, a2a=a2a, sha=sha)
    except OSError as exc:
        return {"error": f"impossible de lancer hub-analyze : {exc}"}
    return {"job_id": job_id, "status": "running", "cache": False, "pid": meta["pid"],
            "input": str(audio), "eta": "1-3 min (30 s par 40 s d'audio mesuré)",
            "note": "suis avec statut_analyse(job_id), puis resultat_analyse(job_id)"}


@mcp.tool()
def statut_analyse(job_id: str) -> dict:
    """Avancement d'une analyse lancée avec analyser_morceau.

    Args:
        job_id: id renvoyé par analyser_morceau.

    Returns:
        {"job_id", "status": running|done|error|unknown, "elapsed_sec", "journal"?}
    """
    try:
        job_dir = _job_dir(job_id)
    except ValueError as exc:
        return {"error": str(exc)}
    etat = _etat(job_dir)
    out = {"job_id": job_id, "status": etat["status"], "elapsed_sec": etat.get("elapsed_sec")}
    for k in ("all_core_ok", "journal", "detail"):
        if k in etat:
            out[k] = etat[k]
    if etat["status"] == "done":
        out["note"] = "prête — appelle resultat_analyse(job_id)"
    return out


@mcp.tool()
def resultat_analyse(job_id: str, detail: str = "resume") -> dict:
    """Résultat d'une analyse terminée (status=done).

    Args:
        job_id: id de l'analyse.
        detail: "resume" (défaut) = bloc summary + chemins ; "complet" = ajoute le
            manifeste intégral (modules, timings, détails d'erreur).

    Returns:
        {"job_id", "input", "all_core_ok", "modules", "summary": {bpm, key, time_signature,
         n_chords, chords[], sections[], moss{caption, instruments, sections, mode},
         drums{hit_count, kick, snare, hihat, mean_microtiming_ms, groove_template},
         drums_midi, loops[]}, "paths": {...}} ; sinon {"status"} ou {"error"}.
    """
    try:
        job_dir = _job_dir(job_id)
    except ValueError as exc:
        return {"error": str(exc)}
    etat = _etat(job_dir)
    if etat["status"] != "done":
        return {"job_id": job_id, "status": etat["status"], "elapsed_sec": etat.get("elapsed_sec"),
                "note": "pas encore terminée — réessaie via statut_analyse" if etat["status"] == "running"
                else etat.get("detail")}
    manifest = _read_json(job_dir / "hub_manifest.json") or {}
    res = _resume(job_dir, manifest)
    if detail == "complet":
        res["manifest"] = manifest
    return res


@mcp.tool()
def lister_analyses(k: int = 10) -> dict:
    """Analyses déjà en cache (réutilisables sans relancer la séparation).

    Args:
        k: nombre max d'entrées (plus récentes d'abord).

    Returns:
        {"analyses": [{job_id, input, status, started, all_core_ok}], "cache_dir"}
    """
    k = max(1, min(int(k), 100))
    rows = []
    if CACHE_DIR.is_dir():
        for d in sorted(CACHE_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not d.is_dir():
                continue
            etat = _etat(d)
            meta = etat.get("meta") or {}
            rows.append({"job_id": d.name, "input": meta.get("input"), "status": etat["status"],
                         "started": meta.get("started"), "all_core_ok": etat.get("all_core_ok")})
            if len(rows) >= k:
                break
    return {"analyses": rows, "cache_dir": str(CACHE_DIR)}


@mcp.tool()
def statut_atelier() -> dict:
    """Sondable (C2) : la suite-musicale est-elle utilisable, et ses modules sont-ils verts ?

    Returns:
        {"ok", "suite_root", "python", "modules": [...] (hub-status), "cache": {"dir", "n"}}
    """
    out: dict[str, Any] = {"suite_root": str(SUITE_ROOT), "python": str(SUITE_PYTHON),
                           "cache": {"dir": str(CACHE_DIR),
                                     "n": sum(1 for _ in CACHE_DIR.iterdir()) if CACHE_DIR.is_dir() else 0}}
    if not SUITE_PYTHON.is_file():
        out.update(ok=False, detail="venv suite-musicale introuvable")
        return out
    try:
        r = subprocess.run(
            [str(SUITE_PYTHON), "-m", "core.cli", "hub-status", "--json"],
            cwd=str(SUITE_ROOT), capture_output=True, text=True, timeout=60, check=False)
        data = json.loads(r.stdout.strip() or "{}")
        out.update(ok=bool(data.get("all_ok")), modules=data.get("modules"),
                   note=None if data.get("all_ok") else
                   "module(s) rouge(s) : hub-analyze continue en dégradé (I3)")
    except Exception as exc:
        out.update(ok=False, detail=f"{type(exc).__name__}: {exc}")
    return out


if __name__ == "__main__":
    if os.getenv("ATELIER_MCP_TRANSPORT", "stdio").lower() == "http":
        from klody_mcp.health import register_health_route
        register_health_route(mcp, "com.klody.atelier-mcp")
        mcp.run(transport="http", host="127.0.0.1",
                # 8101 : hors du bloc 8080-8099 déjà dense (8096 = sidecar lyrics-corpus, 8098 = worker vision).
                port=int(os.getenv("ATELIER_MCP_PORT", "8101")), path="/mcp")
    else:
        mcp.run()
