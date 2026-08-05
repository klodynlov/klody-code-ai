"""Péremption du code de dépendances sous un process long.

Vécu le 2026-08-05 : la PR #206 (`f962c1e`, mergée la veille à 05:40) a bumpé
`openai` 2.41.1 → 2.53.0 dans `requirements.lock`. Le service `com.klody.api`
tournait depuis le 2026-08-03 01:36 et n'a jamais été redémarré — pip a donc
réécrit `site-packages/openai/` SOUS le process vivant.

`openai._utils` est resté figé en 2.41.1 dans `sys.modules`, donc sans
`path_template`, tandis que `openai/resources/*` — chargé PARESSEUSEMENT via
`openai/_utils/_resources_proxy.py` — a été lu neuf sur le disque. Au premier
appel LLM suivant, l'app a rendu sur CHAQUE requête :

    cannot import name 'path_template' from 'openai._utils'

~23 h après le merge, sans la moindre corrélation visible avec la mise à jour,
et avec un venv parfaitement SAIN : l'import dans un interpréteur frais passait,
ce qui a envoyé le diagnostic chercher du côté des versions et du venv. Le
remède était `launchctl kickstart -k gui/$(id -u)/com.klody.api`, et rien — ni
sonde, ni log, ni statut — ne le disait.

Ce module rend l'écart OBSERVABLE, par deux instruments indépendants :

  * `etat_process()` — vu de l'intérieur. L'empreinte de `site-packages` est
    prise à l'import de CE module (donc au démarrage du service, puisqu'il est
    importé au boot par `api/server.py`) puis comparée au disque à la demande.
    Sert `/health` et `/api/status`.
  * `examiner_services()` — vu de l'extérieur, et pour TOUS les `com.klody.*` :
    la date de démarrage de chaque process contre la date de la dernière
    écriture dans `site-packages`. Les serveurs MCP n'ont aucun `/health` ;
    c'est le seul angle qui les couvre.

Les deux peuvent se contredire, et c'est voulu : le premier est une
approximation rapide (il date un import, pas un `fork`), le second est exact
mais coûte deux `subprocess` par service.

⚠️ Trois verdicts, jamais deux — `A_JOUR`, `PERIMEES`, et `NON_JUGE` quand
`site-packages` est introuvable ou qu'un service n'offre aucun processus à
dater. Un garde incapable de dire « je n'ai pas pu juger » fait passer « je n'ai
rien regardé » pour « tout va bien ».
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sysconfig
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

A_JOUR = "a_jour"
PERIMEES = "perimees"
NON_JUGE = "non_juge"

# Timeout des sondes externes. `launchctl print` et `ps` répondent en quelques
# ms ; au-delà, mieux vaut un « non jugé » qu'un diagnostic qui se bloque.
_TIMEOUT_SONDE_S = 10.0


# ── Empreinte de site-packages ────────────────────────────────────────────────

@dataclass(frozen=True)
class Empreinte:
    """L'état de `site-packages` à un instant donné.

    Deux axes, parce qu'un seul se fait contourner :

      * `horodatage` — la mtime la plus récente des dossiers `*.dist-info` ;
      * `nb_paquets` — leur nombre, seul témoin d'une DÉSINSTALLATION sèche,
        qui n'écrit rien et ne rajeunit donc aucune mtime.

    ⚠️ On ne date QUE les `*.dist-info`, jamais le dossier `site-packages`
    lui-même. La première compilation d'un module de premier niveau y crée
    `__pycache__/`, ce qui rajeunit le dossier sans qu'aucune dépendance n'ait
    bougé : le dater produirait un faux positif après le démarrage, sur un venv
    parfaitement stable. pip, lui, réécrit un `*.dist-info` à chaque
    (dés)installation — mesuré sur le venv du dépôt, `openai-2.53.0.dist-info`
    porte bien l'heure de l'installation du 2026-08-04 05:27:36.
    """

    horodatage: float | None
    nb_paquets: int
    racines: tuple[str, ...]

    @property
    def mesurable(self) -> bool:
        """Faux quand rien n'a pu être daté — le cas « non jugé »."""
        return self.horodatage is not None

    def resume(self) -> dict[str, object]:
        """Forme sérialisable, pour `/health`, `/api/status` et les logs."""
        return {
            "horodatage": self.horodatage,
            "iso": iso(self.horodatage),
            "nb_paquets": self.nb_paquets,
            "racines": list(self.racines),
        }


