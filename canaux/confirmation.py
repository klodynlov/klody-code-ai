"""Actions confirmées par « oui » sur iMessage — Klody propose, l'utilisateur valide.

Demandé le 2026-11-26 (chantier « Actions avec ton oui ») : créer un rappel ou
un événement, répondre à un mail, lancer un raccourci, depuis l'iPhone. Le canal
iMessage était en LECTURE seule (`canaux/imessage.py`, liste blanche) ; ces
actions y entrent, mais JAMAIS par la seule décision du modèle :

1. le modèle appelle l'outil → la garde du canal ne l'exécute PAS : elle valide
   les arguments et met l'action EN ATTENTE ;
2. le canal envoie un récapitulatif construit par le CODE à partir des arguments
   exacts (destinataire, objet et corps entier d'un mail ; titre, date, liste
   d'un rappel…), jamais par le modèle ;
3. « oui » dans les `DUREE_S` (10 min) → exécution de CES arguments-là, sans
   repasser par le modèle ; « non », une autre proposition ou l'expiration →
   rien n'est fait.

Pourquoi le récapitulatif est le garde-fou : un mail piégé lu par Klody peut
pousser le modèle à proposer « réponds à x@y avec … ». Le « oui » ne vaut que
si l'utilisateur voit exactement ce qui part. D'où les plafonds : un corps de
mail que l'iPhone n'affiche pas en entier n'est pas proposable.

Une seule action en attente à la fois, une seule proposition par tour : « oui »
porte toujours sur le DERNIER récapitulatif reçu.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger("klody.confirmation")

DUREE_S = 600.0  # 10 min pour répondre « oui »
OUBLI_EXPIREE_S = 3600.0  # « oui » tardif : on explique pendant 1 h, puis on oublie
CORPS_MAX = 1000  # corps de mail entier dans le récapitulatif
ENTREE_MAX = 500  # entrée texte d'un raccourci

OUI = frozenset({"oui", "ok", "okay", "ouais", "vas-y", "vas y", "go", "confirme",
                 "je confirme", "yes", "c'est bon", "valide", "👍"})
NON = frozenset({"non", "annule", "annuler", "stop", "no", "laisse tomber", "pas maintenant"})

_COURRIEL = re.compile(r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[A-Za-z]{2,}$")

GMAIL_ENVOI = "mcp__gmail__send_email"
GMAIL_BROUILLON = "mcp__gmail__create_draft"


@dataclass(frozen=True)
class Action:
    nom: str  # outil d'origine (journal d'audit)
    resume: str  # récapitulatif exact, construit par le code
    executer: Callable[[], str]


Preparateur = Callable[[dict], "Action | str"]


def mot_confirmation(texte: str, prefixe: re.Pattern[str] | None = None) -> str | None:
    """« oui » / « non » si le message n'est QUE ça (préfixe « Klody » toléré)."""
    t = texte.strip()
    if prefixe is not None:
        m = prefixe.match(t)
        if m:
            t = t[m.end():]
    t = re.sub(r"[\s.!?,…]+$", "", t.strip().lower()).replace("’", "'")
    if t in OUI:
        return "oui"
    if t in NON:
        return "non"
    return None


def _courriels(v: object, requis: bool) -> list[str] | str:
    brut = [x.strip() for x in str(v or "").split(",") if x.strip()]
    if requis and not brut:
        return "destinataire manquant"
    for x in brut:
        if not _COURRIEL.match(x):
            return f"adresse invalide « {x[:60]} »"
    if len(brut) > 5:
        return "plus de 5 adresses : à faire depuis l'app"
    return brut


