"""`LLMClient.stream_chat` — réessai borné sur 503, bascule de secours gardée.

Vécu le 2026-09-20 : un 503 « RAM insuffisante pour brain » du gateway était
rendu tel quel, au premier refus, alors qu'il est transitoire (garde mémoire,
`vm_stat` qui sous-estime juste après un chargement). Et en auditant le chemin
d'erreur : sur timeout ou 404, le client basculait sur `MODEL_FALLBACK`
(`mistral:latest`, un nom OLLAMA) même derrière le gateway — qui le rejette en
404 — et MUTAIT `self.model`, empoisonnant le reste de la session. La relance
perdait de surcroît `tool_choice`, `max_tokens` et `silent`.
"""
from types import SimpleNamespace

import httpx
import pytest
from agent.llm import LLMClient
from openai import APITimeoutError, InternalServerError, NotFoundError


class _Delta(SimpleNamespace):
    def __init__(self, content=None):
        super().__init__(content=content, reasoning=None, tool_calls=None, model_extra={})


def _stream(texte: str):
    return iter([SimpleNamespace(choices=[SimpleNamespace(delta=_Delta(texte))])])


def _requete() -> httpx.Request:
    return httpx.Request("POST", "http://localhost:8090/v1/chat/completions")


def _503(detail: str = "RAM insuffisante pour brain (~44 Go) : RAM réelle 46 Go"):
    return InternalServerError(
        f"Error code: 503 - {{'error': {detail!r}}}",
        response=httpx.Response(503, request=_requete()),
        body={"error": detail},
    )


def _404(modele: str = "mistral:latest"):
    return NotFoundError(
        "Error code: 404",
        response=httpx.Response(404, request=_requete()),
        body={"error": f"modèle inconnu : {modele}"},
    )


class _Completions:
    """`create()` déroule un scénario : exception à lever, ou texte à streamer."""

    def __init__(self, scenario: list):
        self._scenario = list(scenario)
        self.captured: list[dict] = []

    def create(self, **params):
        self.captured.append(params)
        etape = self._scenario.pop(0)
        if isinstance(etape, BaseException):
            raise etape
        return _stream(etape)


# `tool_choice` n'est transmis au backend QU'AVEC des outils : on en passe un.
_OUTILS = [{"type": "function", "function": {
    "name": "noop", "description": "", "parameters": {"type": "object", "properties": {}},
}}]


def _client(scenario: list, backend: str = "mlx") -> tuple[LLMClient, _Completions]:
    c = LLMClient.__new__(LLMClient)
    c.model = "brain"
    c.total_tokens = 0
    c._backend = backend
    c._base_url = "http://localhost:8090/v1"
    completions = _Completions(scenario)
    c.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return c, completions


@pytest.fixture
def horloge(monkeypatch):
    """Neutralise l'attente et enregistre les durées demandées."""
    attentes: list[float] = []
    monkeypatch.setattr("agent.llm.time.sleep", attentes.append)
    monkeypatch.setattr("config.LLM_503_ESSAIS", 2)
    monkeypatch.setattr("config.LLM_503_ATTENTE_S", 5.0)
    monkeypatch.setattr("agent.llm.MODEL_FALLBACK", "secours")
    return attentes


class TestReessai503:
    def test_deux_503_puis_succes(self, horloge):
        c, comp = _client([_503(), _503(), "ça marche"])
        content, tool_calls = c.stream_chat(
            [{"role": "user", "content": "salut"}], tools=_OUTILS, silent=True,
            tool_choice="required", max_tokens=1234,
        )
        assert content == "ça marche"
        assert tool_calls is None
        assert len(comp.captured) == 3
        # Backoff exponentiel : 5 s puis 10 s.
        assert horloge == [5.0, 10.0]
        # La relance est LE MÊME tour : arguments conservés (avant, perdus).
        assert all(p["tool_choice"] == "required" for p in comp.captured)
        assert all(p["max_tokens"] == 1234 for p in comp.captured)

    def test_503_persistant_leve_apres_les_essais(self, horloge):
        c, comp = _client([_503(), _503(), _503(), "jamais atteint"])
        with pytest.raises(InternalServerError):
            c.stream_chat([{"role": "user", "content": "salut"}], silent=True)
        assert len(comp.captured) == 3          # 1 appel + 2 essais
        assert horloge == [5.0, 10.0]

    def test_zero_essai_configure_rend_l_erreur_tout_de_suite(self, horloge, monkeypatch):
        monkeypatch.setattr("config.LLM_503_ESSAIS", 0)
        c, comp = _client([_503(), "jamais atteint"])
        with pytest.raises(InternalServerError):
            c.stream_chat([{"role": "user", "content": "salut"}], silent=True)
        assert len(comp.captured) == 1
        assert horloge == []

    def test_le_modele_n_est_pas_mute_par_un_503(self, horloge):
        c, _ = _client([_503(), "ok"])
        c.stream_chat([{"role": "user", "content": "salut"}], silent=True)
        assert c.model == "brain"


class TestBasculeDeSecours:
    """MODEL_FALLBACK est un nom Ollama : derrière le gateway, on n'y va JAMAIS."""

    def test_timeout_en_mode_gateway_ne_bascule_pas(self, horloge):
        c, comp = _client([APITimeoutError(request=_requete()), "jamais atteint"], backend="mlx")
        with pytest.raises(APITimeoutError):
            c.stream_chat([{"role": "user", "content": "salut"}], silent=True)
        assert c.model == "brain"               # session NON empoisonnée
        assert len(comp.captured) == 1

    def test_404_en_mode_gateway_ne_bascule_pas(self, horloge):
        c, comp = _client([_404("brain"), "jamais atteint"], backend="mlx")
        with pytest.raises(NotFoundError):
            c.stream_chat([{"role": "user", "content": "salut"}], silent=True)
        assert c.model == "brain"
        assert len(comp.captured) == 1

    def test_timeout_en_mode_ollama_bascule_et_conserve_les_arguments(self, horloge):
        c, comp = _client([APITimeoutError(request=_requete()), "réponse de secours"], backend="ollama")
        content, _ = c.stream_chat(
            [{"role": "user", "content": "salut"}], tools=_OUTILS, silent=True,
            tool_choice="none", max_tokens=42,
        )
        assert content == "réponse de secours"
        assert c.model == "secours"
        assert comp.captured[-1]["model"] == "secours"
        assert comp.captured[-1]["tool_choice"] == "none"
        assert comp.captured[-1]["max_tokens"] == 42

    def test_404_en_mode_ollama_bascule_une_seule_fois(self, horloge):
        c, comp = _client([_404(), _404("secours")], backend="ollama")
        with pytest.raises(NotFoundError):
            c.stream_chat([{"role": "user", "content": "salut"}], silent=True)
        assert c.model == "secours"
        assert len(comp.captured) == 2          # pas de boucle secours → secours

    def test_fallback_vide_desactive_la_bascule(self, horloge, monkeypatch):
        monkeypatch.setattr("agent.llm.MODEL_FALLBACK", "")
        c, _comp = _client([APITimeoutError(request=_requete())], backend="ollama")
        with pytest.raises(APITimeoutError):
            c.stream_chat([{"role": "user", "content": "salut"}], silent=True)
        assert c.model == "brain"