def iso(horodatage: float | None) -> str | None:
    """Epoch → « 2026-08-04T05:27:36 » local, ou None si rien à dater."""
    if horodatage is None:
        return None
    return datetime.fromtimestamp(horodatage).isoformat(timespec="seconds")


def racines_site_packages() -> tuple[Path, ...]:
    """Les dossiers `site-packages` de l'interpréteur courant, dédoublonnés.

    `purelib` et `platlib` pointent le même dossier dans un venv classique,
    mais pas sur toutes les distributions — on prend les deux et on dédoublonne
    plutôt que de parier.
    """
    trouves: list[Path] = []
    chemins = sysconfig.get_paths()
    for cle in ("purelib", "platlib"):
        brut = chemins.get(cle)
        if not brut:
            continue
        chemin = Path(brut)
        if chemin in trouves or not chemin.is_dir():
            continue
        trouves.append(chemin)
    return tuple(trouves)


def empreinte_disque(racines: Iterable[Path] | None = None) -> Empreinte:
    """Empreinte des dépendances telles qu'elles sont MAINTENANT sur le disque.

    Coût mesuré sur le venv du dépôt (278 `*.dist-info`) : **0,46 ms**. C'est
    ce qui autorise l'appel synchrone dans `/health`, sondé toutes les 60 s par
    `scripts/api-watchdog.sh`, sans cache à maintenir.
    """
    dossiers = tuple(racines) if racines is not None else racines_site_packages()
    horodatage: float | None = None
    nb_paquets = 0
    for racine in dossiers:
        try:
            distributions = list(racine.glob("*.dist-info"))
        except OSError:  # pragma: no cover - racine disparue entre deux appels
            continue
        for dist in distributions:
            try:
                mtime = dist.stat().st_mtime
            except OSError:  # pragma: no cover - course avec une désinstallation
                continue
            nb_paquets += 1
            if horodatage is None or mtime > horodatage:
                horodatage = mtime
    return Empreinte(horodatage, nb_paquets, tuple(str(d) for d in dossiers))


# Prise à l'IMPORT, et c'est le point sensible de tout le module : ce module
# doit être importé au démarrage du service (il l'est par `api/server.py`),
# sinon l'empreinte de référence daterait du premier appel plutôt que du boot.
EMPREINTE_DEMARRAGE = empreinte_disque()


# ── Vu de l'intérieur : le process courant ────────────────────────────────────

def commande_kickstart(label_service: str, uid: int | None = None) -> str:
    """La commande qui a effectivement réparé l'incident du 2026-08-05."""
    identifiant = os.getuid() if uid is None else uid
    return f"launchctl kickstart -k gui/{identifiant}/{label_service}"


