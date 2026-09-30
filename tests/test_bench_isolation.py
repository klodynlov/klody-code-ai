"""Isolation par processus du bench (bench/run.py).

Le bug d'origine, mesuré le 2026-07-29 : toutes les tâches tournaient dans UN
processus. `FileManager.allowed_roots` étant figé dans `__init__` et `_run_klody`
ne repatchant que `.root`, les tâches 2..N gardaient les racines d'un workdir
déjà supprimé. L'agent répondait « hors des racines autorisées » et dictait le
code au lieu de l'écrire — pendant que `write_file` affichait « ✏️ Modifié ».

Symptôme mesurable : la MÊME tâche rendait ✅ à la 1ʳᵉ passe et ❌ à la 2ᵉ.
La catégorie `easy` sortait à 1/5 en lot pour 5/5 tâche par tâche, si bien que
le banc mesurait sa propre fuite d'état et non le modèle.

Ces tests verrouillent le remède : une tâche = un processus neuf. Ils ne
touchent ni la gateway ni le modèle — tout est vérifié à la couture
`subprocess.run`, sinon la suite prendrait des minutes et exigerait un LLM.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from bench import run as bench_run
from bench.framework import Result

from tests import garde_etat


class TacheFactice:
    """Suffit à `_run_one`, qui ne lit que `.id` et `.category` de la classe."""

    id = "easy/factice"
    category = "easy"


def _result(**overrides) -> Result:
    champs = {
        "task_id": "easy/factice",
        "category": "easy",
        "success": True,
        "detail": "ok",
        "latency_s": 1.5,
        "tokens_generated": 10,
        "tokens_per_sec": 6.7,
        "tool_calls_total": 2,
        "tool_calls_broken": 0,
        "iterations": 3,
        "error": None,
    }
    return Result(**{**champs, **overrides})


def _arg(cmd: list[str], nom: str) -> str:
    """Valeur de `--nom` dans la ligne de commande du fils."""
    return cmd[cmd.index(nom) + 1]


@pytest.fixture(autouse=True)
def _pas_d_echappatoire(monkeypatch):
    """BENCH_ISOLATION peut traîner dans l'env du développeur — les tests qui
    veulent l'échappatoire la posent eux-mêmes.

    `KLODY_SOURCE` aussi, et `main()` la POSE : retirée au démontage, sinon le
    `system` d'un test d'ici déclarerait machinerie le reste de la suite.
    (`monkeypatch.delenv` sur une variable absente n'enregistre rien à
    restaurer, d'où le `pop` explicite.)"""
    monkeypatch.delenv("BENCH_ISOLATION", raising=False)
    monkeypatch.delenv("KLODY_SOURCE", raising=False)
    yield
    os.environ.pop("KLODY_SOURCE", None)


# --- le sous-processus est le chemin par défaut ------------------------------


def test_run_one_delegue_a_un_sous_processus(monkeypatch):
    """LE test de non-régression : exécuter la tâche dans le processus courant
    est exactement ce qui rendait le banc faux."""
    appels = []

    def faux_run(cmd, **kwargs):
        appels.append((cmd, kwargs))
        with open(_arg(cmd, "--child-out"), "w", encoding="utf-8") as f:
            json.dump(_result(detail="venu du fils").__dict__, f)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bench_run.subprocess, "run", faux_run)
    monkeypatch.setattr(
        bench_run,
        "_run_one_inprocess",
        lambda _: pytest.fail("la tâche a tourné dans le processus du parent"),
    )

    r = bench_run._run_one(TacheFactice)

    assert len(appels) == 1
    assert r.success is True
    assert r.detail == "venu du fils"


def test_le_fils_recoit_l_id_de_la_tache_et_un_chemin_de_sortie(monkeypatch):
    cmd_vue = []

    def faux_run(cmd, **kwargs):
        cmd_vue.append(cmd)
        with open(_arg(cmd, "--child-out"), "w", encoding="utf-8") as f:
            json.dump(_result().__dict__, f)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bench_run.subprocess, "run", faux_run)
    bench_run._run_one(TacheFactice)

    cmd = cmd_vue[0]
    assert _arg(cmd, "--child-task") == "easy/factice"
    assert _arg(cmd, "--child-out").endswith(".json")
    # `sys.executable` et pas « python » : sous un venv, un interpréteur nu ne
    # verrait ni les dépendances du projet ni le modèle servi.
    assert cmd[0] == sys.executable
    assert cmd[1:3] == ["-m", "bench.run"]


