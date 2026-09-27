"""Le déclencheur local du nightly doit déclencher QUAND il faut, et seulement alors.

Le cron GitHub tirait jusqu'à 5 h 36 en retard (mesuré sur 60 runs) : le run
arrivait sur un Mac endormi et la sentinelle l'annulait. `scripts/bench_dispatch.py`
déclenche depuis le Mac. Ce qui se verrouille ici :

1. un run récent EN VOL ou JUGÉ (vert comme rouge) couvre la journée — pas de
   doublon, pas de relance en boucle d'un même verdict rouge ;
2. un run ANNULÉ ne couvre rien — c'est exactement le cas à rattraper ;
3. « pas pu interroger / pas pu déclencher » rend 1, jamais 0 ;
4. la fenêtre est jugée sur des durées LITTÉRALES (19 h / 21 h), jamais
   recalculées depuis `FENETRE_H` — un test qui suit le réglage qu'il protège
   ne peut pas rougir (mutation échappée sur la veille Qwen) ;
5. le Mac reste éveillé au moins le temps de grâce de la sentinelle, lu dans le
   workflow réel.
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts import bench_dispatch as mod

MAINTENANT = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _run(il_y_a_h: float, status: str = "completed", conclusion: str | None = "success") -> dict:
    cree = (MAINTENANT - timedelta(hours=il_y_a_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "status": status,
        "conclusion": conclusion,
        "createdAt": cree,
        "databaseId": 42,
        "event": "workflow_dispatch",
    }


# --- 1-2. Qui couvre la journée ------------------------------------------------


class TestCouverture:
    def test_aucun_run(self):
        assert mod.run_couvrant([], MAINTENANT) is None

    def test_vert_recent_couvre(self):
        assert mod.run_couvrant([_run(2)], MAINTENANT) is not None

    def test_rouge_recent_couvre(self):
        """Un rouge est une mesure : le relancer à chaque créneau multiplierait
        le même verdict sans rien apprendre."""
        assert mod.run_couvrant([_run(2, conclusion="failure")], MAINTENANT) is not None

    def test_annule_ne_couvre_pas(self):
        """Le cas même qu'on rattrape : la sentinelle a annulé un run que
        personne n'a pris, rien n'a été mesuré."""
        assert mod.run_couvrant([_run(2, conclusion="cancelled")], MAINTENANT) is None

    @pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending", "requested"])
    def test_en_vol_couvre(self, status):
        assert mod.run_couvrant([_run(0.1, status=status, conclusion=None)], MAINTENANT) is not None

    def test_fenetre_litterale_19h_couvre(self):
        assert mod.run_couvrant([_run(19)], MAINTENANT) is not None

    def test_fenetre_litterale_21h_ne_couvre_pas(self):
        assert mod.run_couvrant([_run(21)], MAINTENANT) is None

    def test_un_annule_recent_ne_masque_pas_un_vert_plus_ancien_dans_la_fenetre(self):
        runs = [_run(1, conclusion="cancelled"), _run(10)]
        assert mod.run_couvrant(runs, MAINTENANT)["createdAt"] == runs[1]["createdAt"]


# --- 3. Codes de sortie ---------------------------------------------------------


@pytest.fixture
def espion(monkeypatch):
    appels = {"declencher": 0, "eveil": 0}

    def _declencher():
        appels["declencher"] += 1

    def _eveil():
        appels["eveil"] += 1

    monkeypatch.setattr(mod, "declencher", _declencher)
    monkeypatch.setattr(mod, "garder_eveille", _eveil)
    return appels


class TestExecution:
    def test_interrogation_impossible_rend_1_sans_declencher(self, espion, monkeypatch):
        def boom():
            raise RuntimeError("gh run list a échoué (code 1): 503")

        monkeypatch.setattr(mod, "lister_runs", boom)
        assert mod.executer(check_seulement=False) == 1
        assert espion["declencher"] == 0

    def test_deja_couvert_rend_0_sans_declencher(self, espion, monkeypatch):
        recent = datetime.now(UTC) - timedelta(hours=1)
        run = {"status": "completed", "conclusion": "success",
               "createdAt": recent.strftime("%Y-%m-%dT%H:%M:%SZ"), "databaseId": 1}
        monkeypatch.setattr(mod, "lister_runs", lambda: [run])
        assert mod.executer(check_seulement=False) == 0
        assert espion == {"declencher": 0, "eveil": 0}

    def test_non_couvert_declenche_et_tient_le_mac_eveille(self, espion, monkeypatch):
        monkeypatch.setattr(mod, "lister_runs", lambda: [])
        assert mod.executer(check_seulement=False) == 0
        assert espion == {"declencher": 1, "eveil": 1}

    def test_declenchement_rate_rend_1(self, espion, monkeypatch):
        def boom():
            raise RuntimeError("gh workflow run a échoué (code 1): HTTP 403")

        monkeypatch.setattr(mod, "lister_runs", lambda: [])
        monkeypatch.setattr(mod, "declencher", boom)
        assert mod.executer(check_seulement=False) == 1

    def test_check_ne_declenche_rien(self, espion, monkeypatch):
        monkeypatch.setattr(mod, "lister_runs", lambda: [])
        assert mod.executer(check_seulement=True) == 0
        assert espion == {"declencher": 0, "eveil": 0}


class TestGhSousLaunchd:
    """launchd ne donne que PATH=/usr/bin:/bin:/usr/sbin:/sbin — `gh` n'y est pas."""

    def test_repli_sur_les_emplacements_homebrew(self, monkeypatch, tmp_path):
        faux_gh = tmp_path / "gh"
        faux_gh.write_text("#!/bin/sh\n")
        monkeypatch.setattr(mod.shutil, "which", lambda _nom: None)
        monkeypatch.setattr(mod, "GH_CANDIDATS", (str(tmp_path / "absent"), str(faux_gh)))
        assert mod.trouver_gh() == str(faux_gh)

    def test_gh_introuvable_rend_1(self, espion, monkeypatch, tmp_path):
        monkeypatch.setattr(mod.shutil, "which", lambda _nom: None)
        monkeypatch.setattr(mod, "GH_CANDIDATS", (str(tmp_path / "absent"),))
        assert mod.executer(check_seulement=False) == 1
        assert espion["declencher"] == 0

    def test_commande_de_declenchement(self, monkeypatch):
        vus: list[list[str]] = []

        def faux_run(cmd, **_kw):
            vus.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(mod, "trouver_gh", lambda: "/opt/homebrew/bin/gh")
        monkeypatch.setattr(mod.subprocess, "run", faux_run)
        mod.declencher()
        assert vus == [[
            "/opt/homebrew/bin/gh", "workflow", "run", "bench-nightly.yml",
            "--repo=klodynlov/klody-code-ai", "--ref=main",
        ]]