def etat_process(
    *,
    demarrage: Empreinte | None = None,
    disque: Empreinte | None = None,
    label_service: str | None = None,
) -> dict[str, object]:
    """Le process courant sert-il encore le code de dépendances du disque ?

    Compare deux EMPREINTES, pas deux dates : une comparaison de valeurs est
    insensible à l'horloge, et attrape aussi bien un downgrade (`pip install
    openai==2.41.1` remet une mtime plus ancienne) qu'un bump.
    """
    reference = EMPREINTE_DEMARRAGE if demarrage is None else demarrage
    courante = empreinte_disque() if disque is None else disque

    if not reference.mesurable or not courante.mesurable:
        statut = NON_JUGE
        raison = (
            "aucun dossier `site-packages` datable — péremption des dépendances "
            "NON JUGÉE (ce n'est pas un feu vert)."
        )
    elif (reference.horodatage, reference.nb_paquets) == (
        courante.horodatage,
        courante.nb_paquets,
    ):
        statut = A_JOUR
        raison = (
            f"site-packages inchangé depuis le démarrage "
            f"({courante.nb_paquets} paquets, dernière écriture {iso(courante.horodatage)})."
        )
    else:
        statut = PERIMEES
        raison = (
            "site-packages a été réécrit SOUS ce process : dernière écriture "
            f"{iso(courante.horodatage)} pour {courante.nb_paquets} paquets, contre "
            f"{iso(reference.horodatage)} pour {reference.nb_paquets} au démarrage. "
            "Les modules déjà chargés sont figés dans l'ancienne version, ceux qui "
            "seront importés paresseusement seront lus dans la nouvelle — c'est "
            "l'incident du 2026-08-05 (« cannot import name 'path_template' from "
            "'openai._utils' »). Seul un redémarrage répare."
        )

    remede = None
    if statut == PERIMEES:
        remede = (
            commande_kickstart(label_service)
            if label_service
            else "redémarrer le process qui sert ce code"
        )

    return {
        "statut": statut,
        "raison": raison,
        "remede": remede,
        "demarrage": reference.resume(),
        "disque": courante.resume(),
    }


# ── Vu de l'extérieur : tous les services com.klody.* ─────────────────────────

@dataclass(frozen=True)
class ServiceExamine:
    """Verdict de péremption pour un LaunchAgent."""

    label: str
    statut: str
    raison: str
    pid: int | None = None
    demarrage: float | None = None
    remede: str | None = None


_RE_PID = re.compile(r"^\s*pid\s*=\s*(\d+)", re.MULTILINE)

# `ps -o lstart=` rend « Wed Aug  5 04:59:35 2026 ».
_RE_LSTART = re.compile(
    r"^\s*[A-Za-z]{3}\s+([A-Za-z]{3})\s+(\d{1,2})\s+"
    r"(\d{2}):(\d{2}):(\d{2})\s+(\d{4})\s*$"
)
_MOIS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def epoch_depuis_lstart(texte: str) -> float | None:
    """Convertit une date `ps -o lstart=` en epoch, ou None si illisible.

    ⚠️ Volontairement PAS `strptime` : ses noms de mois suivent la locale du
    process, alors que `ps` écrit en anglais. Sous `LC_TIME=fr_FR`, `strptime`
    échouerait et transformerait un verdict en « non jugé » silencieux —
    exactement le mode de panne que ce module existe pour supprimer.
    """
    m = _RE_LSTART.match(texte.strip())
    if not m:
        return None
    mois = _MOIS.get(m.group(1))
    if mois is None:
        return None
    try:
        return datetime(
            int(m.group(6)), mois, int(m.group(2)),
            int(m.group(3)), int(m.group(4)), int(m.group(5)),
        ).timestamp()
    except ValueError:  # pragma: no cover - date impossible (30 février…)
        return None