def preparer_mail(outil: str, appeler: Callable[[str, dict], dict]) -> Preparateur:
    """Envoi (``send_email``) ou brouillon (``create_draft``) Gmail."""
    envoi = outil == GMAIL_ENVOI
    feuille = outil.rsplit("__", 1)[-1]

    def preparer(args: dict) -> Action | str:
        dest = _courriels(args.get("to"), True)
        cc = _courriels(args.get("cc"), False)
        bcc = _courriels(args.get("bcc"), False)
        for x in (dest, cc, bcc):
            if isinstance(x, str):
                return x
        assert isinstance(dest, list) and isinstance(cc, list) and isinstance(bcc, list)
        objet = " ".join(str(args.get("subject") or "").split())[:200]
        corps = str(args.get("body") or "").strip()
        if not corps:
            return "corps du mail vide"
        if len(corps) > CORPS_MAX:
            return (f"corps de {len(corps)} caractères : au-delà de {CORPS_MAX}, il ne tient pas "
                    "en entier dans la demande de confirmation — raccourcis-le ou passe par l'app")
        propres = {"to": ", ".join(dest), "subject": objet, "body": corps,
                   "cc": ", ".join(cc), "bcc": ", ".join(bcc)}
        entete = ("📧 ENVOYER un mail" if envoi else "📝 Créer un BROUILLON Gmail (rien ne part)")
        lignes = [f"{entete} à {propres['to']}"]
        if cc:
            lignes.append(f"Cc : {propres['cc']}")
        if bcc:
            lignes.append(f"Cci : {propres['bcc']}")
        lignes += [f"Objet : {objet or '(sans objet)'}", "———", corps, "———"]

        def executer() -> str:
            try:
                appeler(feuille, propres)
            except Exception as e:
                if not envoi:
                    raise
                # Délai dépassé ≠ non parti : SMTP a pu accepter avant la coupure.
                return (f"❌ Envoi incertain ({str(e)[:160]}) : vérifie le dossier Envoyés "
                        "avant de redemander, pour ne pas l'envoyer deux fois.")
            return (f"✅ Mail envoyé à {propres['to']}." if envoi
                    else f"✅ Brouillon créé pour {propres['to']} (rien n'est parti).")

        return Action(outil, "\n".join(lignes), executer)

    return preparer


def preparer_raccourci(noms: Callable[[], list[str]],
                       lancer: Callable[[str, str], str]) -> Preparateur:
    def preparer(args: dict) -> Action | str:
        nom = str(args.get("name") or "").strip()
        if not nom:
            return "nom de raccourci vide"
        try:
            connus = noms()
        except Exception as e:
            return f"liste des raccourcis illisible ({type(e).__name__})"
        if nom not in connus:
            return f"raccourci « {nom[:60]} » introuvable (list_shortcuts donne les noms exacts)"
        entree = str(args.get("input_text") or "").strip()
        if len(entree) > ENTREE_MAX:
            return f"entrée de plus de {ENTREE_MAX} caractères"
        resume = f"⚡ Lancer le raccourci « {nom} »" + (f"\nEntrée : {entree}" if entree else "")

        def executer() -> str:
            r = lancer(nom, entree)
            return r if r.startswith(("ERREUR", "⚠️")) else f"✅ Raccourci « {nom} » lancé.\n{r}"[:800]

        return Action("run_shortcut", resume, executer)

    return preparer


def preparer_agenda(outil: str) -> Preparateur:
    from tools import agenda as ag

    def preparer(args: dict) -> Action | str:
        if outil == "creer_rappel":
            p = ag.preparer_rappel(args.get("titre", ""), args.get("echeance", ""),
                                   args.get("liste", ""), args.get("notes", ""))
        else:
            p = ag.preparer_evenement(args.get("titre", ""), args.get("debut", ""),
                                      args.get("fin", ""), args.get("duree_min", 60),
                                      args.get("lieu", ""), args.get("calendrier", ""),
                                      bool(args.get("journee", False)), args.get("notes", ""))
        if isinstance(p, str):
            return p
        prep = p
        return Action(outil, prep.resume, lambda: ag.executer(prep))

    return preparer


