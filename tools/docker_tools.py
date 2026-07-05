"""Introspection Docker en LECTURE SEULE — outil runtime (Roadmap v2 #10).

Permet à Klody d'inspecter l'état Docker local (conteneurs, images, logs, stats)
sans jamais MUTER le démon : aucune opération `run`/`build`/`exec`/`rm`/`stop` —
celles-ci sont une primitive d'évasion de l'hôte (`docker run -v /:/host
--privileged`) qui relève d'un incrément sécurisé dédié.

Sécurité (anti-injection de commande) :
- `subprocess.run([...], shell=False)` — **jamais** de shell, jamais d'f-string.
- Les sous-commandes Docker sont **hardcodées** par action (jamais l'entrée user).
- La seule entrée utilisateur, `target` (nom/ID de conteneur ou d'image), est
  validée contre un charset strict et ne peut pas commencer par `-` (pas de flag
  injecté type `--format`/`--since`) ni contenir de métacaractère shell.
- `docker` absent / démon injoignable → message clair, jamais d'exception brute.
- Sortie plafonnée (octets) et timeout borné.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess

logger = logging.getLogger(__name__)

_TIMEOUT_S = 15
_MAX_OUTPUT = 20_000          # octets de sortie max réinjectés au LLM
_MAX_TAIL = 500               # lignes de logs max
_DEFAULT_TAIL = 200

# Cible autorisée : nom/ID Docker. Alphanumérique + . _ - : / (pas d'espace, pas de
# métacaractère shell, ne commence jamais par '-' → aucun flag injectable).
_TARGET_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,127}$")

# Actions LECTURE SEULE → argv Docker figé. `{target}`/`{tail}` sont substitués par
# des valeurs VALIDÉES, insérées comme éléments argv distincts (jamais concaténés).
_NEEDS_TARGET = frozenset({"inspect", "logs"})
_READ_ACTIONS = frozenset({
    "ps", "images", "inspect", "logs", "stats", "version", "df",
})


class DockerToolError(Exception):
    """Entrée invalide (action inconnue, cible malformée)."""


def _argv(action: str, target: str, tail: int) -> list[str]:
    """Construit l'argv Docker figé pour une action lecture seule."""
    if action == "ps":
        return ["ps", "--all", "--no-trunc", "--format",
                "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"]
    if action == "images":
        return ["images", "--format",
                "table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}"]
    if action == "stats":
        return ["stats", "--no-stream", "--format",
                "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"]
    if action == "version":
        return ["version"]
    if action == "df":
        return ["system", "df"]
    if action == "inspect":
        return ["inspect", target]
    if action == "logs":
        return ["logs", "--tail", str(tail), target]
    raise DockerToolError(f"action inconnue : '{action}'")  # pragma: no cover


def docker_control(action: str, target: str = "", tail: int = _DEFAULT_TAIL) -> dict:
    """Exécute une commande Docker LECTURE SEULE et renvoie un dict structuré.

    Retour : {ok, action, output} ou {ok: False, error}.
    """
    action = (action or "").strip().lower()
    if action not in _READ_ACTIONS:
        return {"ok": False, "error": (
            f"Action '{action}' non supportée. Actions lecture seule : "
            f"{', '.join(sorted(_READ_ACTIONS))}."
        )}

    target = (target or "").strip()
    if action in _NEEDS_TARGET:
        if not target:
            return {"ok": False, "error": f"L'action '{action}' requiert un 'target' (nom/ID)."}
        if not _TARGET_RE.match(target):
            return {"ok": False, "error": (
                "Cible invalide : un nom/ID Docker n'accepte que "
                "[a-zA-Z0-9 . _ - : /] et ne commence pas par '-'."
            )}
    else:
        target = ""  # ignoré pour les actions sans cible

    try:
        tail = max(1, min(int(tail), _MAX_TAIL))
    except (TypeError, ValueError):
        tail = _DEFAULT_TAIL

    if shutil.which("docker") is None:
        return {"ok": False, "error": "Docker introuvable (binaire 'docker' absent du PATH)."}

    argv = ["docker", *_argv(action, target, tail)]
    try:
        # argv figé + shell=False + cible validée → pas d'injection de commande.
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Commande Docker expirée (>{_TIMEOUT_S}s)."}
    except OSError as exc:
        return {"ok": False, "error": f"Échec d'exécution Docker : {exc}"}

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        # Démon arrêté : message le plus courant, rendu explicite.
        if "Cannot connect to the Docker daemon" in err:
            err = "démon Docker injoignable (est-il démarré ?)."
        return {"ok": False, "error": f"Docker a échoué : {err[:_MAX_OUTPUT]}"}

    out = (proc.stdout or "").strip()
    truncated = len(out) > _MAX_OUTPUT
    if truncated:
        out = out[:_MAX_OUTPUT] + "\n[…sortie tronquée…]"
    return {"ok": True, "action": action, "target": target, "output": out, "truncated": truncated}


def format_docker_result(res: dict) -> str:
    """Rend le résultat de docker_control lisible pour le LLM."""
    if not res.get("ok"):
        return res.get("error", "Erreur Docker inconnue.")
    header = f"$ docker {res['action']}" + (f" {res['target']}" if res.get("target") else "")
    body = res.get("output") or "(aucune sortie)"
    return f"{header}\n{body}"
