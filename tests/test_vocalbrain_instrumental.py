"""Tests du mode instrumental (klody_mcp.vocalbrain_server, klody_mcp.klody_music_server).

Aucun réseau, aucun daemon : `_post` / `_ls_post` sont remplacés et on inspecte le
corps réellement envoyé à /generate. Ce qui est vérifié tient en une phrase : un
instrumental ne doit JAMAIS emporter de paroles ni de chemin voix, et ne doit
jamais être annoncé comme chanté.
"""
from __future__ import annotations

import httpx
import pytest
from klody_mcp import klody_music_server as km, vocalbrain_server as vb


class _Resp:
    """Réponse httpx minimale (le code ne lit que status_code/json/text)."""

    def __init__(self, payload: dict, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]


def _capture(monkeypatch, module, nom_post: str, resp: _Resp | None = None) -> dict:
    """Détourne le POST du module et renvoie le dict qui recevra le corps envoyé."""
    vu: dict = {}

    async def _post(path: str, json_body: dict):
        vu["path"] = path
        vu["body"] = json_body
        return resp or _Resp({"session_id": "abc12345", "status": "queued"})

    monkeypatch.setattr(module, nom_post, _post)
    return vu


# --------------------------------------------------------------------------- #
# vocalbrain_server.generer_instrumental                                       #
# --------------------------------------------------------------------------- #


async def test_instrumental_pose_le_drapeau_et_aucune_parole(monkeypatch):
    vu = _capture(monkeypatch, vb, "_post")
    r = await vb.generer_instrumental("boom bap, dusty rhodes, upright bass", duree_sec=45)

    assert vu["path"] == "/generate"
    body = vu["body"]
    assert body["instrumental"] is True
    # Le marqueur d'arrangement — et RIEN d'autre : aucune parole ne doit passer.
    assert body["custom_lyrics"] == "[Instrumental]"
    assert body["duration_sec"] == 45
    assert body["style_prompt"] == "boom bap, dusty rhodes, upright bass"
    assert r["session_id"] == "abc12345"
    assert r["parametres"]["voix"] is None


async def test_instrumental_nenvoie_pas_de_modele_voix(monkeypatch):
    # RVC est sauté côté daemon : envoyer rvc_model/rvc_transpose donnerait à
    # croire qu'une voix clonée intervient.
    vu = _capture(monkeypatch, vb, "_post")
    await vb.generer_instrumental("ambient pads")

    assert "rvc_model" not in vu["body"]
    assert "rvc_transpose" not in vu["body"]


async def test_instrumental_transmet_bpm_tonalite_lora(monkeypatch):
    vu = _capture(monkeypatch, vb, "_post")
    await vb.generer_instrumental("neo soul", bpm=88, tonalite="F minor", lora_style="zouk")

    assert vu["body"]["bpm"] == 88
    assert vu["body"]["key"] == "F minor"
    assert vu["body"]["lora_style"] == "zouk"


async def test_instrumental_sans_bpm_ni_tonalite_ne_les_invente_pas(monkeypatch):
    vu = _capture(monkeypatch, vb, "_post")
    r = await vb.generer_instrumental("lofi")

    assert "bpm" not in vu["body"]
    assert "key" not in vu["body"]
    # Le défaut appliqué est celui du daemon : la réponse le NOMME au lieu de
    # laisser croire à une valeur choisie ici.
    assert "défaut daemon" in str(r["parametres"]["bpm"])
    assert "défaut daemon" in str(r["parametres"]["tonalite"])


@pytest.mark.parametrize(
    ("demande", "attendu"),
    [(3, 10), (5000, 600), (60, 60)],
)
async def test_instrumental_borne_la_duree(monkeypatch, demande, attendu):
    vu = _capture(monkeypatch, vb, "_post")
    r = await vb.generer_instrumental("techno", duree_sec=demande)

    assert vu["body"]["duration_sec"] == attendu
    # Borner en silence serait un mensonge : la note doit le dire.
    assert ("ramenée" in r["note"]) is (demande != attendu)


