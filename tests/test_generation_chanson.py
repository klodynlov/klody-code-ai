"""Tests des deux chemins de génération de chanson chantée.

Aucun réseau, aucun daemon : le POST est détourné et on inspecte le corps
réellement envoyé à /generate — et, surtout, on vérifie qu'AUCUN appel ne part
quand le texte ne pourrait pas être couvert. Un refus arrivé après le POST ne
sert à rien : la génération de 3 minutes est déjà en file.

Le défaut corrigé, en une phrase : Klody demandait une chanson complète sur 30 s
et envoyait les paroles en bloc, ce qui donne au moteur le choix entre tronquer
et répéter. Il fait les deux.
"""
from __future__ import annotations

import httpx
import pytest
from klody_mcp import klody_music_server as km, song_structure as ss, vocalbrain_server as vb


class _Resp:
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
    vu: dict = {}

    async def _post(path: str, json_body: dict):
        vu["path"] = path
        vu["body"] = json_body
        return resp or _Resp({"session_id": "abc12345", "status": "queued"})

    monkeypatch.setattr(module, nom_post, _post)
    return vu


# Chanson complète, structurée, telle qu'un LLM l'écrit (en-têtes français, un
# mélange de crochets et de titres nus). ~230 mots ⇒ ~115 s à la cible du moteur.
_CHANSON = "\n\n".join(
    [
        "[Couplet 1]\n" + " ".join(["mot"] * 40),
        "Refrain :\n" + " ".join(["mot"] * 30),
        "[Couplet 2]\n" + " ".join(["mot"] * 40),
        "[Pont]\n" + " ".join(["mot"] * 20),
        "[Refrain]\n" + " ".join(["mot"] * 30),
        "[Outro]\n" + " ".join(["mot"] * 20),
    ]
)


# --------------------------------------------------------------------------- #
# vocalbrain_server.generer_chanson                                            #
# --------------------------------------------------------------------------- #


class TestParolesEnvoyees:
    async def test_les_marqueurs_sont_canonises(self, monkeypatch):
        # « [Couplet 1] » et « Refrain : » ne sont pas le format du moteur. C'est
        # la balise qui porte la structure : sans elle le refrain n'est pas un
        # refrain, et au-delà de 120 s les segments ne savent pas se partager.
        vu = _capture(monkeypatch, vb, "_post")
        await vb.generer_chanson(_CHANSON, duree_sec=180)

        envoye = vu["body"]["custom_lyrics"]
        entetes = [x for x in envoye.splitlines() if x.startswith("[")]
        assert entetes == [
            "[verse 1]", "[chorus 1]", "[verse 2]",
            "[bridge 1]", "[chorus 2]", "[outro 1]",
        ]

    async def test_aucune_parole_perdue(self, monkeypatch):
        vu = _capture(monkeypatch, vb, "_post")
        await vb.generer_chanson(_CHANSON, duree_sec=180)

        # 40+30+40+20+30+20. ⚠️ Le compte se fait sur l'ARRANGEMENT, pas sur le
        # texte brut : un en-tête nu (« Refrain : ») y compterait pour deux mots,
        # puisque la règle de comptage ne saute que les lignes entre crochets.
        assert ss.mots_chantes(vu["body"]["custom_lyrics"]) == 180

    async def test_chanson_de_plus_de_120s_part_bien(self, monkeypatch):
        # Le cas visé : une chanson complète, multi-segment, non tronquée.
        vu = _capture(monkeypatch, vb, "_post")
        r = await vb.generer_chanson(_CHANSON, duree_sec=180)

        assert vu["body"]["duration_sec"] == 180
        assert r["session_id"] == "abc12345"
        assert r["parametres"]["segments"] == 2
        assert r["parametres"]["sections"] == 6
        assert r["couverture"]["couvrable"] is True


class TestRefusAvantEnvoi:
    async def test_texte_trop_dense_pour_la_duree(self, monkeypatch):
        vu = _capture(monkeypatch, vb, "_post")
        r = await vb.generer_chanson(_CHANSON, duree_sec=15)

        assert "error" in r
        assert vu == {}, "le refus doit précéder le POST, pas le suivre"
        assert "coupera des sections" in r["error"]
        # Actionnable : la durée qui marcherait est dans le message.
        assert str(r["couverture"]["duree_conseillee_sec"]) in r["error"]

    async def test_pas_assez_de_sections_pour_les_segments(self, monkeypatch):
        # 240 s = 3 segments ; un texte d'un seul bloc = 1 section ⇒ les 2 derniers
        # segments re-chantent le même texte (generate_song_long recopie le dernier).
        vu = _capture(monkeypatch, vb, "_post")
        r = await vb.generer_chanson(" ".join(["mot"] * 480), duree_sec=240)

        assert "error" in r and vu == {}
        assert "RE-CHANTERONT" in r["error"]

    async def test_forcer_passe_outre_et_le_dit(self, monkeypatch):
        vu = _capture(monkeypatch, vb, "_post")
        r = await vb.generer_chanson(_CHANSON, duree_sec=15, forcer=True)

        assert "error" not in r
        assert vu["body"]["duration_sec"] == 15
        # Forcer ne doit pas rendre le problème invisible.
        assert "FORCÉ malgré" in r["note"]
        assert r["couverture"]["couvrable"] is False

    async def test_paroles_vides(self, monkeypatch):
        vu = _capture(monkeypatch, vb, "_post")
        r = await vb.generer_chanson("   ")

        assert "error" in r and vu == {}


