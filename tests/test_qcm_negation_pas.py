"""Non-régression : la négation « pas » ne déclenche plus le QCM.

Vécu le 2026-09-27. `Orchestrator._detect_interactive_skill` n'active le mode
« skill interactif » (QCM) que si la requête recoupe l'IDENTITÉ du skill de
tête (nom + slug). Or le seul skill interactif s'appelle « Concevoir un
algorithme pas à pas » : le « pas » de n'importe quelle négation française
passait la garde. Les 5 tâches `discovery` du banc (« NE SONT PAS écrites
dans cet énoncé ») et 319 des 1 787 messages utilisateur distincts de
~/.klody/data basculaient en QCM : tâche de code forcée sur le généraliste,
anti-stall et text-to-action coupés, `ask_user` exposé. Présent depuis #27.

Ce qui est verrouillé ici, sur le VRAI corpus de skills et les VRAIS énoncés
du banc (pas des doubles) :

- aucun énoncé du banc n'active le QCM — la liste se recalcule à chaque
  tâche ajoutée, elle ne se recopie pas ;
- la garde d'identité, testée seule, ne voit aucun terme du nom dans ces
  énoncés — indépendamment du classement de `select_skills`, qui met bel et
  bien ce skill en tête sur `discovery` (par `list_files` → « file »,
  « avant », « écrire ») et peut changer demain ;
- les vraies demandes de conception activent toujours le QCM.

⚠️ Le seuil « df faible » a été examiné et ÉCARTÉ : sur nom+desc+slug, « pas »
a df = 10 quand « structure » a 20 et « méthodes » 31 — un seuil de df
rejetterait des mots d'identité légitimes avant « pas ». La bonne distinction
est grammaticale (mot vide), pas statistique.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.orchestrateur.routage import _skill_is_interactive
from agent.orchestrator import Orchestrator
from bench.framework import discover_tasks
from tools.skills import _matching_terms, _skill_terms, load_skills

_SLUG_QCM = "concevoir_un_algorithme_pas_a_pas"
_TACHES = discover_tasks()


def _qcm(requete: str) -> bool:
    return Orchestrator._detect_interactive_skill(SimpleNamespace(), requete)


def _skill_qcm() -> dict:
    return next(s for s in load_skills() if s.get("slug") == _SLUG_QCM)


def _identite(skill: dict) -> dict:
    """Ce que la garde de `_detect_interactive_skill` compare : nom + slug."""
    return {"name": skill["name"], "slug": skill["slug"], "description": ""}


class TestPrecondition:
    """Sans ces faits, les tests ci-dessous passeraient au vert sans rien
    prouver : un skill renommé ou désactivé rendrait l'incident impossible à
    reproduire, et un test qui ne peut pas rougir ne garde rien."""

    def test_le_skill_qcm_existe_et_est_interactif(self):
        skill = _skill_qcm()
        assert _skill_is_interactive(skill)

    def test_son_nom_contient_la_negation(self):
        # C'est le « pas » du nom qui faisait passer la garde d'identité.
        assert "pas" in _skill_qcm()["name"].lower().split()

    def test_le_banc_contient_des_negations(self):
        avec_pas = [t for t, c in _TACHES.items() if " pas " in f" {c.prompt.lower()} "]
        assert len(avec_pas) >= 5, avec_pas


@pytest.mark.parametrize("tache_id", sorted(_TACHES))
def test_aucun_enonce_du_banc_n_active_le_qcm(tache_id):
    """Bout en bout : sélection réelle + garde d'identité réelle."""
    assert _qcm(_TACHES[tache_id].prompt) is False, (
        f"{tache_id} bascule en QCM : tâche forcée sur le généraliste, "
        "anti-stall coupé, `ask_user` exposé"
    )


@pytest.mark.parametrize("tache_id", sorted(_TACHES))
def test_la_garde_d_identite_ne_voit_rien_dans_le_banc(tache_id):
    """Le mécanisme seul, indépendant du classement de `select_skills`."""
    termes = _matching_terms(_skill_terms(_TACHES[tache_id].prompt), _identite(_skill_qcm()))
    assert termes == set(), f"{tache_id} nomme le skill QCM par {termes}"


class TestSkillTerms:
    def test_pas_est_un_mot_vide(self):
        assert "pas" not in _skill_terms("ces contraintes ne sont pas écrites")

    def test_apocope_algo_ramenee_a_la_forme_pleine(self):
        # Sans elle, « conçois mon algo pas à pas » ne nommait le skill que par
        # « pas » : la retirer des mots vides aurait perdu cette demande.
        assert _skill_terms("conçois mon algo") >= {"algorithme"}
        assert _skill_terms("deux algos") >= {"algorithmes"}


@pytest.mark.parametrize("requete", [
    "aide-moi à concevoir l'algorithme de mon jeu",
    "aide-moi à concevoir un algorithme pas à pas",
    # Phrases des intégrations WebSocket du QCM (test_websocket_chat.py).
    "conçois mon algo pas à pas",
    "conçois mon algo",
    # Déclencheur écrit dans la description du skill : la négation y est
    # présente, mais c'est « algorithme » qui nomme le sujet.
    "je ne sais pas écrire l'algorithme de mon projet",
])
def test_les_vraies_demandes_de_conception_activent_le_qcm(requete):
    assert _qcm(requete) is True


@pytest.mark.parametrize("requete", [
    # Message réel (session KlodyAI), chemins raccourcis : lecture de code
    # détournée en questionnaire par « N'utilise PAS ».
    "Lis le code source de LibraryBrain dans ~/library-brain (list_files puis "
    "read_file). N'utilise PAS ~/Downloads/library-brain-main.",
    # « pas à pas » décrit une MANIÈRE de faire, pas le sujet du skill.
    "corrige ce bug pas à pas",
    "explique-moi pas à pas comment marche ce code",
])
def test_une_negation_ou_une_maniere_ne_nomme_pas_le_skill(requete):
    assert _qcm(requete) is False
