"""Plan de tâche : découper un objectif en étapes, les suivre, les cocher.

Fonctions PURES sur une liste d'étapes ``{"titre", "statut", "note"}`` : l'état vit
dans la mémoire de session (``ConversationMemory.plan``, persistée avec elle), pas
ici — l'orchestrateur est reconstruit à chaque message, la session survit.

Trois usages :
- l'outil ``plan_tache`` (``executer``) que le modèle appelle ;
- ``section_tour`` : le plan ouvert rappelé au début de chaque tour, dans le
  contexte du tour (jamais dans le système, qui doit rester stable pour le cache
  de préfixe) ;
- ``etapes_ouvertes`` : la garde de l'orchestrateur refuse une conclusion tant
  qu'une étape touchée pendant ce tour reste ouverte sans explication.
"""
from __future__ import annotations

from typing import Any

ETAPES_MAX = 12
TITRE_MAX = 160
NOTE_MAX = 300

A_FAIRE, EN_COURS, FAIT, BLOQUE, ABANDONNE = "a_faire", "en_cours", "fait", "bloque", "abandonne"
SYMBOLES = {A_FAIRE: "☐", EN_COURS: "▶", FAIT: "✅", BLOQUE: "⛔", ABANDONNE: "✖"}
# Un plan est « clos » quand plus rien n'attend d'être fait. Bloqué compte comme
# ouvert : c'est une étape à débloquer (ou à abandonner en le disant).
TERMINAUX = frozenset({FAIT, ABANDONNE})
_ALIAS_STATUT = {
    "a_faire": A_FAIRE, "à faire": A_FAIRE, "a faire": A_FAIRE, "todo": A_FAIRE,
    "en_cours": EN_COURS, "en cours": EN_COURS, "in_progress": EN_COURS,
    "fait": FAIT, "faite": FAIT, "done": FAIT, "termine": FAIT, "terminé": FAIT,
    "bloque": BLOQUE, "bloqué": BLOQUE, "bloquee": BLOQUE, "bloquée": BLOQUE, "blocked": BLOQUE,
    "abandonne": ABANDONNE, "abandonné": ABANDONNE, "abandonnee": ABANDONNE,
    "abandonnée": ABANDONNE, "annule": ABANDONNE, "annulé": ABANDONNE,
}

Plan = list[dict[str, str]]


def _texte(v: Any, maxi: int) -> str:
    """Une ligne propre : pas de saut de ligne (le rendu est une liste), bornée."""
    s = " ".join(str(v or "").split())
    return s if len(s) <= maxi else s[: maxi - 1] + "…"


def _statut(v: Any) -> str | None:
    return _ALIAS_STATUT.get(" ".join(str(v or "").lower().split()).replace("-", "_"))


def normaliser(plan: Any) -> Plan:
    """Plan relu depuis la session : tolère un fichier ancien ou abîmé."""
    if not isinstance(plan, list):
        return []
    propre: Plan = []
    for e in plan[:ETAPES_MAX]:
        if not isinstance(e, dict):
            continue
        titre = _texte(e.get("titre"), TITRE_MAX)
        if titre:
            propre.append({"titre": titre, "statut": _statut(e.get("statut")) or A_FAIRE,
                           "note": _texte(e.get("note"), NOTE_MAX)})
    return propre


def etapes_ouvertes(plan: Plan) -> list[int]:
    """Numéros (1-based) des étapes ni faites ni abandonnées."""
    return [i for i, e in enumerate(plan, 1) if e["statut"] not in TERMINAUX]


def rendre(plan: Plan) -> str:
    if not plan:
        return "Aucun plan en cours."
    faites = sum(e["statut"] == FAIT for e in plan)
    lignes = [f"Plan ({faites}/{len(plan)} faite{'s' if faites > 1 else ''}) :"]
    for i, e in enumerate(plan, 1):
        note = f" — {e['note']}" if e["note"] else ""
        lignes.append(f"{i}. {SYMBOLES[e['statut']]} {e['titre']}{note}")
    return "\n".join(lignes)


def _demarrer_suivante(plan: Plan) -> None:
    """Une seule étape en cours : la première à faire si aucune ne l'est."""
    if any(e["statut"] == EN_COURS for e in plan):
        return
    for e in plan:
        if e["statut"] == A_FAIRE:
            e["statut"] = EN_COURS
            return


def _etapes(brut: Any) -> list[str] | str:
    if isinstance(brut, str):
        brut = [ligne for ligne in brut.splitlines() if ligne.strip()]
    if not isinstance(brut, list):
        return "`etapes` doit être une liste de phrases courtes."
    etapes = [_texte(e.get("titre") if isinstance(e, dict) else e, TITRE_MAX) for e in brut]
    etapes = [e for e in etapes if e]
    if len(etapes) < 2:
        return "Un plan compte au moins 2 étapes ; pour une seule action, fais-la directement."
    if len(etapes) > ETAPES_MAX:
        return (f"{len(etapes)} étapes : c'est trop (max {ETAPES_MAX}). Regroupe-les "
                "en étapes vérifiables.")
    return etapes


