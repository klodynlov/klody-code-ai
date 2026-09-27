"""La suite n'écrit pas dans `~/.klody/data` — et le garde qui l'assure sait rougir.

Jumeau de `test_hermeticite_voix.py`, pour la même raison : `tests/garde_etat.py`
et la fixture `_etat_persistant_isole` doivent pouvoir TOMBER. Sans ce fichier,
une régression qui retirerait le hook d'audit ou déplacerait la redirection après
`import config` laisserait la suite verte — et le seul signe serait, de nouveau,
des milliers de sessions de test dans l'historique KlodyAI de l'utilisateur,
c'est-à-dire rien que pytest regarde.

Relevé le 2026-09-27 (sonde d'audit sur la suite complète, avant correctif) :
234 écritures dans le vrai dossier en UNE passe — rejeu de l'orchestrateur
(161), chat WebSocket (41 sessions + 13 `user_profile.json`), boucle de preview
(14), `max_tokens` par type (2), plus un `mkdir` via `importlib.reload(config)`.

⚠️ Ce qui est prouvé ici l'est SANS toucher au vrai dossier : l'auto-test du
garde protège un dossier de `tmp_path`, et la preuve de bout en bout tourne dans
un sous-processus dont le `HOME` est jetable.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import config
import pytest

from tests import garde_etat

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def faux_reel(tmp_path):
    """Un dossier protégé comme le vrai, pour faire rougir le garde sans risque.

    Le registre est remis à sa position d'entrée : les violations provoquées ici
    sont VOULUES, la fixture autouse ne doit pas les reprocher au test.
    """
    dossier = tmp_path / "faux-reel"
    dossier.mkdir()
    (dossier / "existant.json").write_text("{}", encoding="utf-8")
    position = garde_etat.marque()
    with garde_etat.racine_protegee(dossier):
        yield dossier, position
    garde_etat.oublier_depuis(position)


# --- la redirection -----------------------------------------------------------


def test_le_vrai_dossier_par_defaut_est_sous_protection():
    """Si ce chemin sortait de la liste, le hook ne garderait plus rien."""
    vrai = (Path.home() / ".klody" / "data").absolute()
    assert vrai in garde_etat.VRAIS_DOSSIERS
    assert garde_etat.est_protege(vrai / "memory_abcd1234.json")


@pytest.mark.parametrize(
    ("module", "attribut"),
    [
        ("config", "MEMORY_DIR"),
        ("config", "SEMANTIC_MEMORY_DB"),
        # Figés À L'IMPORT : un monkeypatch de config.MEMORY_DIR ne les atteint
        # pas. C'est pour eux que la redirection passe par l'environnement.
        ("agent.long_term_memory", "_STORAGE"),
        ("agent.profiler", "_PROFILE_FILE"),
        ("agent.greeting", "MEMORY_DIR"),
        ("main", "MEMORY_DIR"),
    ],
)
def test_aucun_chemin_d_etat_ne_pointe_sur_le_vrai_dossier(module, attribut):
    import importlib

    valeur = getattr(importlib.import_module(module), attribut)
    assert not garde_etat.est_protege(valeur), f"{module}.{attribut} = {valeur}"


def test_une_session_de_conversation_atterrit_dans_le_dossier_du_test():
    """L'effet, pas le réglage : c'est `ConversationMemory` qui a produit les
    milliers de fichiers."""
    from agent.memory import ConversationMemory

    mem = ConversationMemory()
    mem.add_message("user", "conçois mon algo pas à pas")

    assert mem.memory_file.exists()
    assert mem.memory_file.parent == config.MEMORY_DIR
    assert not garde_etat.est_protege(mem.memory_file)


def test_chaque_test_a_son_propre_dossier():
    """Une session laissée par un test ne doit pas apparaître dans le listage
    d'un autre (`/api/sessions`, `load_latest`)."""
    assert list(config.MEMORY_DIR.glob("memory_*.json")) == []
    assert os.environ["KLODY_DATA_DIR"] == str(config.MEMORY_DIR)


# --- les sous-processus de la suite -------------------------------------------