def test_le_fils_tourne_depuis_la_racine_du_depot(monkeypatch):
    """Sans `cwd`, `python -m bench.run` dépend du dossier courant de l'appelant."""
    vu = {}

    def faux_run(cmd, **kwargs):
        vu.update(kwargs)
        with open(_arg(cmd, "--child-out"), "w", encoding="utf-8") as f:
            json.dump(_result().__dict__, f)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bench_run.subprocess, "run", faux_run)
    bench_run._run_one(TacheFactice)

    assert vu["cwd"] == bench_run.REPO_ROOT


def test_bench_isolation_0_revient_au_processus_partage(monkeypatch):
    """Échappatoire de débogage — elle doit rester explicite et opt-in."""
    monkeypatch.setenv("BENCH_ISOLATION", "0")
    monkeypatch.setattr(
        bench_run.subprocess,
        "run",
        lambda *a, **k: pytest.fail("BENCH_ISOLATION=0 doit éviter le sous-processus"),
    )
    monkeypatch.setattr(bench_run, "_run_one_inprocess", lambda _: _result(detail="en direct"))

    assert bench_run._run_one(TacheFactice).detail == "en direct"


# --- un fils qui meurt ne doit pas passer pour un succès ---------------------


def test_fils_mort_sans_resultat_rend_un_echec_qui_nomme_le_code(monkeypatch):
    """Une dégradation silencieuse serait le pire des deux mondes : ni le
    résultat, ni l'erreur qui le signale."""
    monkeypatch.setattr(
        bench_run.subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 3),  # n'écrit rien
    )

    r = bench_run._run_one(TacheFactice)

    assert r.success is False
    assert r.task_id == "easy/factice"
    assert "3" in r.detail
    assert "easy/factice" in r.error


def test_fils_expire_rend_un_echec_qui_nomme_le_delai(monkeypatch):
    def faux_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    monkeypatch.setattr(bench_run.subprocess, "run", faux_run)
    monkeypatch.setattr(bench_run, "_TASK_TIMEOUT_S", 42)

    r = bench_run._run_one(TacheFactice)

    assert r.success is False
    assert "42" in r.detail


def test_resultat_illisible_rend_un_echec_et_pas_une_exception(monkeypatch):
    def faux_run(cmd, **kwargs):
        with open(_arg(cmd, "--child-out"), "w", encoding="utf-8") as f:
            f.write("{ pas du json")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bench_run.subprocess, "run", faux_run)

    r = bench_run._run_one(TacheFactice)

    assert r.success is False
    assert r.error


# --- le canal parent ↔ fils ne perd aucun champ ------------------------------


def test_le_mode_fils_ecrit_tous_les_champs_de_result(monkeypatch, tmp_path):
    """Le parent reconstruit par `Result(**payload)`. Un champ oublié à
    l'écriture ferait lever le parent sur une tâche par ailleurs réussie."""
    monkeypatch.setattr(bench_run.metrics, "install_patches", lambda: True)
    attendu = _result(task_id="easy/rename_var", detail="rename complet", iterations=7)
    monkeypatch.setattr(bench_run, "_run_one_inprocess", lambda _: attendu)
    sortie = tmp_path / "result.json"

    code = bench_run.main(
        ["--child-task", "easy/rename_var", "--child-out", str(sortie),
         "--child-data-dir", str(tmp_path / "etat")]
    )

    assert code == 0
    charge = json.loads(sortie.read_text(encoding="utf-8"))
    assert set(charge) == {f.name for f in dataclasses.fields(Result)}
    assert Result(**charge) == attendu


