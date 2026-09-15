"""Tests du pont sémantique SampleBrain (klody_mcp.reaper_samples), voie HTTP.

Aucun modèle, aucun index, aucun réseau : ces tests visent la LOGIQUE DE PONT
— détection d'indisponibilité, choix du moteur, fusion des bibliothèques,
filtrage des résultats — qui est justement la partie que la vraie recherche ne
met pas à l'épreuve. `_get_json` est le seul point qui touche le réseau : il
est bouchonné. Le bout en bout (2 serveurs réels + CLAP) est prouvé hors CI.

Ce qu'on refuse de tester ici : la pertinence sémantique. Elle appartient au
dépôt SampleBrain, qui la mesure sur un jeu de requêtes gelé.
"""
from __future__ import annotations

import pytest
from klody_mcp import reaper_samples as rs

DEUX = "principale=http://127.0.0.1:8788,externe=http://127.0.0.1:8799"


@pytest.fixture(autouse=True)
def _env_neutre(monkeypatch):
    monkeypatch.setenv("SAMPLEBRAIN_URLS", DEUX)
    monkeypatch.delenv("KLODY_SAMPLEBRAIN", raising=False)
    monkeypatch.setattr(rs, "_sb_derniere_erreur", "", raising=False)


def _bibliotheque(tmp_path):
    lib = tmp_path / "lib"
    (lib / "pads").mkdir(parents=True)
    (lib / "pads" / "dark_pad.wav").write_bytes(b"RIFF")
    (lib / "pads" / "bright_pad.wav").write_bytes(b"RIFF")
    (lib / "kick_lourd.wav").write_bytes(b"RIFF")
    return lib


class _FauxHTTP:
    """Tient le contrat de `_get_json` : (url, params, timeout) → payload JSON.
    `reponses` = {suffixe d'url: payload | Exception}. Enregistre les appels."""

    def __init__(self, reponses):
        self.reponses = reponses
        self.appels: list[tuple[str, dict | None]] = []

    def __call__(self, url, params, timeout):
        self.appels.append((url, params))
        for suffixe, rep in self.reponses.items():
            if url.endswith(suffixe):
                if isinstance(rep, Exception):
                    raise rep
                return rep
        raise ConnectionError(f"pas de réponse prévue pour {url}")


def _hits(*paths, model="clap", d0=0.3):
    return {"model": model,
            "hits": [{"content_hash": f"h{i}", "distance": d0 + i / 10, "paths": [p]}
                     for i, p in enumerate(paths)]}


class TestDisponibilite:
    def test_interrupteur_coupe_le_moteur(self, monkeypatch):
        monkeypatch.setenv("KLODY_SAMPLEBRAIN", "0")
        st = rs.semantic_status()
        assert st["available"] is False
        assert "KLODY_SAMPLEBRAIN" in st["reason"]

    @pytest.mark.parametrize("valeur", ["0", "false", "OFF", "no"])
    def test_valeurs_qui_coupent(self, monkeypatch, valeur):
        monkeypatch.setenv("KLODY_SAMPLEBRAIN", valeur)
        assert rs.semantic_enabled() is False

    @pytest.mark.parametrize("valeur", ["1", "true", "", "nimporte"])
    def test_valeurs_qui_ne_coupent_pas(self, monkeypatch, valeur):
        monkeypatch.setenv("KLODY_SAMPLEBRAIN", valeur)
        assert rs.semantic_enabled() is True

    def test_aucun_serveur_est_une_raison_lisible(self, monkeypatch):
        monkeypatch.setattr(rs, "_get_json", _FauxHTTP({}))
        st = rs.semantic_status()
        assert st["available"] is False
        assert "8788" in st["reason"] and "8799" in st["reason"]
        assert "samplebrain-index serve" in st["reason"]
        assert st["vivantes"] == []

    def test_partiel_reste_disponible_et_le_dit(self, monkeypatch):
        faux = _FauxHTTP({"8788/api/status": {"model": "clap"}})
        monkeypatch.setattr(rs, "_get_json", faux)
        st = rs.semantic_status()
        assert st["available"] is True
        assert st["vivantes"] == ["principale"]
        assert "externe" in st["reason"]
        assert st["modeles"] == {"principale": "clap"}
        assert st["bibliotheques"] == {"principale": "http://127.0.0.1:8788",
                                       "externe": "http://127.0.0.1:8799"}

    def test_le_statut_ne_lance_aucune_recherche(self, monkeypatch):
        faux = _FauxHTTP({"/api/status": {"model": "clap"}})
        monkeypatch.setattr(rs, "_get_json", faux)
        rs.semantic_status()
        assert all(u.endswith("/api/status") for u, _ in faux.appels)


