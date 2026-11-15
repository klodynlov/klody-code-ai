"""Repli brain → coder quand le 503 MÉMOIRE du gateway persiste.

Mesuré le 2026-11-15 (`~/klody-core/state/journal.db`, 45 jours) : 2 852
requêtes utilisateur `klody-ai` réussies, 46 refusées en 503, 2 injoignables.
Les 5 refus du jour (06:44–06:46) : « RAM insuffisante pour brain (~52 Go) :
libre 55/95 Go virtuel, RAM réelle 54 Go (plancher 12), rien d'évinçable de
plus ». Le réessai (5 s puis 10 s) n'a pas suffi ; `coder` (~40 Go) aurait tenu.

Le repli vaut pour l'APPEL : `self.model` n'est jamais muté — c'est le piège de
`MODEL_FALLBACK` (2026-09-20), qui empoisonnait le reste de la session.
"""
from types import SimpleNamespace

import httpx
import pytest
from agent import erreurs_llm
from agent.erreurs_llm import attente_reessai_503, modele_repli_503, noter_repli_503
from agent.llm import LLMClient
from openai import InternalServerError, NotFoundError

_URL = "http://localhost:8090/v1"
_RAM = (
    "RAM insuffisante pour brain (~52 Go) : libre 55/95 Go virtuel, "
    "RAM réelle 54 Go (plancher 12), rien d'évinçable de plus"
)
_RAM_CODER = "RAM insuffisante pour coder (~40 Go) : RAM réelle 30 Go (plancher 12)"


class _Delta(SimpleNamespace):
    def __init__(self, content=None):
        super().__init__(content=content, reasoning=None, tool_calls=None, model_extra={})


def _stream(texte: str):
    return iter([SimpleNamespace(choices=[SimpleNamespace(delta=_Delta(texte))])])


def _requete() -> httpx.Request:
    return httpx.Request("POST", f"{_URL}/chat/completions")


def _503(detail: str = _RAM):
    return InternalServerError(
        f"Error code: 503 - {{'error': {detail!r}}}",
        response=httpx.Response(503, request=_requete()),
        body={"error": detail},
    )


def _404():
    return NotFoundError(
        "Error code: 404",
        response=httpx.Response(404, request=_requete()),
        body={"error": "modèle inconnu : coder"},
    )


class _Completions:
    def __init__(self, scenario: list):
        self._scenario = list(scenario)
        self.captured: list[dict] = []

    def create(self, **params):
        self.captured.append(params)
        etape = self._scenario.pop(0)
        if isinstance(etape, BaseException):
            raise etape
        return _stream(etape)

    @property
    def modeles(self) -> list[str]:
        return [p["model"] for p in self.captured]


_OUTILS = [{"type": "function", "function": {
    "name": "noop", "description": "", "parameters": {"type": "object", "properties": {}},
}}]
_MSG = [{"role": "user", "content": "salut"}]


def _client(scenario: list, backend: str = "mlx", model: str = "brain",
            base_url: str = _URL) -> tuple[LLMClient, _Completions]:
    c = LLMClient.__new__(LLMClient)
    c.model = model
    c.total_tokens = 0
    c._backend = backend
    c._base_url = base_url
    completions = _Completions(scenario)
    c.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return c, completions


@pytest.fixture
def gateway(monkeypatch):
    """Mode gateway nominal : brain et coder derrière :8090. Rend les attentes."""
    attentes: list[float] = []
    monkeypatch.setattr("agent.llm.time.sleep", attentes.append)
    monkeypatch.setattr("config.BACKEND", "mlx")
    monkeypatch.setattr("config.LLM_MODEL", "brain")
    monkeypatch.setattr("config.LLM_BASE_URL", _URL)
    monkeypatch.setattr("config.CODE_MODEL", "coder")
    monkeypatch.setattr("config.CODE_BASE_URL", _URL)
    monkeypatch.setattr("config.LLM_503_REPLI", True)
    monkeypatch.setattr("config.LLM_503_REPLI_MEMOIRE_S", 120.0)
    monkeypatch.setattr("config.LLM_503_ESSAIS", 2)
    monkeypatch.setattr("config.LLM_503_ATTENTE_S", 5.0)
    return attentes


