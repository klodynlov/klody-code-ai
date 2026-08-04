"""Les deux lockfiles ne doivent jamais diverger sur un paquet commun.

Le dépôt en tient deux : `requirements.lock` (Linux, ce que la CI installe) et
`requirements-macos.lock` (la machine du banc). Le second est un **sous-ensemble
strict** du premier — il lui manque les paquets CUDA/NVIDIA que torch ne tire
que sous Linux — et sur tout le reste ils sont censés porter les mêmes versions.

Ce test existe à cause d'une panne réelle. La fraîcheur du lock macOS n'est
vérifiée que par le **nightly** (elle exige un runner macOS pour recompiler) :
une PR peut donc passer ses neuf portes au vert en laissant ce fichier périmé,
et la casse n'apparaît que le lendemain matin, loin du changement qui l'a
causée. Vécu deux fois — la seconde le 2026-08-04, où un bump de sécurité
(cryptography 50.0.0, CVE-2026-69247) n'a touché que le lock Linux et a fait
échouer le nightly sur `102c102 < cryptography==49.0.0`.

L'invariant se vérifie sans runner macOS et sans recompiler quoi que ce soit :
il suffit de comparer les deux fichiers. Il attrape exactement cet oubli, au
moment du diff plutôt qu'à la nuit suivante.

⚠️ Ce test ne REMPLACE pas le contrôle de fraîcheur du nightly : lui seul dit
si le lock macOS reflète bien ce que `pip-compile` produirait sur un Mac. Il
dit seulement que les deux fichiers racontent la même histoire.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
LOCK_LINUX = RACINE / "requirements.lock"
LOCK_MACOS = RACINE / "requirements-macos.lock"

# Un paquet épinglé en début de ligne. Les lignes de commentaire (`# via …`)
# et les options (`--index-url`) ne matchent pas.
_EPINGLE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)")


def _normaliser(nom: str) -> str:
    """PEP 503 : `pip_audit` et `pip-audit` sont le même paquet."""
    return re.sub(r"[-_.]+", "-", nom).lower()


def lire_lock(chemin: Path) -> dict[str, str]:
    epingles: dict[str, str] = {}
    for ligne in chemin.read_text().splitlines():
        m = _EPINGLE.match(ligne)
        if m:
            epingles[_normaliser(m.group(1))] = m.group(2)
    return epingles


@pytest.fixture(scope="module")
def locks() -> tuple[dict[str, str], dict[str, str]]:
    return lire_lock(LOCK_LINUX), lire_lock(LOCK_MACOS)


def test_les_deux_locks_existent():
    assert LOCK_LINUX.is_file(), f"{LOCK_LINUX} absent"
    assert LOCK_MACOS.is_file(), f"{LOCK_MACOS} absent"


def test_les_locks_sont_lisibles(locks):
    """Un parseur qui ne trouve rien rendrait tous les autres tests verts sans
    rien vérifier — le mode de défaillance dominant du dépôt."""
    linux, macos = locks
    assert len(linux) > 100, f"seulement {len(linux)} paquets lus dans le lock Linux"
    assert len(macos) > 100, f"seulement {len(macos)} paquets lus dans le lock macOS"


def test_aucune_divergence_de_version(locks):
    """Le cœur : un paquet présent des deux côtés doit y porter la MÊME version.

    C'est ce test qui aurait attrapé l'oubli du 2026-08-04, dans la PR plutôt
    qu'au nightly suivant.
    """
    linux, macos = locks
    ecarts = {
        nom: (linux[nom], macos[nom])
        for nom in set(linux) & set(macos)
        if linux[nom] != macos[nom]
    }
    assert not ecarts, (
        "versions divergentes entre requirements.lock et requirements-macos.lock "
        f"(linux, macos) : {ecarts}. Bumper un paquet exige de toucher les DEUX "
        "fichiers — la fraîcheur du lock macOS n'est vérifiée que par le nightly."
    )


def test_macos_est_un_sous_ensemble_de_linux(locks):
    """Le lock macOS ne doit rien contenir que Linux ignore.

    Il est produit avec `-c requirements.lock`, donc contraint par lui : un
    paquet exclusif à macOS signalerait que cette contrainte a sauté.
    """
    linux, macos = locks
    exclusifs = sorted(set(macos) - set(linux))
    assert not exclusifs, (
        f"paquets présents dans le lock macOS et absents du lock Linux : {exclusifs}"
    )


# Les seuls écarts légitimes : ce qui n'existe QUE sous Linux.
#   - nvidia-*, cuda-*, triton : la pile GPU que torch tire sur Linux ;
#   - jeepney + secretstorage  : le trousseau Linux (D-Bus), sans équivalent
#     macOS où keyring passe par le Keychain.
_LINUX_SEULEMENT = {"triton", "jeepney", "secretstorage"}
_PREFIXES_GPU = ("nvidia-", "cuda-")


def test_seuls_les_paquets_gpu_manquent_a_macos(locks):
    """L'écart légitime, et lui seul : ce que torch ou D-Bus ne tirent que sous
    Linux.

    Si un paquet ORDINAIRE disparaît du lock macOS, c'est une régression de
    compilation, pas une différence de plateforme.
    """
    linux, macos = locks
    absents = sorted(set(linux) - set(macos))
    inattendus = [
        n for n in absents
        if not (n.startswith(_PREFIXES_GPU) or n in _LINUX_SEULEMENT)
    ]
    assert not inattendus, (
        "paquets absents du lock macOS sans être liés au GPU ou à Linux : "
        f"{inattendus}"
    )
