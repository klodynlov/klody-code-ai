"""Accueil de session : une phrase, produite EN TÂCHE DE FOND, jamais bloquante.

Ce que ce module fait, et pourquoi il le fait comme ça — la mesure a décidé de
tout. Relevé du 2026-08-01 (gateway `:8090`, alias `brain` →
Qwen3.6-35B-A3B-8bit, `scripts/mesure_plancher_accueil.py --passes 3`) :

    plancher     6,52 s (1ᵉʳ appel du run)  →  0,13 s à chaud
    accueil      0,46 s                     →  0,37 s à chaud

**Ce n'est PAS le coût qui interdit l'accueil synchrone — c'est la VARIANCE.**
0,37 s serait invisible ; 6,52 s serait un gel du prompt au lancement. Le même
appel donne l'un ou l'autre selon que le gateway est réveillé, et rien ne permet
de le savoir à l'avance. Et 6,52 s est un plancher du cas froid : un vrai
chargement des 44 Go de `brain` coûterait davantage (voisin documenté : `coder`,
30 Go, chargé en 8,3 s).

D'où la forme retenue : un thread démon lancé le plus tôt possible, et une
attente BORNÉE au moment d'afficher. Le cas chaud arrive largement dans les
temps ; le cas froid rate l'échéance et retombe, sans un mot, sur un accueil
composé localement. Aucun des deux ne se voit.

Trois règles tenues par les tests :

1. `recuperer()` ne lève JAMAIS et n'attend jamais au-delà de son échéance.
2. L'appel ne porte AUCUN schéma d'outil. Les 69 outils pèsent ~12,5 k tokens de
   prefill, contre ~150 pour ce micro-prompt : router l'accueil par la boucle
   ReAct coûterait deux ordres de grandeur pour une phrase.
3. Le repli local n'appelle rien du tout — il lit SQLite et le disque, déjà
   payés par le démarrage.

⚠️ Effet de bord assumé : lancer la CLI réveille `brain`, modèle PARTAGÉ avec
Library Brain et KlodyAI (`pinned=True` côté gateway). En pratique il est
résident, donc l'appel est à chaud ; mais l'effet sort du périmètre de la CLI.
`GREETING_ENABLED=false` le coupe sans rien casser : le repli local devient
simplement le seul chemin.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import (
    GREETING_DEADLINE_S,
    GREETING_ENABLED,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    MEMORY_DIR,
    PROJECT_ROOT,
)

logger = logging.getLogger(__name__)

# Micro-prompt VOLONTAIREMENT identique à celui de
# `scripts/mesure_plancher_accueil.py` : c'est lui qui a été chronométré à
# 0,37 s. Le faire diverger rendrait la mesure caduque sans que rien ne rougisse.
_SYSTEME = (
    "Tu es Klody, agent de code local. Salue l'utilisateur en UNE phrase, "
    "en français, sans emoji et sans poser de question."
)

# Plafond de sortie. 60 tokens suffisent à une phrase ; au-delà le modèle
# broderait, et l'accueil doit tenir sur une ligne.
_MAX_TOKENS = 60

# Borne de l'appel lui-même, distincte de l'échéance d'affichage. Le thread est
# démon : dépasser l'échéance ne gêne personne, mais laisser l'appel traîner
# occuperait un créneau du gateway pour un résultat que plus personne ne lira.
_TIMEOUT_APPEL_S = 30.0

# Longueur maximale affichée. Aussi la borne utile si l'accueil est un jour dit à
# voix haute : `tools/voice.speak` tronque à 600 caractères.
_CAP = 240

_MARKDOWN = re.compile(r"[*_`#>]+")
_ESPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class ContexteAccueil:
    """Ce que l'accueil sait, sans rien demander au réseau."""

    projet: str
    sessions_passees: int
    faits: tuple[str, ...] = ()


def collecter_contexte() -> ContexteAccueil:
    """Photographie locale du contexte. Ne lève jamais, ne parle à personne.

    Les deux sources sont déjà payées par le démarrage : le disque (fichiers de
    session) et SQLite (mémoire long terme). Vérifier qu'elles sont *justes*
    coûterait plus cher que l'accueil lui-même — on prend ce qui est là.
    """
    projet = PROJECT_ROOT.name or str(PROJECT_ROOT)

    try:
        sessions = len(list(Path(MEMORY_DIR).glob("memory_*.json")))
    except OSError as exc:  # pragma: no cover - disque indisponible
        logger.debug("accueil : sessions illisibles (%s)", exc)
        sessions = 0

    faits: tuple[str, ...] = ()
    try:
        from agent.long_term_memory import get_long_term_memory

        entrees = get_long_term_memory().list_all()
        faits = tuple(
            str(e.get("content", "")).strip()
            for e in entrees
            if e.get("category") in ("user", "preference") and e.get("content")
        )[:3]
    except Exception as exc:
        # La mémoire long terme est un CONFORT ici : son absence ne doit pas
        # priver l'utilisateur d'un bonjour.
        logger.debug("accueil : mémoire long terme indisponible (%s)", exc)

    return ContexteAccueil(projet=projet, sessions_passees=sessions, faits=faits)