class TestConversionDeScore:
    def test_distance_nulle_vaut_similarite_un(self):
        assert rs._similarite(0.0) == 1.0

    def test_formule_verifiee_sur_lindex_reel(self):
        # `_distance = 2 - 2*cos` (concordance à 1e-6 mesurée sur l'index) :
        # une distance de 0,679341 correspond à un cosinus de 0,660329.
        assert rs._similarite(0.679341) == 0.6603

    def test_bornee_entre_zero_et_un(self):
        assert rs._similarite(4.0) == 0.0      # cos = -1
        assert rs._similarite(-0.5) == 1.0     # bruit numérique


class TestFusionDesBibliotheques:
    def test_les_deux_index_sont_interroges_en_local(self, monkeypatch):
        faux = _FauxHTTP({"8788/api/search": _hits("/A/a.wav"),
                          "8799/api/search": _hits("/B/b.wav", d0=0.1)})
        monkeypatch.setattr(rs, "_get_json", faux)
        hits = rs._hits_http("kick", k=20)
        assert [h["paths"][0] for h in hits] == ["/B/b.wav", "/A/a.wav"]   # trié par distance
        assert all(p["local"] == "1" and p["k"] == 20 for _, p in faux.appels)

    def test_modeles_differents_gardent_l_ordre_par_bibliotheque(self, monkeypatch):
        faux = _FauxHTTP({"8788/api/search": _hits("/A/a.wav", d0=0.9),
                          "8799/api/search": _hits("/B/b.wav", model="autre", d0=0.1)})
        monkeypatch.setattr(rs, "_get_json", faux)
        hits = rs._hits_http("kick", k=5)
        # externe < principale alphabétiquement, distances NON comparables → pas retrié
        assert [h["paths"][0] for h in hits] == ["/B/b.wav", "/A/a.wav"]

    def test_un_serveur_muet_n_emporte_pas_l_autre(self, monkeypatch):
        faux = _FauxHTTP({"8788/api/search": _hits("/A/a.wav"),
                          "8799/api/search": ConnectionError("down")})
        monkeypatch.setattr(rs, "_get_json", faux)
        assert [h["paths"][0] for h in rs._hits_http("kick", 5)] == ["/A/a.wav"]
        assert "externe" in rs._sb_derniere_erreur

    def test_tout_muet_rend_vide(self, monkeypatch):
        monkeypatch.setattr(rs, "_get_json", _FauxHTTP({}))
        assert rs._hits_http("kick", 5) == []