# --- 5. Cohérence avec le workflow et l'agent launchd ---------------------------

WORKFLOW = REPO / ".github" / "workflows" / mod.WORKFLOW
PLIST = REPO / "launchagents" / "com.klody.bench-dispatch.plist"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_le_workflow_vise_existe_et_se_declenche_a_la_main():
    donnees = _workflow()
    # PyYAML lit la clé `on` comme le booléen True.
    declencheurs = donnees.get("on", donnees.get(True))
    assert "workflow_dispatch" in declencheurs


def test_le_mac_reste_eveille_au_moins_le_delai_de_la_sentinelle():
    """Lu dans le workflow réel, jamais recopié : si la sentinelle patiente plus
    longtemps, le Mac doit tenir aussi longtemps."""
    grace = int(_workflow()["jobs"]["verify-runner"]["env"]["GRACE_SECONDS"])
    assert grace <= mod.EVEIL_S


@pytest.fixture(scope="module")
def plist() -> dict:
    return plistlib.loads(PLIST.read_bytes())


class TestAgentLaunchd:
    def test_lance_ce_script(self, plist):
        assert plist["ProgramArguments"][-1].endswith("/scripts/bench_dispatch.py")

    def test_plusieurs_creneaux(self, plist):
        """Un seul créneau raté (Mac endormi puis réveillé trop tard, run
        annulé) coûterait une journée entière de mesure."""
        creneaux = plist["StartCalendarInterval"]
        assert isinstance(creneaux, list) and len(creneaux) >= 3

    def test_l_installation_ne_lance_pas_de_bench(self, plist):
        assert not plist.get("RunAtLoad", False)

    def test_le_caffeinate_survit_au_script(self, plist):
        assert plist.get("AbandonProcessGroup") is True