class TestDuree:
    async def test_duree_deduite_des_paroles(self, monkeypatch):
        # Le défaut valait 30 s quelles que soient les paroles : une chanson
        # complète y était forcément tronquée. Il n'y a plus de défaut fixe.
        vu = _capture(monkeypatch, vb, "_post")
        r = await vb.generer_chanson(_CHANSON)

        assert vu["body"]["duration_sec"] == 90  # 180 mots / 2 mots·s⁻¹
        assert r["parametres"]["duree_deduite"] is True
        assert r["couverture"]["couvrable"] is True

    async def test_la_duree_deduite_ne_tronque_jamais(self, monkeypatch):
        _capture(monkeypatch, vb, "_post")
        r = await vb.generer_chanson(_CHANSON)

        assert r["couverture"]["debit_mots_s"] == pytest.approx(ss.DEBIT_CIBLE, abs=0.1)

    @pytest.mark.parametrize(("demande", "attendu"), [(3, 10), (5000, 600)])
    async def test_bornes_du_daemon_et_note(self, monkeypatch, demande, attendu):
        vu = _capture(monkeypatch, vb, "_post")
        r = await vb.generer_chanson(_CHANSON, duree_sec=demande, forcer=True)

        assert vu["body"]["duration_sec"] == attendu
        assert "ramenée" in r["note"]

    async def test_bpm_borne_et_dit(self, monkeypatch):
        vu = _capture(monkeypatch, vb, "_post")
        r = await vb.generer_chanson(_CHANSON, bpm=300)

        assert vu["body"]["bpm"] == 180
        assert "bpm ramené à 180" in r["note"]


class TestPannes:
    async def test_422_rend_le_detail_du_daemon(self, monkeypatch):
        _capture(monkeypatch, vb, "_post", _Resp({}, status_code=422, text="bpm: nope"))
        r = await vb.generer_chanson(_CHANSON)

        assert "bpm: nope" in r["error"]

    async def test_file_pleine(self, monkeypatch):
        _capture(monkeypatch, vb, "_post", _Resp({}, status_code=429))
        r = await vb.generer_chanson(_CHANSON)

        assert "file d'attente" in r["error"]

    async def test_daemon_injoignable(self, monkeypatch):
        async def _boom(path: str, json_body: dict):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(vb, "_post", _boom)
        r = await vb.generer_chanson(_CHANSON)

        assert "local-suno" in r["error"] and "session_id" not in r


# --------------------------------------------------------------------------- #
# klody_music_server.composer_demo                                             #
# --------------------------------------------------------------------------- #


_IDEE = {
    "genre": "neo soul",
    "ambiance": "mélancolique",
    "instrumentation": ["rhodes", "basse fretless"],
    "tonalite_suggeree": "F minor",
    "amorce_paroles": ["je te cherche encore", "dans le bruit de la ville"],
}


class TestComposerDemo:
    def test_la_borne_de_duree_nest_plus_figee_a_120(self):
        # Elle l'était, en citant « bornes daemon (ge=10 le=120) » — faux depuis
        # que le contrat est passé à 600. Toute démo au-delà de 2 min était
        # silencieusement coupée de moitié, sans que rien ne le signale.
        body, _ = km._idee_to_body(_IDEE, 300, "klody", 0, None, instrumental=True)
        assert body["duration_sec"] == 300

    def test_duree_deduite_de_lamorce(self):
        # Une amorce fait 2 à 4 vers : 30 s par défaut l'étirait à ~0,3 mot/s,
        # le cas que le daemon décrit comme « chant étiré et peu intelligible ».
        body, rapport = km._idee_to_body(_IDEE, None, "klody", 0, None)
        assert rapport is not None
        assert body["duration_sec"] == ss.duree_conseillee(rapport["mots"])
        assert body["duration_sec"] < 30

    def test_lamorce_part_balisee(self):
        body, _ = km._idee_to_body(_IDEE, None, "klody", 0, None)
        assert body["custom_lyrics"].startswith("[verse 1]")

    def test_instrumental_na_pas_de_rapport_de_couverture(self):
        # Aucune parole ⇒ rien à couvrir. Un rapport ici serait du bruit.
        body, rapport = km._idee_to_body(_IDEE, 60, "klody", 0, None, instrumental=True)
        assert rapport is None
        assert body["custom_lyrics"] == "[Instrumental]"

    async def test_demo_refusee_avant_le_post(self, monkeypatch):
        vu = _capture(monkeypatch, km, "_ls_post")
        idee = dict(_IDEE, amorce_paroles=[" ".join(["mot"] * 400)])
        r = await km.composer_demo(idee, duree_sec=240)

        assert "error" in r and vu == {}

    async def test_demo_nominale(self, monkeypatch):
        vu = _capture(monkeypatch, km, "_ls_post")
        r = await km.composer_demo(_IDEE)

        assert vu["body"]["custom_lyrics"].startswith("[verse 1]")
        assert r["demo"]["instrumental"] is False
        assert r["couverture"]["couvrable"] is True

    async def test_demo_instrumentale_inchangee(self, monkeypatch):
        # Non-régression : le chemin sans voix ne passe pas par le contrôle.
        vu = _capture(monkeypatch, km, "_ls_post")
        r = await km.composer_demo(_IDEE, instrumental=True)

        assert vu["body"]["instrumental"] is True
        assert r["demo"]["paroles"] is None
        assert r["couverture"] is None