def _numero(v: Any, plan: Plan) -> int | str:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return "`numero` doit être le numéro d'une étape (1, 2, …)."
    if not 1 <= n <= len(plan):
        return f"Il n'y a pas d'étape {n} (le plan en compte {len(plan)})."
    return n


def executer(plan_actuel: Any, args: dict[str, Any]) -> tuple[Plan, str]:
    """Applique une action de l'outil ``plan_tache``. Renvoie (plan, texte pour le modèle).

    Une erreur rend le plan INCHANGÉ et un texte qui commence par « Plan inchangé ».
    """
    plan = normaliser(plan_actuel)
    action = str(args.get("action") or "voir").strip().lower()

    def refus(raison: str) -> tuple[Plan, str]:
        return plan, f"Plan inchangé : {raison}\n{rendre(plan)}"

    if action == "voir":
        return plan, rendre(plan)
    if action == "effacer":
        return [], "Plan effacé."
    if action == "definir":
        etapes = _etapes(args.get("etapes"))
        if isinstance(etapes, str):
            return refus(etapes)
        nouveau = [{"titre": t, "statut": A_FAIRE, "note": ""} for t in etapes]
        _demarrer_suivante(nouveau)
        remplace = " (l'ancien plan est remplacé)" if etapes_ouvertes(plan) else ""
        return nouveau, f"Plan défini{remplace}.\n{rendre(nouveau)}"
    if action == "ajouter":
        titre = _texte(args.get("etape"), TITRE_MAX)
        if not titre:
            return refus("`etape` (le titre de l'étape à ajouter) est vide.")
        if len(plan) >= ETAPES_MAX:
            return refus(f"le plan a déjà {ETAPES_MAX} étapes.")
        apres = args.get("apres")
        position = len(plan)
        if apres not in (None, "", 0, "0"):
            n = _numero(apres, plan)
            if isinstance(n, str):
                return refus(n)
            position = n
        elif apres in (0, "0"):
            position = 0
        nouveau = [dict(e) for e in plan]
        nouveau.insert(position, {"titre": titre, "statut": A_FAIRE, "note": ""})
        _demarrer_suivante(nouveau)
        return nouveau, f"Étape ajoutée en position {position + 1}.\n{rendre(nouveau)}"
    if action == "cocher":
        if not plan:
            return refus("aucun plan en cours ; commence par action=definir.")
        n = _numero(args.get("numero"), plan)
        if isinstance(n, str):
            return refus(n)
        statut = _statut(args.get("statut") or FAIT)
        if statut is None:
            return refus("`statut` doit être fait, en_cours, bloque, abandonne ou a_faire.")
        note = _texte(args.get("note"), NOTE_MAX)
        if statut in (BLOQUE, ABANDONNE) and not note:
            return refus(f"dis pourquoi dans `note` quand une étape est {statut.replace('_', ' ')}.")
        nouveau = [dict(e) for e in plan]
        if statut == EN_COURS:
            for e in nouveau:
                if e["statut"] == EN_COURS:
                    e["statut"] = A_FAIRE
        nouveau[n - 1]["statut"] = statut
        if note:
            nouveau[n - 1]["note"] = note
        _demarrer_suivante(nouveau)
        fin = "" if etapes_ouvertes(nouveau) else "\nToutes les étapes sont closes."
        return nouveau, f"Étape {n} : {SYMBOLES[statut]}.\n{rendre(nouveau)}{fin}"
    return refus(f"action « {action} » inconnue (definir, cocher, ajouter, voir, effacer).")


def section_tour(plan: Any) -> str:
    """Rappel du plan OUVERT pour le contexte du tour ; vide si rien n'est ouvert."""
    propre = normaliser(plan)
    if not etapes_ouvertes(propre):
        return ""
    return (
        "\n\n## Plan en cours (outil plan_tache)\n"
        f"{rendre(propre)}\n"
        "Si la demande poursuit ce plan, travaille l'étape ▶ et coche chaque étape "
        "dès qu'elle est faite. Si elle porte sur autre chose, ignore ce plan."
    )


def relance_garde(plan: Plan) -> str:
    """Message injecté quand l'agent veut conclure avec des étapes encore ouvertes."""
    ouvertes = etapes_ouvertes(plan)
    liste = ", ".join(f"{n}. {plan[n - 1]['titre']}" for n in ouvertes[:5])
    if len(ouvertes) > 5:
        liste += f" (+{len(ouvertes) - 5})"
    return (
        "STOP — ne conclus pas encore. Ton plan a des étapes ouvertes : "
        f"{liste}.\n"
        "Pour chacune : fais-la puis coche-la (plan_tache action=cocher statut=fait), "
        "ou, si elle ne peut pas être faite maintenant, coche-la bloque ou abandonne "
        "avec une note qui dit pourquoi. Ensuite seulement, réponds en disant ce "
        "qui est fait et ce qui ne l'est pas."
    )
