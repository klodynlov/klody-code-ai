"""`/health` et `/api/status` doivent DIRE que les dépendances ont bougé.

Le 2026-08-05, l'API a échoué sur chaque requête pendant ~23 h avec tous ses
backends parfaitement joignables : pip avait réécrit `site-packages/openai/`
sous elle (PR #206, bump 2.41.1 → 2.53.0). `/health` répondait 200. C'est ce
200-là que ces tests interdisent de revenir.

⚠️ Aucun de ces tests ne bouchonne `peremption.etat_process` : ils déplacent
l'empreinte de référence et laissent le vrai code lire le vrai `site-packages`
de l'interpréteur. Un stub de la fonction rendrait le test vert même si le
branchement dans `api/server.py` disparaissait.
"""
from __future__ import annotations

import logging

import config
import pytest
from agent import peremption
from api import server
from api.server import app
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """TestClient sans lifespan (pas de bind du port d'aperçu), backends up.

    `TestClient(app)` hors gestionnaire de contexte n'exécute pas le lifespan :
    on sonde les routes sans démarrer le serveur d'aperçu ni rien binder.
    """
    async def _toujours_up(url, timeout=1.5, accept_status=(200,)):
        return True

    monkeypatch.setattr(server, "_probe_url", _toujours_up)
    monkeypatch.setattr(config, "BACKEND", "ollama")
    monkeypatch.setattr(server, "_peremption_signalee", False)
    return TestClient(app)


@pytest.fixture
def deps_perimees(monkeypatch):
    """Rejoue l'incident : l'empreinte du démarrage ne correspond plus au
    disque. Le contenu importe peu — c'est l'ÉCART qui est le signal."""
    monkeypatch.setattr(
        peremption, "EMPREINTE_DEMARRAGE",
        peremption.Empreinte(horodatage=1.0, nb_paquets=1, racines=("/faux",)),
    )


@pytest.fixture
def deps_a_jour(monkeypatch):
    monkeypatch.setattr(peremption, "EMPREINTE_DEMARRAGE", peremption.empreinte_disque())


@pytest.fixture
def deps_non_jugeables(monkeypatch):
    vide = peremption.Empreinte(horodatage=None, nb_paquets=0, racines=())
    monkeypatch.setattr(peremption, "EMPREINTE_DEMARRAGE", vide)
    monkeypatch.setattr(peremption, "empreinte_disque", lambda racines=None: vide)


class TestHealth:
    def test_venv_stable_reste_200(self, client, deps_a_jour):
        r = client.get("/health")
        assert r.status_code == 200
        corps = r.json()
        assert corps["status"] == "ok"
        assert corps["checks"]["dependances"] == peremption.A_JOUR
        assert "dependances_remede" not in corps["checks"]

    def test_dependances_reecrites_sous_le_process_degradent(self, client, deps_perimees):
        """LE test. Backends up, code du dépôt inchangé, et pourtant 503."""
        r = client.get("/health")
        assert r.status_code == 503, "un 200 ici, c'est l'incident qui repasse"
        corps = r.json()
        assert corps["status"] == "degraded"
        assert corps["checks"]["llm_backend"] == "ok", (
            "la dégradation ne vient PAS d'un backend injoignable"
        )
        assert corps["checks"]["dependances"] == peremption.PERIMEES
        assert "path_template" in corps["checks"]["dependances_detail"]
        assert corps["checks"]["dependances_remede"].startswith("launchctl kickstart -k")
        assert "com.klody.api" in corps["checks"]["dependances_remede"]

    def test_non_jugeable_ne_degrade_pas_mais_se_voit(self, client, deps_non_jugeables):
        """« Je n'ai pas pu juger » ne doit ni faire rougir (ce serait une
        fausse alarme) ni se déguiser en « à jour » (ce serait le mode de
        défaillance dominant du dépôt)."""
        r = client.get("/health")
        assert r.status_code == 200
        checks = r.json()["checks"]
        assert checks["dependances"] == peremption.NON_JUGE
        assert checks["dependances"] != peremption.A_JOUR