def test_le_mode_fils_n_ecrit_rien_dans_results(monkeypatch, tmp_path):
    """Un fils qui écrirait latest.json — ou pire, baseline.json — écraserait
    le run du parent vingt fois de suite."""
    monkeypatch.setattr(bench_run.metrics, "install_patches", lambda: True)
    monkeypatch.setattr(bench_run, "_run_one_inprocess", lambda _: _result())
    resultats = tmp_path / "results"
    resultats.mkdir()
    monkeypatch.setattr(bench_run, "RESULTS_DIR", resultats)

    code = bench_run.main(
        ["--child-task", "easy/rename_var", "--child-out", str(tmp_path / "r.json"),
         "--child-data-dir", str(tmp_path / "etat")]
    )

    # Sans ce code, un fils qui refuserait d'emblée rendrait ce test vert à vide.
    assert code == 0
    assert list(resultats.iterdir()) == []


def test_le_mode_fils_refuse_une_tache_inconnue(monkeypatch, tmp_path):
    monkeypatch.setattr(bench_run.metrics, "install_patches", lambda: True)
    monkeypatch.setattr(
        bench_run,
        "_run_one_inprocess",
        lambda _: pytest.fail("aucune tâche ne devait être exécutée"),
    )

    code = bench_run.main(
        ["--child-task", "easy/inexistante", "--child-out", str(tmp_path / "r.json"),
         "--child-data-dir", str(tmp_path / "etat")]
    )

    assert code == 1


def test_le_mode_fils_exige_un_chemin_de_sortie():
    assert bench_run.main(["--child-task", "easy/rename_var"]) == 1


# --- une tâche = un état neuf ---------------------------------------------------
#
# Constaté le 2026-09-27 : 1 236 sessions du banc dans ~/.klody/data, le vrai
# dossier d'état de l'utilisateur, et un prompt de banc qui injectait SON profil
# et SA mémoire long terme. Cf. le commentaire de `_isoler_etat`.


def test_chaque_fils_recoit_un_dossier_d_etat_jetable_et_distinct(monkeypatch):
    vus = []

    def faux_run(cmd, **kwargs):
        etat = _arg(cmd, "--child-data-dir")
        vus.append((etat, os.path.isdir(etat), os.listdir(etat)))
        with open(_arg(cmd, "--child-out"), "w", encoding="utf-8") as f:
            json.dump(_result().__dict__, f)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bench_run.subprocess, "run", faux_run)
    bench_run._run_one(TacheFactice)
    bench_run._run_one(TacheFactice)

    (etat_1, existe_1, contenu_1), (etat_2, _, _) = vus
    assert existe_1 and contenu_1 == [], "le fils doit trouver un dossier VIDE"
    assert etat_1 != etat_2, "deux tâches ne partagent pas leur état"
    assert not garde_etat.est_protege(etat_1)
    # Dans le dossier jetable de la tâche : il part avec elle.
    assert not os.path.exists(etat_1)


def test_le_mode_fils_exige_un_dossier_d_etat(monkeypatch, tmp_path):
    """Refuser plutôt que retomber sur ~/.klody/data : un parent qui oublierait
    l'argument rendrait des tâches en échec, pas une pollution muette."""
    monkeypatch.setattr(bench_run.metrics, "install_patches", lambda: True)
    monkeypatch.setattr(
        bench_run,
        "_run_one_inprocess",
        lambda _: pytest.fail("la tâche a tourné sans dossier d'état"),
    )

    code = bench_run.main(
        ["--child-task", "easy/rename_var", "--child-out", str(tmp_path / "r.json")]
    )

    assert code == 1


def test_le_mode_fils_redirige_l_etat_avant_la_tache(monkeypatch, tmp_path):
    vu = {}

    def espion_patches():
        # install_patches importe config : la redirection doit la PRÉCÉDER.
        vu["patches"] = os.environ["KLODY_DATA_DIR"]
        return True

    def espion_tache(_):
        vu["tache"] = (os.environ["KLODY_DATA_DIR"], os.environ["SEMANTIC_MEMORY_DB"])
        return _result(task_id="easy/rename_var")

    monkeypatch.setattr(bench_run.metrics, "install_patches", espion_patches)
    monkeypatch.setattr(bench_run, "_run_one_inprocess", espion_tache)
    etat = tmp_path / "etat"

    bench_run.main(
        ["--child-task", "easy/rename_var", "--child-out", str(tmp_path / "r.json"),
         "--child-data-dir", str(etat)]
    )

    assert vu["patches"] == str(etat)
    assert vu["tache"] == (str(etat), str(etat / "semantic_memory.db"))


