"""La veille nightly doit pouvoir ROUGIR — sinon elle est indiscernable du vert.

Le nightly bench est le seul juge du projet. S'il est muet, personne ne le
voit — un garde-fou incapable de rougir. Trois propriétés se verrouillent :

1. « n'a pas pu interroger » rend 1, « a interrogé et tout va bien » rend 0 ;
2. un silence prolongé (aucun run vert > MUETTE_JOURS) se DÉNONCE par notification ;
3. le seuil MUETTE_JOURS est verrouillé sur un littéral — la mutation
   MUETTE_JOURS → 99999 a déjà échappé une fois sur la veille Qwen.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import UTC

from scripts import veille_nightly as mod


@pytest.fixture
def etat(tmp_path, monkeypatch):
    fichier = tmp_path / "veille-nightly.json"
    monkeypatch.setattr(mod, "ETAT_DIR", tmp_path)
    monkeypatch.setattr(mod, "ETAT_FICHIER", fichier)
    return fichier


@pytest.fixture
def notifications(monkeypatch):
    envoyees: list[tuple[str, str]] = []
    monkeypatch.setattr(
        mod, "notifier", lambda titre, corps: (envoyees.append((titre, corps)), True)[1]
    )
    return envoyees


def _runs_verts(n: int, age_jours: float = 0.5) -> list[dict]:
    """Fabrique `n` runs verts datés de `age_jours` dans le passé."""
    from datetime import datetime, timedelta, timezone
    dt = datetime.now(UTC) - timedelta(days=age_jours)
    return [
        {"status": "completed", "conclusion": "success",
         "startedAt": dt.isoformat(), "databaseId": 1000 + i}
        for i in range(n)
    ]


def _runs_annules(n: int, age_jours: float = 0.5) -> list[dict]:
    from datetime import datetime, timedelta, timezone
    dt = datetime.now(UTC) - timedelta(days=age_jours)
    return [
        {"status": "completed", "conclusion": "cancelled",
         "startedAt": dt.isoformat(), "databaseId": 2000 + i}
        for i in range(n)
    ]


def _runs_rouges(n: int, age_jours: float = 0.5) -> list[dict]:
    from datetime import datetime, timedelta, timezone
    dt = datetime.now(UTC) - timedelta(days=age_jours)
    return [
        {"status": "completed", "conclusion": "failure",
         "startedAt": dt.isoformat(), "databaseId": 3000 + i}
        for i in range(n)
    ]


# --- 1. « rien regardé » ≠ « rien trouvé » -----------------------------------


def test_echec_gh_rend_1_pas_0(etat, notifications, monkeypatch):
    """Si gh est injoignable, le script doit rendre 1 (pas pu regarder), pas 0."""
    monkeypatch.setattr(
        mod, "lister_runs",
        lambda: (_ for _ in ()).throw(RuntimeError("gh absent du PATH")),
    )
    code = mod.executer(check_seulement=False)
    assert code == 1, "une interrogation qui n'a RIEN pu regarder doit rendre 1"


def test_echec_gh_notifie_si_muette_longue(etat, notifications, monkeypatch):
    """Après MUETTE_JOURS sans interrogation réussie, le silence se dit."""
    ancien = time.time() - (mod.MUETTE_JOURS + 1) * 86400
    etat.write_text(json.dumps({"dernier_succes": ancien}))
    monkeypatch.setattr(
        mod, "lister_runs",
        lambda: (_ for _ in ()).throw(RuntimeError("réseau mort")),
    )
    mod.executer(check_seulement=False)
    assert notifications, "l'échec prolongé doit notifier"
    assert "MUETTE" in notifications[0][0]


def test_echec_gh_notifie_si_jamais_reussi(etat, notifications, monkeypatch):
    """Aucune interrogation réussie depuis l'installation = notification."""
    monkeypatch.setattr(
        mod, "lister_runs",
        lambda: (_ for _ in ()).throw(RuntimeError("gh absent")),
    )
    mod.executer(check_seulement=False)
    assert notifications, "aucune réussite depuis l'installation doit notifier"
    assert "MUETTE" in notifications[0][0]


# --- 2. nightly muet → notification -------------------------------------------


def test_tout_vert_rend_0_sans_notification(etat, notifications, monkeypatch):
    monkeypatch.setattr(mod, "lister_runs", lambda: _runs_verts(5))
    code = mod.executer(check_seulement=False)
    assert code == 0
    assert not notifications, "tout vert = pas de notification"


def test_aucun_vert_notifie(etat, notifications, monkeypatch):
    monkeypatch.setattr(mod, "lister_runs", lambda: _runs_annules(10))
    code = mod.executer(check_seulement=False)
    assert code == 0
    assert notifications, "aucun run vert doit notifier"
    assert "MUET" in notifications[0][0]


def test_dernier_vert_trop_ancien_notifie(etat, notifications, monkeypatch):
    runs = _runs_annules(8, age_jours=1) + _runs_verts(2, age_jours=5)
    monkeypatch.setattr(mod, "lister_runs", lambda: runs)
    code = mod.executer(check_seulement=False)
    assert code == 0
    assert notifications, "dernier vert > MUETTE_JOURS doit notifier"


def test_dernier_vert_recent_pas_de_notification(etat, notifications, monkeypatch):
    runs = _runs_annules(5, age_jours=0.5) + _runs_verts(2, age_jours=1)
    monkeypatch.setattr(mod, "lister_runs", lambda: runs)
    code = mod.executer(check_seulement=False)
    assert code == 0
    assert not notifications


# --- 3. seuil verrouillé sur un littéral --------------------------------------


def test_seuil_muette_est_3():
    """Le seuil est verrouillé sur un littéral. La mutation MUETTE_JOURS → 99999
    a déjà échappé une fois : le test calculait l'âge depuis la constante elle-même
    et suivait le seuil au lieu de le juger."""
    assert mod.MUETTE_JOURS == 3


def test_silence_de_30_jours_est_detecte(etat, notifications, monkeypatch):
    """Le seuil verrouillé ci-dessus doit avoir un effet : 30 j sans vert
    doit produire une notification. Si MUETTE_JOURS était muté à 99999,
    ce test rougirait — c'est le test que la veille Qwen n'avait pas."""
    runs = _runs_annules(10, age_jours=30)
    monkeypatch.setattr(mod, "lister_runs", lambda: runs)
    mod.executer(check_seulement=False)
    assert notifications, "30 j sans vert doit notifier"


# --- 4. check mode ne modifie pas l'état --------------------------------------


def test_check_ne_notifie_pas(etat, notifications, monkeypatch):
    monkeypatch.setattr(mod, "lister_runs", lambda: _runs_annules(10))
    mod.executer(check_seulement=True)
    assert not notifications, "--check ne doit jamais notifier"


def test_check_ne_ecrit_pas(etat, notifications, monkeypatch):
    monkeypatch.setattr(mod, "lister_runs", lambda: _runs_verts(5))
    mod.executer(check_seulement=True)
    assert not etat.exists(), "--check ne doit pas écrire l'état"


# --- 5. diagnostic structuré --------------------------------------------------


def test_diagnostic_compte_correctement(monkeypatch):
    runs = _runs_verts(3, age_jours=1) + _runs_rouges(2) + _runs_annules(4)
    diag = mod.diagnostiquer(runs)
    assert diag["verts"] == 3
    assert diag["rouges"] == 2
    assert diag["annules"] == 4
    assert diag["total"] == 9
    assert diag["dernier_vert"] is not None