class TestLeWatchdogNeBouclePasSurUn503:
    """Le 503 de péremption ne doit PAS déclencher de relance automatique.

    `api-watchdog.sh` ignore délibérément le code HTTP — seule l'ABSENCE de
    réponse le fait relancer. Si ce contrat cédait, un venv mis à jour ferait
    `kickstart -k` toutes les deux minutes, donc couperait la session de
    travail en boucle. Le remède doit rester un geste humain.

    ⚠️ On EXÉCUTE le watchdog avec des sondes bouchonnées plutôt que de chercher
    des phrases dans son source : ce dépôt a déjà eu un filet anti-récidive qui
    lisait la prose du fichier et s'est mis à rougir sur les commentaires
    expliquant le correctif.
    """

    def _lancer(self, tmp_path, code_curl: int, passes: int = 3) -> bool:
        """Rend True si le watchdog a émis un `launchctl kickstart`."""
        import os
        import subprocess
        from pathlib import Path

        binaires = tmp_path / "bin"
        binaires.mkdir()
        temoin = tmp_path / "kickstart-emis"
        (binaires / "curl").write_text(f"#!/bin/sh\nexit {code_curl}\n")
        (binaires / "launchctl").write_text(
            f'#!/bin/sh\n[ "$1" = kickstart ] && echo "$@" >> "{temoin}"\nexit 0\n'
        )
        # `sample` et `lsof` ne doivent pas partir sur la vraie machine.
        for muet in ("sample", "lsof"):
            (binaires / muet).write_text("#!/bin/sh\nexit 0\n")
        for f in binaires.iterdir():
            f.chmod(0o755)

        script = Path(__file__).resolve().parent.parent / "scripts" / "api-watchdog.sh"
        for _ in range(passes):
            subprocess.run(
                ["/bin/sh", str(script)], capture_output=True, text=True, timeout=60,
                env=dict(os.environ, HOME=str(tmp_path),
                         PATH=f"{binaires}:{os.environ['PATH']}"),
            )
        return temoin.exists()

    def test_une_reponse_http_meme_503_ne_relance_rien(self, tmp_path):
        """`curl` rend 0 dès qu'une réponse arrive, 503 comprise."""
        assert self._lancer(tmp_path, code_curl=0) is False

    def test_une_absence_de_reponse_relance_bien(self, tmp_path):
        """La moitié qui prouve que le bouchon mord : sans elle, un watchdog
        cassé ferait passer le test précédent."""
        assert self._lancer(tmp_path, code_curl=28) is True


class TestApiStatus:
    def test_status_expose_le_verdict(self, client, deps_a_jour, monkeypatch):
        monkeypatch.setattr(server, "_load_project_info", lambda: {"workdir": "/tmp"})
        monkeypatch.setattr(server, "get_librarybrain_status", lambda: {"up": False})
        r = client.get("/api/status")
        assert r.status_code == 200
        deps = r.json()["dependances"]
        assert deps["statut"] == peremption.A_JOUR
        assert deps["demarrage"]["nb_paquets"] == deps["disque"]["nb_paquets"]

    def test_status_nomme_le_remede_quand_c_est_perime(self, client, deps_perimees,
                                                       monkeypatch):
        monkeypatch.setattr(server, "_load_project_info", lambda: {"workdir": "/tmp"})
        monkeypatch.setattr(server, "get_librarybrain_status", lambda: {"up": False})
        deps = client.get("/api/status").json()["dependances"]
        assert deps["statut"] == peremption.PERIMEES
        assert "com.klody.api" in deps["remede"]


class TestAvertissementUnique:
    """Le WARNING doit sortir UNE fois.

    `/health` est sondé toutes les 60 s par le watchdog : logger à chaque
    passage écrirait 1440 lignes par jour, et un avertissement qu'on apprend à
    sauter des yeux ne vaut pas mieux que pas d'avertissement.
    """

    def test_une_seule_ligne_pour_dix_sondes(self, client, deps_perimees, caplog):
        with caplog.at_level(logging.WARNING, logger="api.server"):
            for _ in range(10):
                client.get("/health")
        lignes = [r for r in caplog.records if "DÉPENDANCES PÉRIMÉES" in r.getMessage()]
        assert len(lignes) == 1, f"{len(lignes)} avertissements pour 10 sondes"
        assert "launchctl kickstart -k" in lignes[0].getMessage()

    def test_rien_du_tout_quand_tout_va_bien(self, client, deps_a_jour, caplog):
        with caplog.at_level(logging.WARNING, logger="api.server"):
            client.get("/health")
        assert not [r for r in caplog.records if "DÉPENDANCES" in r.getMessage()]