def _vrais_dossiers_d_un_processus(env: dict[str, str]) -> list[Path]:
    """`garde_etat.VRAIS_DOSSIERS` tel que le calcule un processus NEUF sous `env`
    — la liste est figée à l'import, seul un interpréteur frais la recalcule."""
    proc = subprocess.run(
        [sys.executable, "-c",
         "from tests import garde_etat\n"
         "for p in garde_etat.VRAIS_DOSSIERS: print(p)"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=60, check=True,
    )
    return [Path(ligne) for ligne in proc.stdout.splitlines()]


def test_un_fils_de_la_suite_ne_protege_pas_son_dossier_jetable():
    """Vécu le 2026-09-27 : le pytest fils de `test_hermeticite_mcp.py` héritait du
    `KLODY_DATA_DIR` posé par la fixture, le lisait comme « surcharge du
    développeur », le protégeait — et son conftest refusait de démarrer."""
    fils = _vrais_dossiers_d_un_processus(dict(os.environ))

    assert config.MEMORY_DIR.absolute() not in fils
    # …sans rien lâcher de ce que la suite protège : c'est la moitié qui compte.
    assert (Path.home() / ".klody" / "data").absolute() in fils
    assert set(garde_etat.VRAIS_DOSSIERS) <= set(fils)


def test_un_fils_herite_de_la_surcharge_du_developpeur(tmp_path):
    """La surcharge réelle a été capturée par l'ancêtre : le fils la reçoit par
    l'héritage, pas par `KLODY_DATA_DIR`, qui ne désigne plus qu'un jetable."""
    reel = tmp_path / "second-profil"
    jetable = tmp_path / "jetable"
    env = {**os.environ, "_KLODY_TESTS_VRAIS_DOSSIERS": str(reel),
           "KLODY_DATA_DIR": str(jetable)}

    fils = _vrais_dossiers_d_un_processus(env)

    assert reel.absolute() in fils
    assert jetable.absolute() not in fils


def test_hors_de_la_suite_la_surcharge_est_protegee(tmp_path):
    """Processus racine (aucun ancêtre de la suite) : `KLODY_DATA_DIR` EST la
    surcharge du développeur, donc une donnée réelle."""
    surcharge = tmp_path / "second-profil"
    env = {k: v for k, v in os.environ.items() if k != "_KLODY_TESTS_VRAIS_DOSSIERS"}
    env["KLODY_DATA_DIR"] = str(surcharge)

    assert surcharge.absolute() in _vrais_dossiers_d_un_processus(env)


# --- le garde refuse ET consigne ----------------------------------------------


def test_le_garde_refuse_toute_forme_d_ecriture(faux_reel, tmp_path):
    dossier, position = faux_reel
    dehors = tmp_path / "dehors.json"
    dehors.write_text("{}", encoding="utf-8")

    tentatives = {
        "open w": lambda: open(dossier / "a.json", "w"),  # noqa: SIM115
        "write_text": lambda: (dossier / "b.json").write_text("x"),
        "open a": lambda: open(dossier / "existant.json", "a"),  # noqa: SIM115
        "os.open": lambda: os.open(dossier / "c.json", os.O_WRONLY | os.O_CREAT),
        # Écriture atomique (tmp + replace) : la forme la plus facile à rater.
        "os.replace": lambda: os.replace(dehors, dossier / "d.json"),
        "unlink": lambda: (dossier / "existant.json").unlink(),
        "mkdir": lambda: (dossier / "sous").mkdir(),
        "sqlite3": lambda: sqlite3.connect(dossier / "semantic_memory.db"),
    }
    echappees = []
    for nom, tentative in tentatives.items():
        try:
            tentative()
        except PermissionError as exc:
            assert "état persistant réel" in str(exc)
        else:
            echappees.append(nom)

    assert echappees == [], f"écritures passées au travers du garde : {echappees}"
    assert sorted(p.name for p in dossier.iterdir()) == ["existant.json"]
    assert (dossier / "existant.json").read_text(encoding="utf-8") == "{}"
    assert len(garde_etat.violations_depuis(position)) == len(tentatives)


def test_une_lecture_n_est_pas_une_ecriture(faux_reel):
    """Faux positif à exclure : un test peut INVENTORIER le vrai dossier
    (c'est ce que fait `test_hermeticite_voix` pour l'audio)."""
    dossier, position = faux_reel

    assert (dossier / "existant.json").read_text(encoding="utf-8") == "{}"
    assert [p.name for p in dossier.iterdir()] == ["existant.json"]
    assert garde_etat.violations_depuis(position) == []


def test_une_ecriture_avalee_par_le_code_est_quand_meme_consignee(faux_reel):
    """Le cas réel : `ConversationMemory.save` rattrape l'`OSError` et journalise.
    Le refus seul laisserait le test vert — c'est la consignation que la fixture
    autouse relit après chaque test qui le fait rougir."""
    from agent.memory import ConversationMemory

    dossier, position = faux_reel
    mem = ConversationMemory()
    mem.memory_file = dossier / "memory_avalee.json"

    mem.add_message("user", "dis bonjour")  # ne lève pas : save() avale

    assert not mem.memory_file.exists()
    [violation] = garde_etat.violations_depuis(position)
    assert "memory_avalee.json" in violation


# --- de bout en bout : un test fautif ROUGIT ----------------------------------


def test_un_test_qui_ecrit_dans_le_vrai_dossier_fait_rougir_la_suite(tmp_path):
    """La fixture autouse, pas un double : on lance une vraie session pytest avec
    `tests/conftest.py` chargé comme plugin, sous un `HOME` jetable — le « vrai »
    dossier y est `<tmp>/.klody/data`, jamais celui de l'utilisateur.

    Deux tests : l'un propre (doit passer — sinon l'échec viendrait du harnais,
    pas du garde), l'autre qui écrit en avalant l'erreur, comme `save()`.
    """
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "test_fautif.py").write_text(textwrap.dedent("""
        from pathlib import Path

        def test_propre(tmp_path):
            (tmp_path / "ok.json").write_text("{}")

        def test_ecrit_en_douce():
            cible = Path.home() / ".klody" / "data" / "memory_canari.json"
            try:
                cible.write_text("{}")
            except OSError:
                pass  # exactement ce que fait ConversationMemory.save
    """), encoding="utf-8")
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("KLODY_DATA_DIR", "SEMANTIC_MEMORY_DB", "_KLODY_TESTS_DATA_DIR")
    }
    env |= {"HOME": str(home), "PYTHONPATH": str(REPO)}

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.conftest", "-p", "no:cacheprovider",
         "-q", "--no-header", str(tmp_path / "test_fautif.py")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    sortie = proc.stdout + proc.stderr

    assert proc.returncode == 1, sortie
    # Le corps du test fautif PASSE (l'erreur est avalée) : c'est le démontage
    # qui le fait tomber — pytest le compte donc « passed » ET « error ».
    assert "1 error" in sortie, sortie
    assert "ERROR test_fautif.py::test_ecrit_en_douce" in sortie, sortie
    assert "ERROR test_fautif.py::test_propre" not in sortie, sortie
    assert "memory_canari.json" in sortie, sortie
    assert not (home / ".klody" / "data" / "memory_canari.json").exists()
