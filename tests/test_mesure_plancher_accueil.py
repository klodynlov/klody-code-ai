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


class TestBalayage:
    """Le mode qui isole le coût de prefill des schémas d'outils.

    Il n'a de sens que si deux appels ne partagent JAMAIS le même préfixe : un
    préfixe déjà vu mesure le cache de prompt, pas le prefill — c'est
    exactement l'ambiguïté du run du 2026-08-01 que ce mode doit lever.
    """

    @staticmethod
    def _observer(vues: list[int]):
        def _handler(requete: httpx.Request) -> httpx.Response:
            import json as _json
            vues.append(len(_json.loads(requete.content).get("tools") or []))
            return _reponse_ok(requete)

        return _handler

    def test_balaye_des_tailles_croissantes(self, bouchon):
        vues: list[int] = []
        bouchon(self._observer(vues))
        mesure = mod.balayer(passes=1)
        assert vues == sorted(vues), "les paliers doivent monter"
        assert vues[0] == 0 and vues[-1] == len(mod.TOOLS)
        assert mesure["paliers"][0] == 0

    def test_aucun_prefixe_rejoue_entre_les_passes(self, bouchon):
        """Sinon la passe 2 mesurerait le cache et la pente serait fausse."""
        vues: list[int] = []
        bouchon(self._observer(vues))
        mod.balayer(passes=3)
        non_nuls = [n for n in vues if n]
        assert len(set(non_nuls)) == len(non_nuls)

    def test_le_dernier_palier_decale_vers_le_bas(self, bouchon):
        """Il n'y a pas de place au-dessus de len(TOOLS) : on décale à l'envers."""
        vues: list[int] = []
        bouchon(self._observer(vues))
        mod.balayer(passes=2)
        assert max(vues) <= len(mod.TOOLS)

    def test_chaque_mesure_porte_son_palier(self, bouchon):
        bouchon(_reponse_ok)
        mesure = mod.balayer(passes=2)
        assert all("palier" in m and "tokens" in m for m in mesure["mesures"])
        assert {m["palier"] for m in mesure["mesures"]} == set(mesure["paliers"])

    def test_backend_injoignable_ne_rend_pas_un_chiffre(self, bouchon):
        def _tombe(_requete):
            raise httpx.ConnectError("connexion refusée")

        bouchon(_tombe)
        with pytest.raises(mod.MesureImpossible):
            mod.balayer(passes=1)

    def test_main_en_mode_balayage(self, bouchon, monkeypatch):
        bouchon(_reponse_ok)
        monkeypatch.setattr(
            "sys.argv", ["mesure_plancher_accueil.py", "--balayage", "--passes", "1"]
        )
        assert mod.main() == 0


class TestBascule:
    """Le mode qui mesure si une bascule brain → coder re-paie le prefill.

    Le protocole EST la mesure : sans la phase témoin, l'alternance ne se
    compare à rien ; sans l'exclusion de l'amorçage, le chargement de coder
    (30 Go, 8,3 s mesuré) serait attribué à la bascule.
    """

    @pytest.fixture(autouse=True)
    def _deux_modeles(self, monkeypatch):
        monkeypatch.setattr(mod.config, "LLM_MODEL", "brain")
        monkeypatch.setattr(mod.config, "CODE_MODEL", "coder")

    @staticmethod
    def _observer(vus: list[str], outils: list[int] | None = None):
        def _handler(requete: httpx.Request) -> httpx.Response:
            import json as _json
            charge = _json.loads(requete.content)
            vus.append(charge["model"])
            if outils is not None:
                outils.append(len(charge.get("tools") or []))
            return httpx.Response(
                200,
                json={"model": f"servi/{charge['model']}",
                      "choices": [{"message": {"content": "ok"}}], "usage": {}},
                request=requete,
            )

        return _handler

    def test_refuse_quand_aucun_modele_code_n_est_configure(self, bouchon, monkeypatch):
        """Sans second modèle, la bascule n'existe pas — ne pas rendre 0 et un tableau."""
        monkeypatch.setattr(mod.config, "CODE_MODEL", "")
        bouchon(_reponse_ok)
        with pytest.raises(mod.MesureImpossible) as exc:
            mod.basculer(passes=1)
        assert "CODE_MODEL" in str(exc.value)

    def test_l_ordre_des_appels_suit_le_protocole(self, bouchon):
        vus: list[str] = []
        bouchon(self._observer(vus))
        mod.basculer(passes=2)
        assert vus == [
            "brain", "coder",                                  # amorçage
            "brain", "coder", "brain", "coder",                # alternance
            "brain", "brain",                                  # témoin
        ]

    def test_le_temoin_ne_bascule_jamais(self, bouchon):
        """C'est tout son intérêt : même préfixe, même modèle, sans interruption."""
        bouchon(self._observer([]))
        mesure = mod.basculer(passes=3)
        temoin = [m for m in mesure["mesures"] if m["phase"] == "témoin"]
        assert temoin and all(m["role"] == "brain" for m in temoin)

    def test_l_amorcage_est_isole_des_conclusions(self, bouchon):
        bouchon(self._observer([]))
        mesure = mod.basculer(passes=2)
        phases = {m["phase"] for m in mesure["mesures"]}
        assert phases == {"amorçage", "alternance", "témoin"}
        assert sum(m["phase"] == "amorçage" for m in mesure["mesures"]) == 2

    def test_chaque_appel_porte_bien_les_schemas(self, bouchon):
        """Le prefill dont on teste la survie, c'est celui des outils."""
        outils: list[int] = []
        bouchon(self._observer([], outils))
        mod.basculer(passes=1)
        assert outils and all(n == len(mod.TOOLS) for n in outils)

    def test_les_deux_modeles_servis_sont_rapportes(self, bouchon):
        bouchon(self._observer([]))
        mesure = mod.basculer(passes=1)
        assert mesure["model_served"] == "servi/brain"
        assert mesure["code_model_served"] == "servi/coder"

    def test_alias_identiques_rend_la_mesure_NON_CONCLUANTE(self, capsys):
        """Sans ce garde, « aucune bascule » se lirait « bascule gratuite »."""
        mod._resume_bascule({
            "mesures": [
                {"phase": "alternance", "role": "brain", "latence_s": 0.4},
                {"phase": "alternance", "role": "coder", "latence_s": 0.4},
                {"phase": "témoin", "role": "brain", "latence_s": 0.4},
            ],
            "model_served": "meme/modele",
            "code_model_served": "meme/modele",
        })
        sortie = capsys.readouterr().out
        assert "NON CONCLUANTE" in sortie

    def test_ecart_affiche_quand_les_modeles_different(self, capsys):
        mod._resume_bascule({
            "mesures": [
                {"phase": "alternance", "role": "brain", "latence_s": 4.5},
                {"phase": "alternance", "role": "coder", "latence_s": 4.4},
                {"phase": "témoin", "role": "brain", "latence_s": 0.4},
            ],
            "model_served": "a", "code_model_served": "b",
        })
        sortie = capsys.readouterr().out
        assert "ÉCART" in sortie and "4.10" in sortie
        assert "NON CONCLUANTE" not in sortie

    def test_backend_injoignable_ne_rend_pas_un_chiffre(self, bouchon):
        def _tombe(_requete):
            raise httpx.ConnectError("connexion refusée")

        bouchon(_tombe)
        with pytest.raises(mod.MesureImpossible):
            mod.basculer(passes=1)


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