class TestFiltrageDesResultats:
    def _brancher(self, monkeypatch, *paths, d0=0.5):
        faux = _FauxHTTP({"/api/search": _hits(*paths, d0=d0)})
        monkeypatch.setattr(rs, "_get_json", faux)
        return faux

    def test_hors_racine_est_ecarte(self, tmp_path, monkeypatch):
        lib = _bibliotheque(tmp_path)
        ailleurs = tmp_path / "ailleurs"
        ailleurs.mkdir()
        (ailleurs / "intrus.wav").write_bytes(b"RIFF")
        self._brancher(monkeypatch, str(ailleurs / "intrus.wav"), str(lib / "pads" / "dark_pad.wav"))
        hits = rs._search_samplebrain("pad", [lib], limit=10)
        assert [h["name"] for h in hits] == ["dark_pad.wav"]

    def test_chemin_mort_est_ecarte(self, tmp_path, monkeypatch):
        """Un index a le droit d'avoir une longueur de retard sur le disque."""
        lib = _bibliotheque(tmp_path)
        self._brancher(monkeypatch, str(lib / "supprime.wav"), str(lib / "kick_lourd.wav"))
        hits = rs._search_samplebrain("kick", [lib], limit=10)
        assert [h["name"] for h in hits] == ["kick_lourd.wav"]

    def test_plusieurs_chemins_pour_un_contenu(self, tmp_path, monkeypatch):
        lib = _bibliotheque(tmp_path)
        a, b = lib / "pads" / "dark_pad.wav", lib / "pads" / "bright_pad.wav"
        faux = _FauxHTTP({"/api/search": {"model": "clap", "hits": [
            {"content_hash": "x", "distance": 0.4, "paths": [str(a), str(b), str(a)]}]}})
        monkeypatch.setattr(rs, "_get_json", faux)
        hits = rs._search_samplebrain("pad", [lib], limit=10)
        # 2 bibliothèques rendent le même hit : dédupliqué par chemin
        assert [h["name"] for h in hits] == ["dark_pad.wav", "bright_pad.wav"]
        assert {h["score"] for h in hits} == {0.8}

    def test_limite_respectee(self, tmp_path, monkeypatch):
        lib = _bibliotheque(tmp_path)
        self._brancher(monkeypatch, str(lib / "pads" / "dark_pad.wav"),
                       str(lib / "pads" / "bright_pad.wav"), str(lib / "kick_lourd.wav"))
        assert len(rs._search_samplebrain("x", [lib], limit=2)) == 2

    def test_champs_du_contrat(self, tmp_path, monkeypatch):
        lib = _bibliotheque(tmp_path)
        self._brancher(monkeypatch, str(lib / "pads" / "dark_pad.wav"))
        h = rs._search_samplebrain("pad", [lib], limit=1)[0]
        assert set(h) == {"path", "name", "rel", "root", "score", "via"}
        assert h["via"] == "samplebrain"
        assert h["rel"] == "pads/dark_pad.wav"
        assert h["root"] == str(lib)

    def test_interrupteur_ne_touche_pas_le_reseau(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KLODY_SAMPLEBRAIN", "0")
        faux = _FauxHTTP({})
        monkeypatch.setattr(rs, "_get_json", faux)
        assert rs._search_samplebrain("pad", [tmp_path], 5) == []
        assert faux.appels == []


class TestChoixDuMoteur:
    def test_mode_inconnu_est_refuse(self):
        with pytest.raises(ValueError, match="mode inconnu"):
            rs.search_samples("kick", mode="magique")

    def test_auto_se_rabat_quand_le_semantique_ne_repond_pas(self, tmp_path, monkeypatch):
        lib = _bibliotheque(tmp_path)
        monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))
        monkeypatch.setattr(rs, "_get_json", _FauxHTTP({}))
        hits = rs.search_samples("kick")
        assert hits and hits[0]["via"] == "filesystem"
        assert hits[0]["name"] == "kick_lourd.wav"

    def test_samplebrain_assume_son_vide(self, tmp_path, monkeypatch):
        lib = _bibliotheque(tmp_path)
        monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))
        monkeypatch.setattr(rs, "_get_json", _FauxHTTP({}))
        assert rs.search_samples("kick", mode="samplebrain") == []

    def test_filesystem_n_interroge_jamais_le_reseau(self, tmp_path, monkeypatch):
        lib = _bibliotheque(tmp_path)
        monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))

        def _interdit(*a, **k):
            raise AssertionError("le mode filesystem ne doit pas toucher le réseau")

        monkeypatch.setattr(rs, "_get_json", _interdit)
        hits = rs.search_samples("kick", mode="filesystem")
        assert hits and hits[0]["via"] == "filesystem"

    def test_requete_vide_part_au_filesystem(self, tmp_path, monkeypatch):
        lib = _bibliotheque(tmp_path)
        monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))

        def _interdit(*a, **k):
            raise AssertionError("une requête vide ne doit pas toucher le réseau")

        monkeypatch.setattr(rs, "_get_json", _interdit)
        hits = rs.search_samples("", limit=10)
        assert len(hits) == 3
        assert {h["via"] for h in hits} == {"filesystem"}

    def test_semantique_prioritaire_quand_il_repond(self, tmp_path, monkeypatch):
        lib = _bibliotheque(tmp_path)
        monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))
        faux = _FauxHTTP({"/api/search": _hits(str(lib / "kick_lourd.wav"))})
        monkeypatch.setattr(rs, "_get_json", faux)
        hits = rs.search_samples("nappe sombre", limit=5)
        assert [h["via"] for h in hits] == ["samplebrain"]
        assert hits[0]["name"] == "kick_lourd.wav"
        assert faux.appels, "les serveurs doivent avoir été interrogés"

    def test_la_marge_demandee_depasse_la_limite(self, tmp_path, monkeypatch):
        lib = _bibliotheque(tmp_path)
        monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))
        faux = _FauxHTTP({"/api/search": {"model": "clap", "hits": []}})
        monkeypatch.setattr(rs, "_get_json", faux)
        rs.search_samples("pad", limit=5)
        assert faux.appels[0][1]["k"] >= 20
