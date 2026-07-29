"""Tests de la provenance des runs (bench/provenance.py) et de son transport.

Enjeu : un fichier de résultats doit dire quelle configuration l'a produit. En mode
gateway, `.env` ne contient qu'un alias — seule une complétion révèle le modèle
réellement servi.
"""
from __future__ import annotations

import json

import httpx
import pytest
from bench import provenance
from bench.gate import load_results, load_run


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._payload


# --- résolution du modèle servi --------------------------------------------


def test_resolve_lit_le_modele_de_la_reponse(monkeypatch):
    """L'alias `brain` doit se résoudre en l'id réellement chargé."""
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return _Response({"model": "unsloth/Qwen3.6-35B-A3B-MLX-8bit"})

    monkeypatch.setattr(httpx, "post", fake_post)

    served = provenance.resolve_served_model("http://x:8090/v1", "brain")

    assert served == "unsloth/Qwen3.6-35B-A3B-MLX-8bit"
    assert captured["url"] == "http://x:8090/v1/chat/completions"
    # Un seul token : la sonde ne doit rien coûter.
    assert captured["json"]["max_tokens"] == 1
    assert captured["json"]["model"] == "brain"


def test_resolve_pose_le_bearer_quand_une_cle_existe(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs["headers"])
        return _Response({"model": "m"})

    monkeypatch.setattr(httpx, "post", fake_post)
    provenance.resolve_served_model("http://x/v1", "brain", api_key="secret")

    assert captured["Authorization"] == "Bearer secret"


def test_resolve_sans_cle_n_envoie_pas_d_authorization(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: (captured.update(kw["headers"]), _Response({"model": "m"}))[1]
    )
    provenance.resolve_served_model("http://x/v1", "brain")

    assert "Authorization" not in captured


@pytest.mark.parametrize(
    "payload",
    [{"model": ""}, {"model": None}, {}, {"model": 42}],
)
def test_resolve_rend_none_sur_reponse_inexploitable(monkeypatch, payload):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _Response(payload))

    assert provenance.resolve_served_model("http://x/v1", "brain") is None


def test_resolve_ne_leve_jamais(monkeypatch):
    """Best-effort : une sonde qui échoue ne doit pas faire tomber le bench."""

    def boom(url, **kwargs):
        raise httpx.ConnectError("gateway éteint")

    monkeypatch.setattr(httpx, "post", boom)

    assert provenance.resolve_served_model("http://x/v1", "brain") is None


def test_resolve_sans_url_ou_modele_ne_sonde_pas(monkeypatch):
    def boom(url, **kwargs):  # pragma: no cover - ne doit jamais être appelé
        raise AssertionError("aucune requête attendue")

    monkeypatch.setattr(httpx, "post", boom)

    assert provenance.resolve_served_model("", "brain") is None
    assert provenance.resolve_served_model("http://x/v1", "") is None


# --- photographie de la config ---------------------------------------------


def test_describe_config_sans_resolution_ne_sonde_pas(monkeypatch):
    monkeypatch.setattr(
        provenance, "resolve_served_model", lambda *a, **k: pytest.fail("pas de réseau attendu")
    )
    described = provenance.describe_config(resolve=False)

    assert described["model_served"] is None
    assert "backend" in described and "base_url" in described


def test_describe_config_resout_le_modele(monkeypatch):
    monkeypatch.setattr(provenance, "resolve_served_model", lambda *a, **k: "resolu")

    assert provenance.describe_config(resolve=True)["model_served"] == "resolu"


# --- résumé lisible --------------------------------------------------------


def test_describe_short_montre_la_resolution_d_alias():
    meta = {"model_configured": "brain", "model_served": "Qwen3.6-35B", "backend": "mlx"}

    assert provenance.describe_short(meta) == "brain → Qwen3.6-35B · backend=mlx"


def test_describe_short_ne_duplique_pas_un_id_identique():
    meta = {"model_configured": "Qwen3.6", "model_served": "Qwen3.6", "backend": "mlx"}

    assert "→" not in provenance.describe_short(meta)


def test_describe_short_tolere_l_absence_de_metadonnees():
    """Les runs antérieurs n'ont pas de meta : ils restent lisibles."""
    assert "inconnue" in provenance.describe_short(None)
    assert "inconnue" in provenance.describe_short({})


# --- transport dans le fichier de run --------------------------------------


def test_load_run_lit_l_enveloppe(tmp_path):
    p = tmp_path / "run.json"
    p.write_text(
        json.dumps({"meta": {"model_served": "m"}, "results": [{"task_id": "easy/a"}]}),
        encoding="utf-8",
    )

    meta, results = load_run(p)

    assert meta["model_served"] == "m"
    assert len(results) == 1


def test_load_run_lit_encore_la_liste_plate(tmp_path):
    """Format historique : les baselines déjà promues ne doivent pas casser."""
    p = tmp_path / "run.json"
    p.write_text(json.dumps([{"task_id": "easy/a"}]), encoding="utf-8")

    meta, results = load_run(p)

    assert meta == {}
    assert len(results) == 1
    assert load_results(p) == results