def accueil_local(contexte: ContexteAccueil) -> str:
    """Accueil composé sans le moindre appel. C'est le SOCLE, pas un pis-aller.

    Il sert dans trois cas indiscernables pour l'utilisateur : accueil désactivé,
    échéance ratée, appel en échec. Il doit donc être présentable seul.
    """
    if contexte.sessions_passees <= 1:
        return f"Bonjour — première session sur {contexte.projet}."
    return f"Bonjour — {contexte.sessions_passees} sessions sur {contexte.projet}."


def _decrire_contexte(contexte: ContexteAccueil) -> str:
    morceaux = [f"Projet : {contexte.projet}.",
                f"Sessions précédentes : {contexte.sessions_passees}."]
    if contexte.faits:
        morceaux.append("À savoir : " + " ; ".join(contexte.faits) + ".")
    return " ".join(morceaux) + " Dis bonjour."


def nettoyer(texte: str) -> str:
    """Ramène une génération libre à une ligne affichable. Jamais d'exception.

    Le modèle rend parfois du markdown, des guillemets, ou deux phrases. La
    bannière n'a de place que pour une ligne — et le jour où cette phrase sera
    dite à voix haute, `speak` exigera de toute façon du texte nu.
    """
    texte = _MARKDOWN.sub("", texte or "")
    texte = _ESPACES.sub(" ", texte).strip().strip('"«»').strip()
    if len(texte) > _CAP:
        coupe = texte[:_CAP]
        point = max(coupe.rfind(". "), coupe.rfind("! "), coupe.rfind("? "))
        texte = coupe[: point + 1] if point > _CAP // 2 else coupe.rstrip() + "…"
    return texte


class AccueilEnTacheDeFond:
    """Lance l'accueil généré en arrière-plan, retombe en silence s'il tarde.

    Usage :
        accueil = AccueilEnTacheDeFond()
        accueil.demarrer()          # le plus tôt possible dans le démarrage
        ...                         # travail de démarrage déjà prévu
        print(accueil.recuperer())  # attente bornée, jamais bloquante
    """

    def __init__(
        self,
        contexte: ContexteAccueil | None = None,
        *,
        actif: bool | None = None,
        echeance_s: float | None = None,
        client: Any = None,
    ) -> None:
        self.contexte = contexte if contexte is not None else collecter_contexte()
        self.actif = GREETING_ENABLED if actif is None else actif
        self.echeance_s = GREETING_DEADLINE_S if echeance_s is None else echeance_s
        self._client = client
        self._texte: str | None = None
        self._pret = threading.Event()
        self._thread: threading.Thread | None = None

    # -- interne ---------------------------------------------------------- #

    def _construire_client(self) -> Any:
        from openai import OpenAI

        return OpenAI(
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
            timeout=_TIMEOUT_APPEL_S,
            # Aucun réessai : un accueil raté n'a aucune valeur à la 2ᵉ tentative,
            # l'échéance d'affichage sera passée depuis longtemps.
            max_retries=0,
        )

    def _travailler(self) -> None:
        try:
            client = self._client or self._construire_client()
            reponse = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEME},
                    {"role": "user", "content": _decrire_contexte(self.contexte)},
                ],
                max_tokens=_MAX_TOKENS,
                # PAS de `tools=` : cf. l'en-tête du module.
            )
            brut = reponse.choices[0].message.content or ""
            texte = nettoyer(brut)
            self._texte = texte or None
        except Exception as exc:
            # Tout échec est SILENCIEUX côté écran : l'accueil n'est pas une
            # fonctionnalité pour laquelle il vaut la peine d'inquiéter
            # quelqu'un. La trace part dans le log.
            logger.debug("accueil généré indisponible (%s)", exc)
            self._texte = None
        finally:
            self._pret.set()

    # -- API -------------------------------------------------------------- #

    def demarrer(self) -> None:
        """Lance le thread. Sans effet si l'accueil généré est désactivé."""
        if not self.actif or self._thread is not None:
            return
        # daemon=True : si l'utilisateur quitte pendant la génération, le
        # processus ne doit pas rester accroché à une phrase de politesse.
        self._thread = threading.Thread(
            target=self._travailler, name="klody-accueil", daemon=True
        )
        self._thread.start()

    def recuperer(self) -> str:
        """Rend la phrase à afficher. Attente BORNÉE, jamais d'exception.

        Retombe sur `accueil_local` si l'accueil généré n'est pas arrivé à
        temps, a échoué, ou n'a jamais été lancé — trois cas que l'utilisateur
        n'a aucune raison de distinguer.
        """
        if self._thread is not None:
            self._pret.wait(max(0.0, self.echeance_s))
        return self._texte or accueil_local(self.contexte)

    @property
    def genere(self) -> bool:
        """Vrai si la phrase rendue vient du modèle. Pour les tests et le log."""
        return self._texte is not None