def _lancer(argv: Sequence[str]) -> tuple[int, str]:
    """Exécute une sonde externe. Tout échec devient « pas de sortie »."""
    try:
        # argv en liste, binaires fixes, aucune donnée utilisateur : pas de shell.
        p = subprocess.run(  # nosec
            list(argv), capture_output=True, text=True,
            timeout=_TIMEOUT_SONDE_S, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("sonde %s indisponible : %s", argv[0] if argv else "?", exc)
        return 127, ""
    return p.returncode, p.stdout


def est_periodique(plist: Path) -> bool:
    """Un job `StartInterval` ré-exécute son programme à chaque tick.

    Il repart donc du disque à chaque fois et ne peut PAS servir de
    dépendances périmées — l'écarter est un fait, pas une commodité.
    """
    try:
        return "<key>StartInterval</key>" in plist.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def services_versionnes(racine_depot: Path | None = None) -> list[tuple[str, Path]]:
    """Les LaunchAgents versionnés dans `launchagents/`, triés par label."""
    racine = Path(__file__).resolve().parent.parent if racine_depot is None else racine_depot
    return [(p.stem, p) for p in sorted((racine / "launchagents").glob("*.plist"))]


def examiner_services(
    services: Iterable[tuple[str, Path]],
    *,
    empreinte: Empreinte | None = None,
    lancer: Callable[[Sequence[str]], tuple[int, str]] | None = None,
    uid: int | None = None,
) -> list[ServiceExamine]:
    """Verdict de péremption pour chaque service, du dehors.

    Ici la comparaison est temporelle et non plus une égalité d'empreintes :
    on ne peut pas connaître l'empreinte qu'un AUTRE process avait à son
    démarrage. Un service démarré avant la dernière écriture dans
    `site-packages` sert donc du code de dépendances potentiellement périmé.
    """
    disque = empreinte_disque() if empreinte is None else empreinte
    executer = _lancer if lancer is None else lancer
    identifiant = os.getuid() if uid is None else uid

    resultats: list[ServiceExamine] = []
    for label, plist in services:
        rc, sortie = executer(["launchctl", "print", f"gui/{identifiant}/{label}"])
        if rc != 0:
            resultats.append(ServiceExamine(
                label, NON_JUGE,
                "absent de launchd — rien à dater "
                "(`install-launchagents.sh --check` dit s'il devrait y être).",
            ))
            continue

        if est_periodique(plist):
            resultats.append(ServiceExamine(
                label, A_JOUR,
                "périodique (`StartInterval`) — ré-exécuté à chaque tick, "
                "il repart toujours du disque.",
            ))
            continue

        m = _RE_PID.search(sortie)
        if not m:
            resultats.append(ServiceExamine(
                label, NON_JUGE,
                "chargé mais sans processus — aucune date de démarrage à comparer.",
            ))
            continue
        pid = int(m.group(1))

        _rc_ps, brut = executer(["ps", "-o", "lstart=", "-p", str(pid)])
        debut = epoch_depuis_lstart(brut)
        if debut is None:
            resultats.append(ServiceExamine(
                label, NON_JUGE,
                f"date de démarrage du pid {pid} illisible — non jugé.", pid=pid,
            ))
            continue

        reecriture = disque.horodatage
        if reecriture is None:
            resultats.append(ServiceExamine(
                label, NON_JUGE,
                "aucun `site-packages` datable — non jugé.", pid=pid, demarrage=debut,
            ))
            continue

        if debut < reecriture:
            resultats.append(ServiceExamine(
                label, PERIMEES,
                f"démarré le {iso(debut)}, site-packages réécrit depuis "
                f"({iso(disque.horodatage)}).",
                pid=pid, demarrage=debut,
                remede=commande_kickstart(label, identifiant),
            ))
        else:
            resultats.append(ServiceExamine(
                label, A_JOUR,
                f"démarré le {iso(debut)}, après la dernière écriture dans "
                f"site-packages ({iso(disque.horodatage)}).",
                pid=pid, demarrage=debut,
            ))
    return resultats


def verdict(examens: Iterable[ServiceExamine]) -> str:
    """Agrège des verdicts de service en un verdict unique.

    ⚠️ `NON_JUGE` n'est PAS absorbé par `A_JOUR` : une liste où rien n'a pu
    être daté doit rendre « non jugé », jamais « tout va bien ». C'est la
    distinction que ce dépôt paie cher quand elle manque.
    """
    liste = list(examens)
    if any(e.statut == PERIMEES for e in liste):
        return PERIMEES
    if any(e.statut == A_JOUR for e in liste):
        return A_JOUR
    return NON_JUGE