def test_un_vrai_fils_ne_voit_aucun_chemin_d_etat_hors_de_son_dossier(tmp_path):
    """Dans un interpréteur NEUF, comme le vrai fils : les modules qui figent
    leur chemin à l'import (`_STORAGE`, `_PROFILE_FILE`) doivent le prendre
    dans le dossier de la tâche. Un monkeypatch de config.MEMORY_DIR ne les
    aurait pas atteints — d'où la redirection par l'environnement."""
    etat = tmp_path / "etat"
    etat.mkdir()
    env = {k: v for k, v in os.environ.items()
           if k not in ("KLODY_DATA_DIR", "SEMANTIC_MEMORY_DB")}
    env["HOME"] = str(tmp_path / "home")  # le défaut ne pointe jamais sur le vrai
    code = textwrap.dedent(f"""
        import json, sys
        from pathlib import Path
        from bench import run
        run._isoler_etat(Path({str(etat)!r}))
        import config, agent.long_term_memory as ltm, agent.profiler as prof
        print(json.dumps([str(config.MEMORY_DIR), str(config.SEMANTIC_MEMORY_DB),
                          str(ltm._STORAGE), str(prof._PROFILE_FILE)]))
    """)

    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=bench_run.REPO_ROOT, env=env,
        capture_output=True, text=True, timeout=120, check=True,
    )

    chemins = json.loads(proc.stdout.strip().splitlines()[-1])
    assert all(c.startswith(str(etat)) for c in chemins), chemins


