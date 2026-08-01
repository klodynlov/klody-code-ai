"""Accueil de session INTERACTIF, produit en tâche de fond.

Un accueil utile ne dit pas seulement bonjour : il rappelle où on en était et
propose des suites concrètes, sélectionnables au clavier. C'est aussi le seul
moment où l'agent peut orienter quelqu'un qui ne sait pas quoi lui demander.

Trois morceaux, dans cet ordre à l'écran :

    salutation    « Bonjour — 12 sessions sur klody-code-ai. »
    rappel        « Hier, tu travaillais sur : mesurer le coût du garde doc. »
    propositions  1..3 pistes, tapables par leur numéro

**Le socle ne dépend d'aucun appel LLM.** `accueil_local()` compose les trois
morceaux à partir du disque et de SQLite — déjà payés par le démarrage. Le
modèle, quand il répond à temps, ne fait que mieux les formuler. Un accueil sans
gateway reste donc un accueil complet, propositions comprises : c'est ce qui
permet de le rendre inconditionnel.

**Pourquoi en tâche de fond** — mesuré le 2026-08-01 (gateway `:8090`, `brain` →
Qwen3.6-35B-A3B-8bit, relevé dans `bench/results/reference_2026-08-01_plancher_accueil.md`) :

    à chaud   0,37 s        →  invisible
    à froid   6,52 s        →  gel du prompt au lancement

Ce n'est pas le coût qui interdit le synchrone, c'est la VARIANCE : le même
appel donne l'un ou l'autre selon que le gateway est réveillé, sans moyen de le
savoir à l'avance. D'où un thread démon et une attente bornée à l'affichage.

Règles tenues par les tests :

1. `recuperer()` ne lève JAMAIS et n'attend jamais au-delà de son échéance.
2. L'appel ne porte AUCUN schéma d'outil (~150 tokens contre ~12,3 k pour les
   69 outils, soit ~0,4 s contre ~4,6 s à cache froid).
3. Le socle rend toujours au moins une proposition — sinon l'accueil
   « interactif » ne le serait plus dès que le modèle manque à l'appel.

⚠️ Effet de bord assumé : lancer la CLI réveille `brain`, modèle PARTAGÉ avec
Library Brain et KlodyAI (`pinned` côté gateway). `GREETING_ENABLED=false` coupe
l'appel ; le socle local, propositions comprises, reste.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
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

# Micro-prompt. Le format de réponse est LIGNE À LIGNE, pas du JSON : un accueil
# raté doit dégrader vers le socle, pas déclencher la validation + retry borné de
# `docs/json-constraint-policy.md`. Personne n'attend un accueil deux fois.
_SYSTEME = (
    "Tu es Klody, agent de code local. Accueille l'utilisateur qui ouvre une "
    "session, en français, sans emoji.\n"
    "Réponds EXACTEMENT dans ce format, rien d'autre :\n"
    "BONJOUR: <une phrase de salutation>\n"
    "RAPPEL: <une phrase sur la session précédente, ou vide s'il n'y en a pas>\n"
    "- <proposition d'action concrète>\n"
    "- <autre proposition>\n"
    "- <autre proposition>\n"
    "Les propositions sont des actions que TU peux faire maintenant, formulées "
    "à l'infinitif, courtes (moins de 60 caractères)."
)

_MAX_TOKENS = 160
_TIMEOUT_APPEL_S = 30.0
_CAP_LIGNE = 200
_MAX_PROPOSITIONS = 3

_MARKDOWN = re.compile(r"[*_`#>]+")
_ESPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class ContexteAccueil:
    """Ce que l'accueil sait, sans rien demander au réseau."""

    projet: str
    sessions_passees: int
    faits: tuple[str, ...] = ()
    derniere_tache: str = ""
    derniere_date: str = ""


