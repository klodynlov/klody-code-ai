"""Introspection Kubernetes en LECTURE SEULE — outil runtime (Roadmap v2 #10).

Permet à Klody d'inspecter un cluster via `kubectl` sans jamais le MUTER : aucune
opération `apply`/`create`/`delete`/`edit`/`scale`/`patch`/`exec`/`rollout` — celles-ci
relèvent d'un incrément sécurisé dédié. Même patron de sécurité que `docker_control` :

- `subprocess.run([...], shell=False)` — jamais de shell, jamais d'f-string.
- Les verbes kubectl sont **hardcodés** par action (jamais l'entrée utilisateur).
- Chaque entrée user (resource / name / namespace / container) est validée par un
  charset strict et ne peut pas commencer par `-` → aucun flag injectable (`-o`,
  `--kubeconfig`…) ni métacaractère shell.
- Le format de sortie (`-o wide`) est fixe (pas de gadget `-o jsonpath=…` injecté).
- `--request-timeout` borne l'attente API ; sortie plafonnée ; `kubectl` absent géré.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess

logger = logging.getLogger(__name__)

_TIMEOUT_S = 15
_REQUEST_TIMEOUT = "10s"
_MAX_OUTPUT = 20_000
_MAX_TAIL = 500
_DEFAULT_TAIL = 200

# Type de ressource k8s : minuscules, chiffres, '.', '-' (groupes/CRD). Ex: pods,
# deployments, svc, pods.v1, ingresses.networking.k8s.io.
_RESOURCE_RE = re.compile(r"^[a-z][a-z0-9.\-]{0,62}$")
# Nom de ressource / conteneur (RFC1123-ish) : minuscules, chiffres, '-', '.'.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{0,252}$")
# Namespace (label RFC1123).
_NS_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}$")

_READ_ACTIONS = frozenset({
    "get", "describe", "logs", "top", "version", "cluster-info", "api-resources",
})
_NEEDS_RESOURCE = frozenset({"get", "describe", "top"})
_NEEDS_NAME = frozenset({"describe", "logs"})
_TOP_RESOURCES = frozenset({"pods", "nodes", "pod", "node"})


class KubectlToolError(Exception):
    """Entrée invalide."""


def _ns_args(namespace: str) -> list[str]:
    if not namespace:
        return []
    if namespace == "all":
        return ["--all-namespaces"]
    return ["-n", namespace]


def _argv(action: str, resource: str, name: str, namespace: str,
          container: str, tail: int) -> list[str]:
    """argv kubectl figé pour une action lecture seule (entrées déjà validées)."""
    common = [f"--request-timeout={_REQUEST_TIMEOUT}"]
    ns = _ns_args(namespace)
    if action == "version":
        return ["version", *common]
    if action == "cluster-info":
        return ["cluster-info", *common]
    if action == "api-resources":
        return ["api-resources", *common]
    if action == "get":
        parts = ["get", resource, *([name] if name else []), *ns, "-o", "wide", *common]
        return parts
    if action == "describe":
        return ["describe", resource, name, *ns, *common]
    if action == "logs":
        parts = ["logs", name, *ns, "--tail", str(tail)]
        if container:
            parts += ["-c", container]
        return [*parts, *common]
    if action == "top":
        return ["top", resource, *ns, *common]
    raise KubectlToolError(f"action inconnue : '{action}'")  # pragma: no cover


def kubectl_control(
    action: str,
    resource: str = "",
    name: str = "",
    namespace: str = "",
    container: str = "",
    tail: int = _DEFAULT_TAIL,
) -> dict:
    """Exécute une commande kubectl LECTURE SEULE. Retourne un dict structuré."""
    action = (action or "").strip().lower()
    if action not in _READ_ACTIONS:
        return {"ok": False, "error": (
            f"Action '{action}' non supportée. Actions lecture seule : "
            f"{', '.join(sorted(_READ_ACTIONS))}."
        )}

    resource = (resource or "").strip()
    name = (name or "").strip()
    namespace = (namespace or "").strip()
    container = (container or "").strip()

    # --- Validation des entrées (anti-injection) --- #
    if action in _NEEDS_RESOURCE:
        if not resource:
            return {"ok": False, "error": f"L'action '{action}' requiert une 'resource'."}
        if not _RESOURCE_RE.match(resource):
            return {"ok": False, "error": "Type de ressource invalide (attendu ex: pods, deployments, svc)."}
        if action == "top" and resource.lower() not in _TOP_RESOURCES:
            return {"ok": False, "error": "'top' n'accepte que 'pods' ou 'nodes'."}
    else:
        resource = ""

    if action in _NEEDS_NAME and not name:
        return {"ok": False, "error": f"L'action '{action}' requiert un 'name'."}
    if name and not _NAME_RE.match(name):
        return {"ok": False, "error": "Nom invalide (minuscules, chiffres, '-', '.', ne commence pas par '-')."}
    if action not in ("get", "describe", "logs"):
        name = ""

    if namespace and namespace != "all" and not _NS_RE.match(namespace):
        return {"ok": False, "error": "Namespace invalide (label RFC1123)."}
    if container and not _NAME_RE.match(container):
        return {"ok": False, "error": "Nom de conteneur invalide."}
    if action != "logs":
        container = ""

    try:
        tail = max(1, min(int(tail), _MAX_TAIL))
    except (TypeError, ValueError):
        tail = _DEFAULT_TAIL

    if shutil.which("kubectl") is None:
        return {"ok": False, "error": "kubectl introuvable (binaire absent du PATH)."}

    argv = ["kubectl", *_argv(action, resource, name, namespace, container, tail)]
    try:
        # argv figé + shell=False + entrées validées → pas d'injection de commande.
        proc = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=_TIMEOUT_S, shell=False, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Commande kubectl expirée (>{_TIMEOUT_S}s)."}
    except OSError as exc:
        return {"ok": False, "error": f"Échec d'exécution kubectl : {exc}"}

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        low = err.lower()
        if any(s in low for s in (
            "connection to the server", "refused", "unable to connect",
            "no configuration", "kubeconfig", "couldn't get",
        )):
            err = "cluster injoignable ou kubeconfig absent."
        return {"ok": False, "error": f"kubectl a échoué : {err[:_MAX_OUTPUT]}"}

    out = (proc.stdout or "").strip()
    truncated = len(out) > _MAX_OUTPUT
    if truncated:
        out = out[:_MAX_OUTPUT] + "\n[…sortie tronquée…]"
    return {"ok": True, "action": action, "output": out, "truncated": truncated}


def format_kubectl_result(res: dict) -> str:
    """Rend le résultat de kubectl_control lisible pour le LLM."""
    if not res.get("ok"):
        return res.get("error", "Erreur kubectl inconnue.")
    body = res.get("output") or "(aucune sortie)"
    return f"$ kubectl {res['action']}\n{body}"
