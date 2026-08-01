"""/status ne doit sonder que des services RÉELLEMENT utilisés.

Le défaut réparé : la commande interrogeait Ollama inconditionnellement et
affichait « ✗ hors ligne » en `BACKEND=mlx`, où Ollama n'est ni le backend LLM
ni le fournisseur d'embeddings (`SEMANTIC_MEMORY_PROVIDER=st` par défaut,
sentence-transformers en processus, `cos(ollama, st) = 1.0000` mesuré).

Un avertissement qui décrit un problème inexistant coûte autant qu'une erreur
fausse : le 2026-07-30, un faux « Ollama injoignable » en tête de log a orienté
tout le diagnostic d'un 1/5 vers Ollama, alors que la cause était
`MLX_CODE_BASE_URL`.

Second contrat verrouillé ici : la sonde est une sonde de DISPONIBILITÉ. Elle
n'envoie jamais de complétion — celle-ci peut déclencher le chargement des 44 Go
de `brain` ou se faire refuser en 503, soit très exactement le préflight du
nightly qui fabriquait la condition qu'il prétendait détecter.
"""
from __future__ import annotations

import io

import httpx
import main
import pytest
from rich.console import Console


class _MemoireFactice:
    session_id = "test1234"
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


class _LLMFactice:
    model = "brain"
    total_tokens = 1234


class _OrchestrateurFactice:
    def __init__(self) -> None:
        self.llm = _LLMFactice()
        self.memory = _MemoireFactice()


@pytest.fixture
def ecran(monkeypatch):
    """Console capturée + LibraryBrain neutralisé (sinon /status part en réseau)."""
    tampon = io.StringIO()
    monkeypatch.setattr(main, "console", Console(file=tampon, width=140, no_color=True))
    monkeypatch.setattr(main, "get_librarybrain_status", lambda: {"up": False, "restarts": 0})
    return tampon


@pytest.fixture
def sonde(monkeypatch):
    """Bouchonne httpx.get et ENREGISTRE les URL réellement appelées."""
    appels: list[str] = []

    def _poser(*, status: int = 200, charge: dict | None = None, tombe: bool = False):
        def _faux_get(url, **_kwargs):
            appels.append(str(url))
            if tombe:
                raise httpx.ConnectError("connexion refusée")
            return httpx.Response(
                status,
                json=charge if charge is not None else {"models": [{"name": "a"}, {"name": "b"}]},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(httpx, "get", _faux_get)
        return appels

    return _poser


def _status(ecran) -> str:
    assert main.handle_special_command("/status", _OrchestrateurFactice()) is True
    return ecran.getvalue()


class TestModeMLX:
    @pytest.fixture(autouse=True)
    def _mode(self, monkeypatch):
        monkeypatch.setattr(main, "BACKEND", "mlx")
        monkeypatch.setattr(main, "LLM_BASE_URL", "http://localhost:8090/v1")
        monkeypatch.setattr(main, "SEMANTIC_MEMORY_PROVIDER", "st")

    def test_aucune_sonde_vers_ollama(self, ecran, sonde):
        appels = sonde()
        _status(ecran)
        assert appels == ["http://localhost:8090/v1/models"]
        assert not any("11434" in u or "api/tags" in u for u in appels)

    def test_ollama_absent_de_l_ecran(self, ecran, sonde):
        sonde()
        assert "Ollama" not in _status(ecran)

    def test_le_backend_actif_est_nomme(self, ecran, sonde):
        sonde()
        assert "MLX" in _status(ecran)

    def test_gateway_injoignable_accuse_le_gateway(self, ecran, sonde):
        sonde(tombe=True)
        sortie = _status(ecran)
        assert "hors ligne" in sortie
        assert "gateway" in sortie
        # Le remède ne doit surtout pas être « ollama serve » : c'est le message
        # qui nomme la mauvaise dépendance et coûte une enquête entière.
        assert "ollama serve" not in sortie

    def test_la_sonde_n_envoie_jamais_de_completion(self, ecran, sonde):
        """Une complétion peut charger 44 Go ou rendre 503 — /status ne paie pas ça."""
        appels = sonde()
        _status(ecran)
        assert not any("chat/completions" in u for u in appels)

    def test_embeddings_non_sondes_est_dit_explicitement(self, ecran, sonde):
        """« Je n'ai pas jugé » ne doit pas se lire comme « j'ai jugé, c'est bon »."""
        sonde()
        sortie = _status(ecran)
        assert "non sondé" in sortie
        assert "st" in sortie


class TestModeOllama:
    @pytest.fixture(autouse=True)
    def _mode(self, monkeypatch):
        monkeypatch.setattr(main, "BACKEND", "ollama")
        monkeypatch.setattr(main, "OLLAMA_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setattr(main, "SEMANTIC_MEMORY_PROVIDER", "st")

    def test_sonde_bien_ollama(self, ecran, sonde):
        appels = sonde()
        _status(ecran)
        assert appels == ["http://localhost:11434/api/tags"]

    def test_compte_les_modeles(self, ecran, sonde):
        sonde()
        assert "2 modèle(s)" in _status(ecran)

    def test_hors_ligne_propose_ollama_serve(self, ecran, sonde):
        sonde(tombe=True)
        assert "ollama serve" in _status(ecran)

    def test_json_illisible_ne_casse_pas_status(self, ecran, sonde):
        """Un corps inattendu dégrade le détail, il ne fait pas tomber la commande."""
        sonde(charge={"pas": "ce qu'on attend"})
        assert "en ligne" in _status(ecran)


class TestEmbeddingsParOllama:
    def test_sondes_quand_le_fournisseur_est_ollama(self, ecran, sonde, monkeypatch):
        monkeypatch.setattr(main, "BACKEND", "mlx")
        monkeypatch.setattr(main, "LLM_BASE_URL", "http://localhost:8090/v1")
        monkeypatch.setattr(main, "OLLAMA_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setattr(main, "SEMANTIC_MEMORY_PROVIDER", "ollama")
        appels = sonde()
        sortie = _status(ecran)
        # Ollama redevient légitime à l'écran : il sert vraiment quelque chose.
        assert "http://localhost:11434/api/tags" in appels
        assert "Ollama" in sortie
