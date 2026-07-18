"""Tests pour klody_mcp/vlc_server.py — normalisation du status VLC, garde-fous
d'entrée (chemins ASI02 + anti-SSRF), et surtout le DIAGNOSTIC TRI-ÉTAT :
401 (vivant, mauvais mot de passe) ≠ ConnectError (down) ≠ 404 (pas VLC).
Confondre les trois est le bug de sonde qui a déjà coûté cher ailleurs.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from klody_mcp import vlc_server as vs

# ── Normalisation du status.json ──────────────────────────────────────────────


class TestResumeEtat:
    def test_extrait_titre_et_artiste_des_meta_imbriquees(self):
        raw = {
            "state": "playing",
            "time": 42,
            "length": 300,
            "position": 0.14,
            "volume": 256,
            "fullscreen": False,
            "information": {"category": {"meta": {"title": "Blue Train", "artist": "Coltrane"}}},
        }
        etat = vs._resume_etat(raw)
        assert etat["etat"] == "playing"
        assert etat["titre"] == "Blue Train"
        assert etat["artiste"] == "Coltrane"
        assert etat["position_s"] == 42
        assert etat["volume_pct"] == 100
        assert etat["progression_pct"] == 14.0

    def test_retombe_sur_filename_sans_titre(self):
        raw = {"information": {"category": {"meta": {"filename": "track01.flac"}}}}
        assert vs._resume_etat(raw)["titre"] == "track01.flac"

    def test_status_vide_ne_leve_pas(self):
        etat = vs._resume_etat({})
        assert etat["etat"] == "inconnu"
        assert etat["titre"] == ""
        assert etat["progression_pct"] is None

    def test_information_mal_typee_ignoree(self):
        # VLC renvoie parfois information: [] quand rien ne joue.
        assert vs._resume_etat({"information": []})["titre"] == ""

    @pytest.mark.parametrize("brut,attendu", [(0, 0), (128, 50), (256, 100), (512, 200)])
    def test_volume_converti_en_pourcent(self, brut, attendu):
        assert vs._pourcent_volume(brut) == attendu

    def test_volume_illisible_renvoie_none(self):
        assert vs._pourcent_volume("n/a") is None


class TestFeuillesPlaylist:
    def test_aplatit_larbre_et_marque_le_courant(self):
        arbre = {
            "type": "node",
            "children": [
                {
                    "type": "node",
                    "children": [
                        {"type": "leaf", "id": "5", "name": "a.mp3", "uri": "file:///a.mp3",
                         "duration": 180, "current": "current"},
                        {"type": "leaf", "id": "6", "name": "b.mp3", "uri": "file:///b.mp3",
                         "duration": 200},
                    ],
                }
            ],
        }
        pistes: list[dict] = []
        vs._feuilles(arbre, pistes)
        assert [p["nom"] for p in pistes] == ["a.mp3", "b.mp3"]
        assert pistes[0]["en_cours"] is True
        assert pistes[1]["en_cours"] is False

    def test_playlist_vide(self):
        pistes: list[dict] = []
        vs._feuilles({"type": "node", "children": []}, pistes)
        assert pistes == []


# ── Garde-fous d'entrée (ASI02 + anti-SSRF) ───────────────────────────────────


class TestResoudreMedia:
    def test_fichier_sous_racine_autorisee_devient_file_uri(self, tmp_path):
        f = tmp_path / "morceau.mp3"
        f.write_bytes(b"\x00")
        uri, err = vs._resoudre_media(str(f))
        assert err is None
        assert uri.startswith("file:///")
        assert uri.endswith("morceau.mp3")

    def test_traversal_refuse(self):
        uri, err = vs._resoudre_media("../../../etc/passwd")
        assert uri is None
        assert "racines autorisées" in err

    def test_fichier_sensible_hors_racines_refuse(self):
        uri, err = vs._resoudre_media("~/.ssh/id_ed25519")
        assert uri is None
        assert err

    def test_fichier_inexistant_sous_racine_refuse(self, tmp_path):
        uri, err = vs._resoudre_media(str(tmp_path / "fantome.mp3"))
        assert uri is None
        assert "non trouvé" in err

    def test_url_publique_acceptee(self):
        with patch.object(vs, "_url_privee", return_value=False):
            uri, err = vs._resoudre_media("https://stream.example.com/radio.mp3")
        assert err is None
        assert uri == "https://stream.example.com/radio.mp3"

    def test_url_loopback_refusee_par_defaut(self):
        uri, err = vs._resoudre_media("http://127.0.0.1:8000/api/secret")
        assert uri is None
        assert "réseau privé" in err

    def test_url_privee_autorisee_si_flag(self, monkeypatch):
        monkeypatch.setattr(vs, "ALLOW_PRIVATE_URLS", True)
        uri, err = vs._resoudre_media("http://192.168.1.20/media/film.mkv")
        assert err is None
        assert uri.startswith("http://192.168.1.20")

    @pytest.mark.parametrize("media", ["smb://nas/partage/x.mkv", "ftp://h/x.mp3", "file:///etc/passwd"])
    def test_schemas_non_http_refuses(self, media):
        uri, err = vs._resoudre_media(media)
        assert uri is None
        assert "non autorisé" in err

    def test_media_vide_refuse(self):
        uri, err = vs._resoudre_media("  ")
        assert uri is None
        assert "vide" in err


class TestValidationArguments:
    @pytest.mark.parametrize("val", ["90", "+30", "-10", "50%", "1h20m"])
    def test_seek_formes_valides(self, val):
        assert vs._valider_seek(val) == (val, None)

    @pytest.mark.parametrize("val", ["90&command=pl_empty", "../x", "90 ; rm", ""])
    def test_seek_formes_refusees(self, val):
        v, err = vs._valider_seek(val)
        assert v is None and err

    @pytest.mark.parametrize("pct,attendu", [(0, "0"), (50, "128"), (100, "256"), (200, "512")])
    def test_volume_converti(self, pct, attendu):
        assert vs._valider_volume(pct) == (attendu, None)

    @pytest.mark.parametrize("pct", [-1, 201, 9999, "fort", None])
    def test_volume_hors_bornes_refuse(self, pct):
        v, err = vs._valider_volume(pct)
        assert v is None and err


# ── Diagnostic tri-état (le cœur du sujet) ────────────────────────────────────


def _client_mock(reponse=None, exc=None):
    """Fabrique un faux httpx.AsyncClient utilisable en context manager."""
    client = AsyncMock()
    client.get = AsyncMock(side_effect=exc) if exc else AsyncMock(return_value=reponse)
    ctx = AsyncMock()
    ctx.__aenter__.return_value = client
    ctx.__aexit__.return_value = False
    return ctx


@pytest.fixture(autouse=True)
def _mot_de_passe(monkeypatch):
    """Sans mot de passe, _vlc_get court-circuite avant tout appel réseau."""
    monkeypatch.setattr(vs, "VLC_HTTP_PASSWORD", "secret")


class TestDiagnosticTriEtat:
    @pytest.mark.asyncio
    async def test_connexion_refusee_dit_down(self):
        with patch.object(vs.httpx, "AsyncClient", return_value=_client_mock(exc=httpx.ConnectError("refus"))):
            res = await vs._vlc_get("/requests/status.json")
        assert res["error"] == vs._ERR_DOWN
        assert "n'est pas lancé" in res["error"]

    @pytest.mark.asyncio
    async def test_401_dit_VIVANT_pas_down(self):
        # Le piège : 401 = VLC répond donc VLC tourne. Dire « down » enverrait
        # le diagnostic sur une fausse piste (relancer VLC ne répare rien).
        resp = httpx.Response(401, request=httpx.Request("GET", "http://x/requests/status.json"))
        with patch.object(vs.httpx, "AsyncClient", return_value=_client_mock(reponse=resp)):
            res = await vs._vlc_get("/requests/status.json")
        assert res["error"] == vs._ERR_AUTH
        assert "VIVANT" in res["error"]
        assert res["error"] != vs._ERR_DOWN

    @pytest.mark.asyncio
    async def test_404_dit_pas_vlc(self):
        resp = httpx.Response(404, request=httpx.Request("GET", "http://x/requests/status.json"))
        with patch.object(vs.httpx, "AsyncClient", return_value=_client_mock(reponse=resp)):
            res = await vs._vlc_get("/requests/status.json")
        assert res["error"] == vs._ERR_NOT_VLC

    @pytest.mark.asyncio
    async def test_timeout_nest_pas_un_down(self):
        with patch.object(vs.httpx, "AsyncClient", return_value=_client_mock(exc=httpx.ReadTimeout("lent"))):
            res = await vs._vlc_get("/requests/status.json")
        assert "figé" in res["error"]
        assert res["error"] != vs._ERR_DOWN

    @pytest.mark.asyncio
    async def test_sans_mot_de_passe_diagnostic_dedie(self, monkeypatch):
        monkeypatch.setattr(vs, "VLC_HTTP_PASSWORD", "")
        res = await vs._vlc_get("/requests/status.json")
        assert res["error"] == vs._ERR_NO_PASSWORD

    @pytest.mark.asyncio
    async def test_json_illisible_ne_leve_pas(self):
        resp = httpx.Response(200, text="<html>pas du json</html>",
                              request=httpx.Request("GET", "http://x/"))
        with patch.object(vs.httpx, "AsyncClient", return_value=_client_mock(reponse=resp)):
            res = await vs._vlc_get("/requests/status.json")
        assert "illisible" in res["error"]

    @pytest.mark.asyncio
    async def test_succes_renvoie_le_json(self):
        resp = httpx.Response(200, json={"state": "playing", "volume": 256},
                              request=httpx.Request("GET", "http://x/"))
        with patch.object(vs.httpx, "AsyncClient", return_value=_client_mock(reponse=resp)):
            res = await vs._vlc_get("/requests/status.json")
        assert res["state"] == "playing"

    @pytest.mark.asyncio
    async def test_command_propage_lerreur_sans_la_normaliser(self):
        with patch.object(vs.httpx, "AsyncClient", return_value=_client_mock(exc=httpx.ConnectError("refus"))):
            res = await vs._command("pl_pause")
        assert res == {"error": vs._ERR_DOWN}  # pas un faux état "inconnu"


# ── Commandes asynchrones : ne jamais rendre le status d'AVANT ────────────────


class TestCommandeConfirmee:
    """VLC applique ses commandes en asynchrone et renvoie le status d'AVANT
    dans la réponse à la commande. Rendre ce status ferait mentir l'outil
    (`stop` répondait « playing »). _command doit RELIRE l'état."""

    @pytest.mark.asyncio
    async def test_ne_rend_pas_le_status_pre_commande(self, monkeypatch):
        monkeypatch.setattr(vs.asyncio, "sleep", AsyncMock())
        reponses = [
            {"state": "playing"},  # réponse à la commande = état d'AVANT
            {"state": "stopped"},  # relecture = état RÉEL
        ]
        with patch.object(vs, "_vlc_get", AsyncMock(side_effect=reponses)):
            res = await vs._command("pl_stop", attendre=lambda e: e["etat"] == "stopped")
        assert res["etat"] == "stopped"

    @pytest.mark.asyncio
    async def test_boucle_jusqua_satisfaction_du_predicat(self, monkeypatch):
        monkeypatch.setattr(vs.asyncio, "sleep", AsyncMock())
        reponses = [
            {"state": "stopped"},  # commande
            {"state": "stopped"},  # VLC n'a pas encore démarré
            {"state": "stopped"},
            {"state": "playing"},  # ça y est
        ]
        with patch.object(vs, "_vlc_get", AsyncMock(side_effect=reponses)):
            res = await vs._command("in_play", attendre=lambda e: e["etat"] == "playing")
        assert res["etat"] == "playing"

    @pytest.mark.asyncio
    async def test_predicat_jamais_satisfait_rend_le_dernier_etat_observe(self, monkeypatch):
        monkeypatch.setattr(vs.asyncio, "sleep", AsyncMock())
        with patch.object(vs, "_vlc_get", AsyncMock(return_value={"state": "stopped"})):
            res = await vs._command(
                "in_play", attendre=lambda e: e["etat"] == "playing", essais=3
            )
        assert res["etat"] == "stopped"  # pas de succès inventé

    @pytest.mark.asyncio
    async def test_erreur_pendant_la_relecture_remonte(self, monkeypatch):
        monkeypatch.setattr(vs.asyncio, "sleep", AsyncMock())
        reponses = [{"state": "playing"}, {"error": vs._ERR_DOWN}]
        with patch.object(vs, "_vlc_get", AsyncMock(side_effect=reponses)):
            res = await vs._command("pl_stop")
        assert res == {"error": vs._ERR_DOWN}