class TestDecision:
    """`modele_repli_503` : la décision unique, lue par les deux chemins."""

    def _llm(self, **kw):
        base = {"model": "brain", "_backend": "mlx", "_base_url": _URL}
        return SimpleNamespace(**{**base, **kw})

    def test_503_ram_sur_brain_en_gateway_rend_coder(self, gateway):
        assert modele_repli_503(_503(), self._llm()) == "coder"

    def test_slash_final_de_l_url_ne_compte_pas(self, gateway):
        assert modele_repli_503(_503(), self._llm(_base_url=_URL + "/")) == "coder"

    @pytest.mark.parametrize("llm_kw, raison", [
        ({"_backend": "ollama"}, "mode ollama : ni gateway ni alias coder"),
        ({"model": "coder"}, "jamais de repli coder → coder"),
        ({"model": "vision"}, "modèle tiers choisi dans le sélecteur"),
        ({"_base_url": "http://localhost:8080/v1"}, "mode autonome : deux serveurs"),
    ])
    def test_pas_de_repli(self, gateway, llm_kw, raison):
        assert modele_repli_503(_503(), self._llm(**llm_kw)) is None, raison

    def test_pas_de_repli_sans_modele_de_code(self, gateway, monkeypatch):
        monkeypatch.setattr("config.CODE_MODEL", "")
        assert modele_repli_503(_503(), self._llm()) is None

    def test_pas_de_repli_si_les_deux_alias_sont_le_meme(self, gateway, monkeypatch):
        # Un seul modèle pour tout : rejouer sur lui-même ne libère rien.
        monkeypatch.setattr("config.CODE_MODEL", "brain")
        assert modele_repli_503(_503(), self._llm()) is None

    def test_pas_de_repli_si_coupe(self, gateway, monkeypatch):
        monkeypatch.setattr("config.LLM_503_REPLI", False)
        assert modele_repli_503(_503(), self._llm()) is None

    def test_pas_de_repli_sur_503_non_memoire(self, gateway):
        # Un worker qui démarre ne sera pas mieux servi par coder.
        assert modele_repli_503(_503("worker en cours de démarrage"), self._llm()) is None

    def test_pas_de_repli_sur_404(self, gateway):
        assert modele_repli_503(_404(), self._llm()) is None

    def test_reglages_par_defaut(self):
        # Verrou sur des littéraux : un défaut à `false` rendrait le repli
        # inopérant en production avec une suite entièrement verte.
        import importlib
        import os
        import subprocess
        import sys

        env = {k: v for k, v in os.environ.items() if not k.startswith("LLM_503_")}
        sortie = subprocess.run(
            [sys.executable, "-c",
             "import config; print(config.LLM_503_REPLI, config.LLM_503_REPLI_MEMOIRE_S)"],
            capture_output=True, text=True, env=env, cwd=os.path.dirname(importlib.import_module("config").__file__),
        )
        assert sortie.stdout.split() == ["True", "120.0"], sortie.stderr[-400:]