async def test_instrumental_borne_le_bpm_et_le_dit(monkeypatch):
    vu = _capture(monkeypatch, vb, "_post")
    r = await vb.generer_instrumental("drum and bass", bpm=300)

    assert vu["body"]["bpm"] == 180
    assert "bpm ramené à 180" in r["note"]


async def test_instrumental_style_vide_refuse(monkeypatch):
    vu = _capture(monkeypatch, vb, "_post")
    r = await vb.generer_instrumental("   ")

    assert "error" in r
    assert vu == {}, "aucun appel daemon ne doit partir sur un style vide"


async def test_instrumental_daemon_injoignable_nomme_la_panne(monkeypatch):
    async def _boom(path: str, json_body: dict):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(vb, "_post", _boom)
    r = await vb.generer_instrumental("jazz trio")

    assert "error" in r
    assert "local-suno" in r["error"]
    assert "session_id" not in r


async def test_instrumental_422_rend_le_detail_du_daemon(monkeypatch):
    _capture(monkeypatch, vb, "_post", _Resp({}, status_code=422, text="key: invalid"))
    r = await vb.generer_instrumental("house", tonalite="XYZ")

    assert "error" in r
    assert "key: invalid" in r["error"]


async def test_instrumental_file_pleine(monkeypatch):
    _capture(monkeypatch, vb, "_post", _Resp({}, status_code=429))
    r = await vb.generer_instrumental("trap")

    assert "error" in r
    assert "file d'attente" in r["error"]


async def test_generer_chanson_ne_pose_pas_le_drapeau(monkeypatch):
    # Non-régression : le chemin chanté reste chanté.
    vu = _capture(monkeypatch, vb, "_post")
    await vb.generer_chanson("je marche sous la pluie", style="pop")

    assert vu["body"].get("instrumental") in (None, False)
    assert vu["body"]["custom_lyrics"] == "je marche sous la pluie"
    assert vu["body"]["rvc_model"] == "klody"


# --------------------------------------------------------------------------- #
# klody_music_server.composer_demo                                             #
# --------------------------------------------------------------------------- #


_IDEE = {
    "genre": "neo soul",
    "ambiance": "mélancolique",
    "instrumentation": ["rhodes", "basse fretless"],
    "tonalite_suggeree": "F minor",
    "amorce_paroles": ["je te cherche encore"],
}


def test_idee_to_body_instrumental_jette_lamorce_de_paroles():
    body = km._idee_to_body(_IDEE, 30, "klody", 0, None, instrumental=True)

    assert body["instrumental"] is True
    assert body["custom_lyrics"] == "[Instrumental]"
    assert "je te cherche encore" not in str(body)
    # Le style (genre, ambiance, instrumentation, tonalité) reste le conditionnement.
    assert "neo soul" in body["style_prompt"]


def test_idee_to_body_chante_par_defaut():
    body = km._idee_to_body(_IDEE, 30, "klody", 0, None)

    assert "instrumental" not in body
    assert body["custom_lyrics"] == "je te cherche encore"


async def test_composer_demo_instrumental_ne_rend_pas_de_paroles(monkeypatch):
    vu = _capture(monkeypatch, km, "_ls_post")
    r = await km.composer_demo(_IDEE, instrumental=True)

    assert vu["body"]["instrumental"] is True
    # Rendre le marqueur comme des « paroles » ferait croire à une démo chantée.
    assert r["demo"]["paroles"] is None
    assert r["demo"]["instrumental"] is True
    assert "aucune voix" in r["note"]


async def test_composer_demo_chante_reste_annonce_comme_chante(monkeypatch):
    vu = _capture(monkeypatch, km, "_ls_post")
    r = await km.composer_demo(_IDEE)

    assert vu["body"].get("instrumental") in (None, False)
    assert r["demo"]["paroles"] == "je te cherche encore"
    assert r["demo"]["instrumental"] is False