@dataclass(frozen=True)
class Accueil:
    """Le rendu prêt à afficher — et à dire à voix haute."""

    salutation: str
    rappel: str = ""
    propositions: tuple[str, ...] = field(default_factory=tuple)

    def en_texte(self) -> str:
        """Version linéaire, pour la synthèse vocale et les logs.

        Les propositions sont numérotées à l'oral aussi : quelqu'un qui n'a que
        le son doit pouvoir répondre « 2 » comme les autres.
        """
        morceaux = [self.salutation]
        if self.rappel:
            morceaux.append(self.rappel)
        for i, proposition in enumerate(self.propositions, 1):
            morceaux.append(f"{i}. {proposition}")
        return " ".join(morceaux)


def _date_relative(horodatage: float, maintenant: float | None = None) -> str:
    maintenant = time.time() if maintenant is None else maintenant
    jours = int((maintenant - horodatage) // 86400)
    if jours <= 0:
        return "plus tôt aujourd'hui"
    if jours == 1:
        return "hier"
    if jours < 7:
        return f"il y a {jours} jours"
    return "la dernière fois"


def _derniere_tache(memory_dir: Path) -> tuple[str, str]:
    """Première demande de la session précédente, et sa date relative.

    C'est le meilleur résumé disponible pour zéro token : le premier message
    utilisateur d'une session dit ce qu'on venait y faire, alors que le dernier
    dit souvent « merci » ou « relance les tests ».
    """
    try:
        fichiers = sorted(
            memory_dir.glob("memory_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError as exc:  # pragma: no cover - disque indisponible
        logger.debug("accueil : sessions illisibles (%s)", exc)
        return "", ""
    if not fichiers:
        return "", ""

    dernier = fichiers[0]
    try:
        donnees = json.loads(dernier.read_text(encoding="utf-8"))
        for message in donnees.get("messages", []):
            if message.get("role") == "user" and message.get("content"):
                tache = _une_ligne(str(message["content"]))
                return tache, _date_relative(dernier.stat().st_mtime)
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("accueil : session précédente illisible (%s)", exc)
    return "", ""


def collecter_contexte() -> ContexteAccueil:
    """Photographie locale du contexte. Ne lève jamais, ne parle à personne."""
    projet = PROJECT_ROOT.name or str(PROJECT_ROOT)
    dossier = Path(MEMORY_DIR)

    try:
        sessions = len(list(dossier.glob("memory_*.json")))
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
        # La mémoire long terme est un CONFORT : son absence ne doit pas priver
        # l'utilisateur d'un bonjour.
        logger.debug("accueil : mémoire long terme indisponible (%s)", exc)

    tache, date = _derniere_tache(dossier)
    return ContexteAccueil(
        projet=projet,
        sessions_passees=sessions,
        faits=faits,
        derniere_tache=tache,
        derniere_date=date,
    )


def accueil_local(contexte: ContexteAccueil) -> Accueil:
    """Accueil composé sans le moindre appel. C'est le SOCLE, pas un pis-aller.

    Il sert dans trois cas indiscernables pour l'utilisateur : accueil désactivé,
    échéance ratée, appel en échec. Il doit donc être présentable seul —
    propositions comprises, sinon « interactif » deviendrait conditionnel à la
    disponibilité du gateway.
    """
    if contexte.sessions_passees <= 1:
        salutation = f"Bonjour — première session sur {contexte.projet}."
    else:
        salutation = (
            f"Bonjour — {contexte.sessions_passees} sessions sur {contexte.projet}."
        )

    rappel = ""
    propositions: list[str] = []
    if contexte.derniere_tache:
        apercu = _tronquer(contexte.derniere_tache, 90)
        quand = contexte.derniere_date or "la dernière fois"
        rappel = f"{quand.capitalize()}, tu me demandais : « {apercu} »"
        propositions.append("Reprendre là où on s'est arrêté")

    propositions.append("Faire le point sur les changements en cours")
    propositions.append("Lancer les tests du projet")
    return Accueil(
        salutation=salutation,
        rappel=rappel,
        propositions=tuple(propositions[:_MAX_PROPOSITIONS]),
    )


def _decrire_contexte(contexte: ContexteAccueil) -> str:
    morceaux = [
        f"Projet : {contexte.projet}.",
        f"Sessions précédentes : {contexte.sessions_passees}.",
    ]
    if contexte.derniere_tache:
        morceaux.append(
            f"{(contexte.derniere_date or 'La dernière fois').capitalize()}, "
            f"la demande était : {_tronquer(contexte.derniere_tache, 200)}."
        )
    if contexte.faits:
        morceaux.append("À savoir : " + " ; ".join(contexte.faits) + ".")
    return " ".join(morceaux)


def _une_ligne(texte: str) -> str:
    texte = _MARKDOWN.sub("", texte or "")
    return _ESPACES.sub(" ", texte).strip().strip('"«»').strip()


def _tronquer(texte: str, cap: int) -> str:
    texte = _une_ligne(texte)
    return texte if len(texte) <= cap else texte[: cap - 1].rstrip() + "…"


def nettoyer(texte: str) -> str:
    """Ramène une génération libre à une ligne affichable. Jamais d'exception."""
    return _tronquer(texte, _CAP_LIGNE)


def analyser(brut: str, socle: Accueil) -> Accueil:
    """Lit le format ligne à ligne du modèle, champ par champ, sans jamais lever.

    Le repli est PARTIEL et c'est le point : un modèle qui donne une belle
    salutation mais oublie les propositions ne doit pas faire perdre celles du
    socle. Un format global (JSON) aurait imposé le tout ou rien.
    """
    salutation = ""
    rappel = ""
    propositions: list[str] = []

    for ligne in (brut or "").splitlines():
        depouillee = ligne.strip()
        if not depouillee:
            continue
        haut = depouillee.upper()
        if haut.startswith("BONJOUR:"):
            salutation = nettoyer(depouillee.split(":", 1)[1])
        elif haut.startswith("RAPPEL:"):
            rappel = nettoyer(depouillee.split(":", 1)[1])
        elif depouillee.startswith(("-", "•", "*")):
            proposition = nettoyer(depouillee.lstrip("-•* ").strip())
            if proposition:
                propositions.append(proposition)

    return Accueil(
        salutation=salutation or socle.salutation,
        rappel=rappel or socle.rappel,
        propositions=tuple(propositions[:_MAX_PROPOSITIONS]) or socle.propositions,
    )


class AccueilEnTacheDeFond:
    """Lance l'accueil généré en arrière-plan, retombe en silence s'il tarde.

    Usage :
        accueil = AccueilEnTacheDeFond()
        accueil.demarrer()          # le plus tôt possible dans le démarrage
        ...                         # travail de démarrage déjà prévu
        rendu = accueil.recuperer() # attente bornée, jamais bloquante
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
        self._accueil: Accueil | None = None
        self._pret = threading.Event()
        self._thread: threading.Thread | None = None

    # -- interne ---------------------------------------------------------- #

    def _construire_client(self) -> Any:
        from openai import OpenAI

        return OpenAI(
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
            timeout=_TIMEOUT_APPEL_S,
            # Aucun réessai : l'échéance d'affichage sera passée depuis longtemps.
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
            self._accueil = analyser(brut, accueil_local(self.contexte))
        except Exception as exc:
            # Échec SILENCIEUX à l'écran : un accueil n'est pas une raison
            # d'inquiéter quelqu'un. La trace part dans le log.
            logger.debug("accueil généré indisponible (%s)", exc)
            self._accueil = None
        finally:
            self._pret.set()

    # -- API -------------------------------------------------------------- #

    def demarrer(self) -> None:
        """Lance le thread. Sans effet si l'accueil généré est désactivé."""
        if not self.actif or self._thread is not None:
            return
        # daemon=True : quitter pendant la génération ne doit pas retenir le
        # processus pour une phrase de politesse.
        self._thread = threading.Thread(
            target=self._travailler, name="klody-accueil", daemon=True
        )
        self._thread.start()

    def recuperer(self) -> Accueil:
        """Rend l'accueil à afficher. Attente BORNÉE, jamais d'exception."""
        if self._thread is not None:
            self._pret.wait(max(0.0, self.echeance_s))
        return self._accueil or accueil_local(self.contexte)

    @property
    def genere(self) -> bool:
        """Vrai si le rendu vient du modèle. Pour les tests et le log."""
        return self._accueil is not None