class TestCLI:
    def test_503_persistant_puis_coder_repond(self, gateway):
        c, comp = _client([_503(), _503(), _503(), "réponse du repli"])
        content, _ = c.stream_chat(
            _MSG, tools=_OUTILS, silent=True, tool_choice="required", max_tokens=1234,
        )
        assert content == "réponse du repli"
        # 1 appel + 2 essais sur brain, PUIS coder.
        assert comp.modeles == ["brain", "brain", "brain", "coder"]
        assert gateway == [5.0, 10.0]
        # Le tour rejoué est LE MÊME tour.
        assert comp.captured[-1]["tool_choice"] == "required"
        assert comp.captured[-1]["max_tokens"] == 1234
        assert comp.captured[-1]["messages"] == _MSG

    def test_le_modele_de_session_n_est_pas_mute(self, gateway):
        c, _ = _client([_503(), _503(), _503(), "ok"])
        client_avant = c.client
        c.stream_chat(_MSG, silent=True)
        assert c.model == "brain"
        assert c._base_url == _URL
        assert c.client is client_avant          # pas de switch_to déguisé

    def test_l_appel_suivant_reessaie_brain_d_abord(self, gateway):
        c, comp = _client([_503(), _503(), _503(), "dégradé", "nominal"])
        c.stream_chat(_MSG, silent=True)
        content, _ = c.stream_chat(_MSG, silent=True)
        assert content == "nominal"
        assert comp.modeles[-1] == "brain"

    def test_apres_un_repli_les_appels_suivants_n_attendent_plus(self, gateway):
        # Un tour ReAct enchaîne les appels : sans mémoire du repli, chaque
        # itération repaierait 5 + 10 s avant d'arriver au même coder.
        c, comp = _client([_503(), _503(), _503(), "un", _503(), "deux"])
        c.stream_chat(_MSG, silent=True)
        content, _ = c.stream_chat(_MSG, silent=True)
        assert content == "deux"
        assert comp.modeles == ["brain", "brain", "brain", "coder", "brain", "coder"]
        assert gateway == [5.0, 10.0]            # rien de plus au second appel

    def test_la_memoire_du_repli_expire(self, gateway, monkeypatch):
        c, comp = _client([_503(), _503(), _503(), "un", _503(), _503(), _503(), "deux"])
        c.stream_chat(_MSG, silent=True)
        c._repli_503_a -= 121.0                  # 121 s plus tard
        c.stream_chat(_MSG, silent=True)
        assert gateway == [5.0, 10.0, 5.0, 10.0]
        assert comp.modeles[4:] == ["brain", "brain", "brain", "coder"]

    def test_503_des_deux_cotes_rend_le_refus_de_brain(self, gateway):
        c, comp = _client([_503(), _503(), _503(), _503(_RAM_CODER), "jamais atteint"])
        with pytest.raises(InternalServerError) as info:
            c.stream_chat(_MSG, silent=True)
        # Un SEUL essai sur coder : ni réessai, ni second repli.
        assert comp.modeles == ["brain", "brain", "brain", "coder"]
        assert gateway == [5.0, 10.0]
        # C'est le refus du modèle DEMANDÉ qui remonte : le message reste celui d'avant.
        assert "pour brain" in erreurs_llm.detail_http(info.value)
        assert c.model == "brain"

    def test_autre_erreur_du_repli_remonte_telle_quelle(self, gateway):
        c, comp = _client([_503(), _503(), _503(), _404()])
        with pytest.raises(NotFoundError):
            c.stream_chat(_MSG, silent=True)
        assert comp.modeles == ["brain", "brain", "brain", "coder"]

    def test_pas_de_repli_en_mode_ollama(self, gateway, monkeypatch):
        monkeypatch.setattr("agent.llm.MODEL_FALLBACK", "")
        c, comp = _client([_503(), _503(), _503(), "jamais atteint"], backend="ollama")
        with pytest.raises(InternalServerError):
            c.stream_chat(_MSG, silent=True)
        assert comp.modeles == ["brain", "brain", "brain"]

    def test_pas_de_repli_depuis_coder(self, gateway):
        c, comp = _client([_503(_RAM_CODER)] * 3 + ["jamais atteint"], model="coder")
        with pytest.raises(InternalServerError):
            c.stream_chat(_MSG, silent=True)
        assert comp.modeles == ["coder", "coder", "coder"]

    def test_le_mode_degrade_est_annonce(self, gateway, monkeypatch):
        lignes: list[str] = []
        monkeypatch.setattr("agent.llm.console.print", lambda *a, **_k: lignes.append(" ".join(map(str, a))))
        c, _ = _client([_503(), _503(), _503(), "ok"])
        content, _ = c.stream_chat(_MSG, silent=False)
        assert content == "ok"
        annonce = [ligne for ligne in lignes if "mode dégradé" in ligne]
        assert len(annonce) == 1, lignes
        assert "[yellow]" in annonce[0]
        assert "« brain »" in annonce[0] and "« coder »" in annonce[0]

    def test_503_des_deux_cotes_n_affiche_qu_une_erreur(self, gateway, monkeypatch):
        # Le refus du repli ne s'affiche pas : une seule ligne rouge, celle du
        # modèle demandé — pas deux erreurs empilées pour une seule panne.
        lignes: list[str] = []
        monkeypatch.setattr("agent.llm.console.print", lambda *a, **_k: lignes.append(" ".join(map(str, a))))
        c, comp = _client([_503(), _503(), _503(), _503(_RAM_CODER), "jamais atteint"])
        with pytest.raises(InternalServerError):
            c.stream_chat(_MSG, silent=False)
        assert comp.modeles == ["brain", "brain", "brain", "coder"]
        rouges = [ligne for ligne in lignes if "✗" in ligne]
        assert len(rouges) == 1, lignes
        assert "RAM réelle 54 Go" in rouges[0] and "RAM réelle 30 Go" not in rouges[0]

    def test_silencieux_n_annonce_rien(self, gateway, monkeypatch):
        lignes: list[str] = []
        monkeypatch.setattr("agent.llm.console.print", lambda *a, **_k: lignes.append(str(a)))
        c, _ = _client([_503(), _503(), _503(), "ok"])
        c.stream_chat(_MSG, silent=True)
        assert lignes == []


class TestAttente:
    def test_sans_repli_recent_l_attente_est_inchangee(self, gateway):
        llm = SimpleNamespace(model="brain", _backend="mlx", _base_url=_URL)
        assert attente_reessai_503(_503(), 0, llm) == 5.0
        assert attente_reessai_503(_503(), 1, llm) == 10.0
        assert attente_reessai_503(_503(), 2, llm) is None

    def test_repli_recent_supprime_l_attente(self, gateway):
        llm = SimpleNamespace(model="brain", _backend="mlx", _base_url=_URL)
        noter_repli_503(llm)
        assert attente_reessai_503(_503(), 0, llm) is None

    def test_repli_recent_ne_supprime_pas_l_attente_d_un_503_sans_repli(self, gateway):
        # 503 non mémoire : aucun repli possible, donc le réessai reste le seul recours.
        llm = SimpleNamespace(model="brain", _backend="mlx", _base_url=_URL)
        noter_repli_503(llm)
        assert attente_reessai_503(_503("worker en cours de démarrage"), 0, llm) == 5.0