class Confirmations:
    """L'action en attente du canal (une au plus) et son cycle de vie."""

    def __init__(self, preparateurs: dict[str, Preparateur],
                 horloge: Callable[[], float] = time.time,
                 duree_s: float = DUREE_S) -> None:
        self.preparateurs = preparateurs
        self.horloge = horloge
        self.duree_s = duree_s
        self._action: Action | None = None
        self._depuis = 0.0
        self._expiree: float | None = None
        self._nouvelle = False
        self._ce_tour = False

    @property
    def noms(self) -> frozenset[str]:
        return frozenset(self.preparateurs)

    def debut_tour(self) -> None:
        self._ce_tour = False

    def _purger(self) -> None:
        now = self.horloge()
        if self._action is not None and now - self._depuis > self.duree_s:
            logger.info("[confirmation] %s expirée sans réponse", self._action.nom)
            self._action, self._expiree = None, self._depuis + self.duree_s
        if self._expiree is not None and now - self._expiree > OUBLI_EXPIREE_S:
            self._expiree = None

    def etat(self) -> str | None:
        """« attente », « expiree » (oui tardif à expliquer) ou None."""
        self._purger()
        if self._action is not None:
            return "attente"
        return "expiree" if self._expiree is not None else None

    def proposer(self, nom: str, args: dict) -> str:
        """Appelé par la garde à la place de l'outil : rend le texte pour le MODÈLE."""
        if self._ce_tour:
            return ("Refusé : une seule action à confirmer par message. Dis à l'utilisateur "
                    "que tu proposeras la suivante après sa réponse.")
        try:
            r = self.preparateurs[nom](args if isinstance(args, dict) else {})
        except Exception as e:
            logger.exception("[confirmation] préparation %s", nom)
            r = f"préparation impossible ({type(e).__name__})"
        if isinstance(r, str):
            return f"Action refusée AVANT confirmation : {r}. Corrige les arguments ou explique."
        remplace = self._action is not None
        self._action, self._depuis = r, self.horloge()
        self._expiree, self._nouvelle, self._ce_tour = None, True, True
        logger.info("[confirmation] %s proposée", nom)
        return ("EN ATTENTE DE CONFIRMATION — rien n'est fait. Le canal envoie à "
                "l'utilisateur ce récapitulatif exact et attend son « oui » (10 min) :\n"
                f"{r.resume}\n"
                + ("(Elle remplace la proposition précédente, annulée.)\n" if remplace else "")
                + "Réponds-lui en une phrase que tu attends sa confirmation ; ne dis "
                  "jamais que c'est fait.")

    def recapitulatif_nouveau(self) -> str | None:
        """Le message de confirmation à envoyer après le tour, une seule fois."""
        if not self._nouvelle or self._action is None:
            return None
        self._nouvelle = False
        minutes = int(self.duree_s // 60)
        return (f"⏳ À confirmer :\n{self._action.resume}\n\n"
                f"Réponds « oui » pour le faire, « non » pour annuler ({minutes} min).")

    def repondre(self, mot: str) -> tuple[str, str]:
        """Traite « oui » / « non » ; rend (texte pour l'utilisateur, ligne d'audit)."""
        etat = self.etat()
        if etat == "expiree":
            self._expiree = None
            return ("⌛ La proposition a expiré sans réponse : rien n'a été fait. "
                    "Redemande-la si besoin.", "expiree")
        action, self._action = self._action, None
        if action is None:
            return "Rien à confirmer.", "vide"
        if mot != "oui":
            logger.info("[confirmation] %s annulée", action.nom)
            return "Annulé : rien n'a été fait.", f"{action.nom} annulée"
        try:
            r = action.executer()
        except Exception as e:
            logger.exception("[confirmation] exécution %s", action.nom)
            r = f"❌ Échec : {type(e).__name__} — {str(e)[:200]}"
        ok = not r.startswith(("❌", "ERREUR", "⚠️"))
        logger.info("[confirmation] %s exécutée → %s", action.nom, "ok" if ok else "échec")
        return r, f"{action.nom} {'ok' if ok else 'échec'}"


def confirmations_par_defaut(gmail_url: str | None) -> Confirmations:
    """Les actions confirmables du canal : agenda, raccourcis, et Gmail s'il est configuré."""
    from tools import mac_control

    preparateurs: dict[str, Preparateur] = {
        "creer_rappel": preparer_agenda("creer_rappel"),
        "creer_evenement": preparer_agenda("creer_evenement"),
        "run_shortcut": preparer_raccourci(mac_control.raccourcis_disponibles,
                                           mac_control.run_shortcut),
    }
    if gmail_url:
        from canaux.proactif import GmailMCP

        gmail = GmailMCP(gmail_url)
        for outil in (GMAIL_ENVOI, GMAIL_BROUILLON):
            preparateurs[outil] = preparer_mail(outil, gmail.appeler)
    return Confirmations(preparateurs)
