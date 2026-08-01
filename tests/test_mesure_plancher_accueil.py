"""L'instrument de mesure du plancher d'accueil doit savoir ÉCHOUER.

Deux contrats verrouillés ici, tous deux payés cash par le dépôt :

1. **404 et 503 ne disent pas la même chose.** Le préflight du nightly les a
   confondus une journée entière — `curl -sf` rend le même code d'erreur pour
   les deux et `-s … > /dev/null` jette le message qui les sépare. Résultat :
   « l'alias ne résout pas » affiché alors que le resolver allait très bien et
   qu'il manquait 1 Go de RAM.
2. **« Je n'ai pas pu mesurer » n'est pas « j'ai mesuré, c'est bon ».** Un
   instrument qui rend 0 sur un backend injoignable est indiscernable d'un
   instrument vert.
"""
from __future__ import annotations

import httpx
import pytest
from scripts import mesure_plancher_accueil as mod


def _reponse_ok(_requete: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "unsloth/Qwen3.6-35B-A3B-MLX-8bit",
            "choices": [{"message": {"content": "Bonjour !"}}],
            "usage": {"prompt_tokens": 42, "completion_tokens": 7},
        },
    )


@pytest.fixture
def bouchon(monkeypatch):
    """Injecte un MockTransport dans le Client construit par `mesurer`."""
    vrai_client = httpx.Client

    def _poser(handler):
        def _fabrique(**kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return vrai_client(**kwargs)

        monkeypatch.setattr(mod.httpx, "Client", _fabrique)

    return _poser


class TestDiagnostic:
    def test_404_accuse_le_resolver(self):
        message = mod._diagnostic(404, '{"error": "modèle inconnu"}')
        assert "ne résout pas" in message
        # Ne doit surtout PAS parler de RAM : c'est l'inversion du 2026-07-30.
        assert "RAM" not in message

    def test_503_disculpe_le_resolver(self):
        message = mod._diagnostic(503, '{"error": "RAM insuffisante"}')
        assert "reconnu le modèle" in message
        assert "ne résout pas" not in message

    def test_les_deux_diagnostics_sont_distincts(self):
        assert mod._diagnostic(404, "x") != mod._diagnostic(503, "x")

    def test_status_inattendu_reste_lisible(self):
        assert "HTTP 500" in mod._diagnostic(500, "boom")


class TestMesure:
    def test_chemin_nominal(self, bouchon):
        bouchon(_reponse_ok)
        mesure = mod.mesurer(passes=2)
        assert set(mesure["latences_s"]) == {"plancher", "accueil", "avec_outils"}
        assert all(len(v) == 2 for v in mesure["latences_s"].values())
        assert mesure["model_served"] == "unsloth/Qwen3.6-35B-A3B-MLX-8bit"
        assert mesure["nb_outils"] == len(mod.TOOLS)

    @pytest.mark.parametrize("status", [404, 503, 500])
    def test_un_http_non_200_interdit_toute_mesure(self, bouchon, status):
        bouchon(lambda _req: httpx.Response(status, json={"error": "nope"}))
        with pytest.raises(mod.MesureImpossible):
            mod.mesurer(passes=1)

    def test_backend_injoignable_ne_rend_pas_un_chiffre(self, bouchon):
        def _tombe(_requete):
            raise httpx.ConnectError("connexion refusée")

        bouchon(_tombe)
        with pytest.raises(mod.MesureImpossible) as exc:
            mod.mesurer(passes=1)
        # Le message ne doit pas accuser Ollama quand ce n'est pas la cible :
        # « Impossible de joindre Ollama » a coûté une enquête entière.
        assert "PAS Ollama" in str(exc.value)

    def test_le_bras_avec_outils_envoie_bien_les_schemas(self, bouchon):
        """Sinon le troisième bras mesurerait la même chose que le deuxième."""
        vues: list[int] = []

        def _observe(requete: httpx.Request) -> httpx.Response:
            import json as _json
            charge = _json.loads(requete.content)
            vues.append(len(charge.get("tools") or []))
            return _reponse_ok(requete)

        bouchon(_observe)
        mod.mesurer(passes=1)
        assert vues == [0, 0, len(mod.TOOLS)]


def test_main_rend_1_quand_la_mesure_est_impossible(bouchon, monkeypatch, capsys):
    """Le code de sortie sépare « pas pu juger » de « jugé, c'est bon »."""
    def _tombe(_requete):
        raise httpx.ConnectError("connexion refusée")

    bouchon(_tombe)
    monkeypatch.setattr("sys.argv", ["mesure_plancher_accueil.py", "--passes", "1"])
    assert mod.main() == 1
    assert "MESURE IMPOSSIBLE" in capsys.readouterr().err


def test_main_rend_0_quand_la_mesure_aboutit(bouchon, monkeypatch):
    bouchon(_reponse_ok)
    monkeypatch.setattr("sys.argv", ["mesure_plancher_accueil.py", "--passes", "1"])
    assert mod.main() == 0
