"""scripts/etat_pollue.py — trier les sessions de test/banc sans jamais emporter une vraie.

Le cas qui a dicté la règle, trouvé dans le vrai dossier le 2026-09-27 : une
session RÉELLE ouverte sur « explique en raisonnant » — chaîne présente dans
`test_websocket_chat.py` — et suivie d'une vraie réponse du modèle. Un tri sur le
seul premier message l'aurait déplacée. Ces tests verrouillent qu'elle reste.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts import etat_pollue as ep


def _session(*messages, duree_s: float = 0.02) -> dict:
    return {
        "session_id": "abcd1234",
        "title": "",
        "archived": False,
        "created_at": "2026-09-27T10:00:00",
        "updated_at": f"2026-09-27T10:00:{duree_s:09.6f}" if duree_s < 60 else "2026-09-27T10:05:00",
        "messages": [{"role": "system", "content": "Tu es Klody."}, *messages],
    }


def _u(texte):
    return {"role": "user", "content": texte}


def _a(texte=None, **extra):
    return {"role": "assistant", "content": texte, **extra}


@pytest.fixture
def depot(tmp_path):
    """Un faux `tests/` : les littéraux sont lus dans le code ET les fixtures JSON."""
    tests = tmp_path / "tests"
    (tests / "fixtures").mkdir(parents=True)
    (tests / "test_x.py").write_text(
        'PROMPT = "explique en raisonnant"\nREPONSE = "Bonjour je suis Klody"\n',
        encoding="utf-8",
    )
    (tests / "fixtures" / "01.json").write_text(
        json.dumps({"user_prompt": "Lis pi.txt.", "llm": [{"content": "Pi vaut 3,14."}]}),
        encoding="utf-8",
    )
    donnees = tmp_path / "data"
    donnees.mkdir()
    return tests, donnees


def _ecrire(donnees: Path, nom: str, session: dict) -> Path:
    f = donnees / f"memory_{nom}.json"
    f.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    return f


def _classer(depot, session):
    tests, _ = depot
    litteraux = ep.litteraux_des_tests(tests)
    return ep.classer(session, litteraux, frozenset(ep._compacte(s) for s in litteraux))


# --- ce qui part -------------------------------------------------------------


@pytest.mark.parametrize("chemin", ["/private/tmp/kb-3owvratp", "/var/folders/6d/x/T/klody-bench-jhqyn5eg"])
def test_le_preambule_du_banc_signe_une_session_du_banc(depot, chemin):
    enonce = (
        f"[Répertoire de travail : {chemin}]\n"
        "Les fichiers sont dans ce répertoire. Utilise read_file pour lire.\n\n"
        "Renomme la variable."
    )
    assert _classer(depot, _session(_u(enonce), _a("Fait."), duree_s=40)) == "banc"


def test_messages_et_reponses_scriptes_signent_une_session_de_test(depot):
    # Le faux client streame des tokens SANS espaces : « Bonjourjesuis Klody ».
    s = _session(_u("explique en raisonnant"), _a("BonjourjesuisKlody"))
    assert _classer(depot, s) == "test"


def test_les_messages_fabriques_par_l_orchestrateur_ne_sauvent_pas_un_test(depot):
    s = _session(
        _u("Lis pi.txt."),
        _a(None, tool_calls=[{"function": {"name": "read_file"}}]),
        _a("[Système] Tool fallback exécuté : preview_code\nAperçu créé"),
        _a("Pi vaut 3,14."),
    )
    assert _classer(depot, s) == "test"


# --- ce qui reste ------------------------------------------------------------


def test_une_vraie_reponse_du_modele_garde_la_session(depot):
    """LE cas réel : premier message identique à un test, réponse d'un vrai modèle."""
    s = _session(
        _u("explique en raisonnant"),
        _a("C'est une situation qui nécessite une attention médicale immédiate."),
        duree_s=35,
    )
    assert _classer(depot, s) is None


def test_un_premier_message_hors_des_tests_garde_la_session(depot):
    assert _classer(depot, _session(_u("salut klody"), _a("BonjourjesuisKlody"))) is None


def test_une_session_sans_reponse_est_gardee(depot):
    """Un « salut » réel tombé sur un 503 n'a aucune réponse : on ne tranche pas."""
    assert _classer(depot, _session(_u("explique en raisonnant"))) is None


