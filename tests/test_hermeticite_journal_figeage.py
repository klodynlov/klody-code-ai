"""La suite n'écrit pas dans le vrai journal de figeage de l'API.

Ce fichier existe pour que le détournement posé par `tests/conftest.py` puisse
ROUGIR. Sans lui, retirer `journal_figeage.detourner()` ne ferait tomber aucun
test : la suite se remettrait à écrire « dumper armé » dans
`~/Library/Logs/klody-api-hang.log`, un fichier que pytest ne regarde jamais.

On teste l'EFFET, pas seulement le réglage. « `_HANG_DUMP_PATH` n'est pas le
vrai chemin » resterait vrai avec un second armement, resté sur le chemin par
défaut. Le verdict est donc le vrai journal lui-même, relu après la position
qu'il avait avant que la suite ne touche à l'API. Et la sonde qui le relit est
d'abord éprouvée sur l'armement réel du dumper — sans quoi un changement du
format de la ligne la rendrait aveugle, donc verte à jamais.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

from tests import journal_figeage


def test_le_dumper_est_arme_dans_le_fichier_jetable_et_la_sonde_l_y_voit():
    """Le dumper fonctionne toujours — ailleurs. Et la sonde reconnaît sa ligne.

    Les deux moitiés comptent : sans la première, un dumper simplement désarmé
    passerait pour un dumper détourné ; sans la seconde, le test suivant pourrait
    chercher une ligne que le serveur n'écrit plus sous cette forme.
    """
    from api import server

    jetable = Path(os.environ[journal_figeage.VARIABLE])
    assert jetable == server._HANG_DUMP_PATH
    assert server._HANG_DUMP_PATH != journal_figeage.VRAI_JOURNAL
    assert journal_figeage.armements(server._HANG_DUMP_PATH, os.getpid()), (
        f"aucune ligne « dumper armé, pid {os.getpid()} » dans "
        f"{server._HANG_DUMP_PATH} : l'armement n'a pas eu lieu, ou son format a "
        "changé et la sonde de tests/journal_figeage.py ne le reconnaît plus"
    )


def test_le_vrai_journal_ne_recoit_aucun_armement_de_la_suite():
    """Vécu jusqu'au 2026-09-27 : une ligne par session pytest dans le vrai journal.

    L'import est forcé ici : l'armement a lieu À L'IMPORT, et ce test ne doit pas
    dépendre de l'ordre d'exécution pour l'avoir déjà provoqué. Absent (CI
    Linux) ⇒ vide, et c'est exactement l'invariant voulu : une ligne apparue le
    ferait tomber.
    """
    import api.server

    fuites = journal_figeage.armements(
        journal_figeage.VRAI_JOURNAL, os.getpid(), depuis=journal_figeage.TAILLE_INITIALE
    )
    assert not fuites, (
        f"la suite (pid {os.getpid()}) a armé le dumper dans le VRAI "
        f"{journal_figeage.VRAI_JOURNAL} : le détournement de tests/conftest.py "
        "n'est plus posé avant le premier import d'api.server"
    )


def test_detourner_refuse_d_arriver_apres_l_import(monkeypatch):
    """Détourner après coup ne rattraperait rien : l'armement a déjà écrit.

    Un import anticipé (plugin, `tests/__init__.py`, haut du conftest) doit donc
    faire échouer le démarrage de la suite, pas la laisser écrire en silence.
    """
    monkeypatch.setitem(sys.modules, "api.server", types.ModuleType("api.server"))
    # Rendue à la sortie même si le refus ne venait pas.
    monkeypatch.setenv(journal_figeage.VARIABLE, os.environ[journal_figeage.VARIABLE])

    with pytest.raises(RuntimeError, match="déjà importé"):
        journal_figeage.detourner()


class TestSonde:
    """La sonde elle-même : ce qui la ferait rougir, et ce qui ne le doit pas."""

    def test_voit_la_ligne_de_ce_pid_apres_la_position(self, tmp_path):
        journal = tmp_path / "hang.log"
        journal.write_text("\n=== dumper armé, pid 111 ===\n", encoding="utf-8")
        depuis = journal.stat().st_size
        with journal.open("a", encoding="utf-8") as f:
            f.write("\n=== dumper armé, pid 222 ===\n")

        assert journal_figeage.armements(journal, 222, depuis) == [
            "=== dumper armé, pid 222 ==="
        ]

    def test_ignore_un_autre_pid_et_ce_qui_precede_la_position(self, tmp_path):
        """Une API de prod qui redémarre pendant la suite a un AUTRE pid : deux
        processus vivants ne partagent jamais le leur. Et une ligne ancienne d'un
        pid recyclé est avant la position."""
        journal = tmp_path / "hang.log"
        journal.write_text("\n=== dumper armé, pid 222 ===\n", encoding="utf-8")
        depuis = journal.stat().st_size
        with journal.open("a", encoding="utf-8") as f:
            f.write("\n=== dumper armé, pid 333 ===\n")

        assert journal_figeage.armements(journal, 222, depuis) == []

    def test_ne_confond_pas_un_pid_avec_son_prefixe(self, tmp_path):
        journal = tmp_path / "hang.log"
        journal.write_text("\n=== dumper armé, pid 2222 ===\n", encoding="utf-8")

        assert journal_figeage.armements(journal, 222) == []

    def test_fichier_absent_rend_vide(self, tmp_path):
        assert journal_figeage.armements(tmp_path / "absent.log", 222) == []

    def test_fichier_tronque_est_relu_depuis_le_debut(self, tmp_path):
        """Remplacé par plus court que la position mémorisée : tout ce qu'il
        contient a été écrit depuis, y compris une ligne de ce pid."""
        journal = tmp_path / "hang.log"
        journal.write_text("\n=== dumper armé, pid 222 ===\n", encoding="utf-8")

        assert journal_figeage.armements(journal, 222, depuis=10_000) == [
            "=== dumper armé, pid 222 ==="
        ]