def test_bench_isolation_0_isole_aussi_l_etat(monkeypatch, tmp_path):
    """Le mode de débogage exécute l'agent dans le parent : sans ce dossier,
    c'est là que les sessions rejoindraient l'historique de l'utilisateur."""
    monkeypatch.setenv("BENCH_ISOLATION", "0")
    monkeypatch.setattr(bench_run, "_TMP_ROOT", str(tmp_path))
    monkeypatch.setattr(bench_run, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(bench_run.provenance, "describe_config", lambda: {})
    monkeypatch.setattr(bench_run.provenance, "describe_short", lambda _m: "")
    vu = {}

    def espion_patches():
        vu["etat"] = os.environ["KLODY_DATA_DIR"]
        return True

    monkeypatch.setattr(bench_run.metrics, "install_patches", espion_patches)
    monkeypatch.setattr(bench_run, "_run_one", lambda cls: _result(task_id=cls.id))

    bench_run.main(["--task", "easy/rename_var", "--label", "t"])

    etat = Path(vu["etat"])
    assert etat.parent == tmp_path and etat.name.startswith("kb-etat-")
    assert not garde_etat.est_protege(etat)


# --- le banc ne se fait pas passer pour l'utilisateur ------------------------
#
# Le fils appelle le gateway de PROD :8090, dont klody-core tient le journal
# d'usage ; le miner d'habitudes n'y mine que `source='user'`. Relevé le
# 2026-09-30 : la promotion de la baseline du 2026-09-28 y figure pour 1 554
# événements `llm` et 1 772 `tool` classés `user`. Cf. `_declarer_machinerie`.

_SONDE_FILS = textwrap.dedent("""
    import json
    from agent import journal_client
    from agent.llm import LLMClient
    print(json.dumps({
        "llm": LLMClient().client.default_headers.get("X-Klody-Source"),
        "evenements": journal_client.source(),
    }))
""")


def _parent_sans_reseau(monkeypatch, tmp_path) -> None:
    """Le parent jusqu'à `_run_one`, sans provenance ni fichiers de résultats réels."""
    monkeypatch.setattr(bench_run, "_TMP_ROOT", str(tmp_path))
    monkeypatch.setattr(bench_run, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(bench_run.provenance, "describe_config", lambda: {})
    monkeypatch.setattr(bench_run.provenance, "describe_short", lambda _m: "")
    monkeypatch.setattr(bench_run.metrics, "install_patches", lambda: True)


def test_les_appels_d_un_vrai_fils_se_declarent_machinerie(monkeypatch, tmp_path):
    """LE test de non-régression. À la couture `subprocess.run`, un interpréteur
    NEUF reçoit exactement l'environnement dont le vrai fils hériterait, et y
    construit le VRAI client LLM : c'est son `default_headers` que le gateway
    lit pour classer les tours `llm`. Sans le correctif, l'en-tête est absent
    et le gateway dérive `user` de `X-Klody-App: klody-ai`."""
    vrai_run = subprocess.run
    vus = []

    def faux_run(cmd, **kwargs):
        env = dict(kwargs.get("env") or os.environ)   # ce que le fils hérite
        env["HOME"] = str(tmp_path / "home")          # jamais le vrai ~/.klody
        sonde = vrai_run(
            [sys.executable, "-c", _SONDE_FILS], cwd=kwargs.get("cwd"), env=env,
            capture_output=True, text=True, timeout=120, check=True,
        )
        vus.append(json.loads(sonde.stdout.strip().splitlines()[-1]))
        with open(_arg(cmd, "--child-out"), "w", encoding="utf-8") as f:
            json.dump(_result(task_id="easy/rename_var").__dict__, f)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bench_run.subprocess, "run", faux_run)
    _parent_sans_reseau(monkeypatch, tmp_path)

    assert bench_run.main(["--task", "easy/rename_var", "--label", "t"]) == 0

    assert vus == [{"llm": "system", "evenements": "system"}]


@pytest.mark.parametrize(("posee", "attendue"), [
    ("user", "user"),        # l'opérateur qui veut compter son run garde la main
    ("test", "test"),
    ("", "system"),          # `KLODY_SOURCE=` dans un .env n'est pas un choix
])
def test_une_source_deja_posee_est_respectee(monkeypatch, tmp_path, posee, attendue):
    vu = {}

    def faux_run(cmd, **kwargs):
        vu["source"] = (kwargs.get("env") or os.environ).get("KLODY_SOURCE")
        with open(_arg(cmd, "--child-out"), "w", encoding="utf-8") as f:
            json.dump(_result(task_id="easy/rename_var").__dict__, f)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setenv("KLODY_SOURCE", posee)
    monkeypatch.setattr(bench_run.subprocess, "run", faux_run)
    _parent_sans_reseau(monkeypatch, tmp_path)

    bench_run.main(["--task", "easy/rename_var", "--label", "t"])

    assert vu["source"] == attendue


def test_un_fils_lance_a_la_main_se_declare_aussi(monkeypatch, tmp_path):
    """`python -m bench.run --child-task …` sans parent (débogage d'une tâche)."""
    vu = {}

    def espion_tache(_):
        vu["source"] = os.environ.get("KLODY_SOURCE")
        return _result(task_id="easy/rename_var")

    monkeypatch.setattr(bench_run.metrics, "install_patches", lambda: True)
    monkeypatch.setattr(bench_run, "_run_one_inprocess", espion_tache)

    bench_run.main(
        ["--child-task", "easy/rename_var", "--child-out", str(tmp_path / "r.json"),
         "--child-data-dir", str(tmp_path / "etat")]
    )

    assert vu["source"] == "system"


def test_importer_le_banc_ne_declare_rien(tmp_path):
    """La suite importe `bench.run` : une déclaration à l'import aurait classé
    `system` tout processus qui l'importe. Interpréteur neuf, variable absente."""
    env = {k: v for k, v in os.environ.items() if k != "KLODY_SOURCE"}
    env["HOME"] = str(tmp_path / "home")
    proc = subprocess.run(
        [sys.executable, "-c",
         "import os; from bench import run; print(os.environ.get('KLODY_SOURCE'))"],
        cwd=bench_run.REPO_ROOT, env=env,
        capture_output=True, text=True, timeout=120, check=True,
    )

    assert proc.stdout.strip().splitlines()[-1] == "None"