def test_une_session_longue_est_gardee(depot):
    s = _session(_u("explique en raisonnant"), _a("BonjourjesuisKlody"), duree_s=300)
    assert _classer(depot, s) is None


def test_une_session_sans_dates_est_gardee(depot):
    s = _session(_u("explique en raisonnant"), _a("BonjourjesuisKlody"))
    del s["created_at"]
    assert _classer(depot, s) is None


# --- le script ----------------------------------------------------------------


def test_l_inventaire_ne_deplace_rien(depot, capsys, monkeypatch):
    tests, donnees = depot
    _ecrire(donnees, "t1", _session(_u("explique en raisonnant"), _a("BonjourjesuisKlody")))
    _ecrire(donnees, "r1", _session(_u("explique en raisonnant"), _a("Vraie réponse."), duree_s=30))
    avant = sorted(p.name for p in donnees.iterdir())
    monkeypatch.setattr(ep, "api_ecoute", lambda *a: pytest.fail("l'inventaire ne sonde pas l'API"))

    code = ep.main(["--dossier", str(donnees), "--tests", str(tests)])

    assert code == 0
    assert sorted(p.name for p in donnees.iterdir()) == avant
    sortie = capsys.readouterr().out
    assert "1  tests" in sortie and "1  gardées" in sortie


def test_la_quarantaine_deplace_ecrit_un_manifeste_et_garde_le_reel(depot, tmp_path, monkeypatch):
    tests, donnees = depot
    test = _ecrire(donnees, "t1", _session(_u("explique en raisonnant"), _a("BonjourjesuisKlody")))
    reel = _ecrire(donnees, "r1", _session(_u("explique en raisonnant"), _a("Vraie réponse."), duree_s=30))
    (donnees / "long_term.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(ep, "api_ecoute", lambda *a: False)
    cible = tmp_path / "quarantaine"

    code = ep.main(["--dossier", str(donnees), "--tests", str(tests), "--quarantaine", str(cible)])

    assert code == 0
    assert reel.exists() and (donnees / "long_term.json").exists()
    assert not test.exists()
    assert (cible / "test" / test.name).exists()
    [ligne] = (cible / "manifeste.tsv").read_text(encoding="utf-8").splitlines()
    categorie, origine, destination, _premier = ligne.split("\t")
    assert (categorie, origine, destination) == ("test", str(test), str(cible / "test" / test.name))


def test_la_quarantaine_refuse_tant_que_l_api_ecoute(depot, tmp_path, monkeypatch, capsys):
    """Règle de bascule : arrêter l'écrivain avant de déplacer son état."""
    tests, donnees = depot
    test = _ecrire(donnees, "t1", _session(_u("explique en raisonnant"), _a("BonjourjesuisKlody")))
    monkeypatch.setattr(ep, "api_ecoute", lambda *a: True)

    code = ep.main(["--dossier", str(donnees), "--tests", str(tests),
                    "--quarantaine", str(tmp_path / "q")])

    assert code == 1
    assert test.exists()
    assert "launchctl bootout" in capsys.readouterr().out


def test_la_quarantaine_refuse_d_etre_dans_le_dossier_inspecte(depot, monkeypatch):
    tests, donnees = depot
    _ecrire(donnees, "t1", _session(_u("explique en raisonnant"), _a("BonjourjesuisKlody")))
    monkeypatch.setattr(ep, "api_ecoute", lambda *a: False)

    code = ep.main(["--dossier", str(donnees), "--tests", str(tests),
                    "--quarantaine", str(donnees / "q")])

    assert code == 1
    assert (donnees / "memory_t1.json").exists()


def test_sans_litteraux_le_script_refuse_de_juger(depot, tmp_path):
    """Zéro chaîne lue ⇒ zéro session « de test » : ce serait un faux vert."""
    _, donnees = depot
    vide = tmp_path / "tests-vides"
    vide.mkdir()
    assert ep.main(["--dossier", str(donnees), "--tests", str(vide)]) == 2


def test_dossier_absent(tmp_path):
    assert ep.main(["--dossier", str(tmp_path / "absent")]) == 2
