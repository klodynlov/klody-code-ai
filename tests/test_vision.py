"""Tests de tools/vision.py — outil analyser_image (image → modèle VL via gateway).

Hermétiques : aucun appel réseau réel. Le client OpenAI est simulé par
monkeypatch de `openai.OpenAI` (l'outil fait `from openai import OpenAI` au
moment de l'appel). Les racines sandbox sont redirigées vers tmp_path.
"""
from __future__ import annotations

import openai
import pytest

import config
from tools import vision


# --------------------------------------------------------------------------- #
# Faux client OpenAI                                                           #
# --------------------------------------------------------------------------- #
class _FakeMsg:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMsg(content)


class _FakeResp:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, holder):
        self._h = holder

    def create(self, **kwargs):
        self._h.captured = kwargs
        if self._h.raises is not None:
            raise self._h.raises
        return _FakeResp(self._h.content)


class _FakeClient:
    def __init__(self, holder, **kwargs):
        holder.client_kwargs = kwargs

        class _Chat:
            completions = _FakeCompletions(holder)

        self.chat = _Chat()


class _Holder:
    """État partagé entre le test et le faux client."""
    content = "Une photo d'un chat roux sur un canapé."
    raises: Exception | None = None
    captured: dict | None = None
    client_kwargs: dict | None = None


@pytest.fixture()
def vl_env(monkeypatch, tmp_path):
    """Modèle VL configuré, racine sandbox = tmp_path, faux client OpenAI."""
    monkeypatch.setattr(config, "VL_MODEL", "vision")
    monkeypatch.setattr(config, "VL_BASE_URL", "http://localhost:8090/v1")
    monkeypatch.setattr(config, "VL_API_KEY", "mlx")
    monkeypatch.setattr(config, "VL_MAX_TOKENS", 512)
    monkeypatch.setattr(config, "VL_MAX_IMAGE_MB", 12.0)
    monkeypatch.setattr(vision, "_VISION_ROOTS", [tmp_path])

    holder = _Holder()
    holder.content = _Holder.content
    holder.raises = None
    holder.captured = None
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: _FakeClient(holder, **kw))
    return holder, tmp_path


def _png(tmp_path, name="photo.png", data=b"\x89PNG\r\nfake-bytes"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


class TestSucces:
    def test_decrit_image_et_renvoie_texte(self, vl_env):
        holder, tmp = vl_env
        img = _png(tmp)
        out = vision.analyser_image(str(img))
        assert "Analyse de photo.png" in out
        assert "chat roux" in out

    def test_payload_contient_image_url_et_question(self, vl_env):
        holder, tmp = vl_env
        img = _png(tmp)
        vision.analyser_image(str(img), question="Quel animal ?")
        msgs = holder.captured["messages"]
        content = msgs[0]["content"]
        types = {part["type"] for part in content}
        assert types == {"text", "image_url"}
        text_part = next(p for p in content if p["type"] == "text")
        assert text_part["text"] == "Quel animal ?"
        img_part = next(p for p in content if p["type"] == "image_url")
        assert img_part["image_url"]["url"].startswith("data:image/png;base64,")
        assert holder.captured["model"] == "vision"

    def test_question_vide_utilise_defaut(self, vl_env):
        holder, tmp = vl_env
        vision.analyser_image(str(_png(tmp)), question="")
        text_part = next(
            p for p in holder.captured["messages"][0]["content"] if p["type"] == "text"
        )
        assert text_part["text"] == vision._DEFAULT_QUESTION


class TestDesactive:
    def test_vl_non_configure(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "VL_MODEL", "")
        out = vision.analyser_image(str(_png(tmp_path)))
        assert "indisponible" in out


class TestSandbox:
    def test_hors_racines_refuse(self, vl_env):
        out = vision.analyser_image("/etc/passwd")
        assert "accès refusé" in out or "non-image" in out

    def test_extension_non_image_bloquee(self, vl_env):
        """Un secret (.env) ne peut pas être lu « comme une image »."""
        holder, tmp = vl_env
        secret = tmp / ".env"
        secret.write_text("SECRET=123")
        out = vision.analyser_image(str(secret))
        assert "non-image" in out
        assert holder.captured is None  # aucun appel VL → rien n'a fuité

    def test_image_introuvable(self, vl_env):
        holder, tmp = vl_env
        out = vision.analyser_image(str(tmp / "absente.png"))
        assert "introuvable" in out

    def test_image_trop_grosse(self, vl_env, monkeypatch):
        holder, tmp = vl_env
        monkeypatch.setattr(config, "VL_MAX_IMAGE_MB", 0.0001)
        out = vision.analyser_image(str(_png(tmp, data=b"x" * 5000)))
        assert "trop volumineuse" in out


class TestPannes:
    def test_worker_injoignable(self, vl_env):
        holder, tmp = vl_env
        holder.raises = RuntimeError("connection refused sk-secret-leak")
        out = vision.analyser_image(str(_png(tmp)))
        assert "n'a pas répondu" in out
        # L'exception brute (qui peut contenir URL/clé) ne doit PAS fuiter au LLM.
        assert "sk-secret-leak" not in out

    def test_reponse_vide(self, vl_env):
        holder, tmp = vl_env
        holder.content = "   "
        out = vision.analyser_image(str(_png(tmp)))
        assert "réponse vide" in out


class TestRegistry:
    def test_outil_enregistre(self):
        from tools import registry

        names = {t["function"]["name"] for t in registry.TOOLS}
        assert "analyser_image" in names
