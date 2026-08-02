"""Tests du pont sémantique SampleBrain (klody_mcp.reaper_samples).

Aucun modèle, aucun index, aucun réseau : ces tests visent la LOGIQUE DE PONT
— détection d'indisponibilité, choix du moteur, filtrage des résultats — qui
est justement la partie que la vraie recherche ne met pas à l'épreuve. Le
chemin de bout en bout (index réel + CLAP réel) est prouvé hors CI, ses
chiffres sont dans le message de commit et dans la docstring du module.

Ce qu'on refuse de tester ici : la pertinence sémantique. Elle appartient au
dépôt SampleBrain, qui la mesure sur un jeu de requêtes gelé.
"""
from __future__ import annotations

import pytest
from klody_mcp import reaper_samples as rs


@pytest.fixture(autouse=True)
def _index_neutre(monkeypatch, tmp_path):
    """Part d'un état vierge : aucun indexeur en cache, home vide.

    `_sb_indexer` est un cache de module. Sans cette remise à zéro, un test qui
    installe un faux indexeur le laisserait au suivant — et le suivant
    passerait pour de mauvaises raisons.
    """
    monkeypatch.setattr(rs, "_sb_indexer", None, raising=False)
    monkeypatch.setattr(rs, "_sb_derniere_erreur", "", raising=False)
    monkeypatch.setenv("SAMPLEBRAIN_HOME", str(tmp_path / "sb"))
    monkeypatch.delenv("KLODY_SAMPLEBRAIN", raising=False)


def _bibliotheque(tmp_path):
    lib = tmp_path / "lib"
    (lib / "pads").mkdir(parents=True)
    (lib / "pads" / "dark_pad.wav").write_bytes(b"RIFF")
    (lib / "pads" / "bright_pad.wav").write_bytes(b"RIFF")
    (lib / "kick_lourd.wav").write_bytes(b"RIFF")
    return lib


class _FauxIndexeur:
    """Tient le contrat de `Indexer.search_text` — rien de plus."""

    def __init__(self, resultats):
        self.resultats = resultats
        self.appels: list[tuple[str, int]] = []

    def search_text(self, query, k=10):
        self.appels.append((query, k))
        return self.resultats


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

    def test_index_absent_est_une_raison_lisible(self, tmp_path):
        st = rs.semantic_status()
        # Le paquet peut être installé ou non selon la machine ; dans les deux
        # cas la raison doit NOMMER ce qui manque, jamais rester vide.
        assert st["available"] is False
        assert st["reason"], "une indisponibilité sans raison est indiagnosticable"
        assert str(tmp_path) in st["home"]

    def test_indexeur_none_quand_indisponible(self):
        assert rs._samplebrain_indexer() is None

    def test_echec_non_mis_en_cache(self, monkeypatch):
        """Un index construit après le démarrage doit être pris sans redémarrage."""
        assert rs._samplebrain_indexer() is None
        faux = _FauxIndexeur([])
        monkeypatch.setattr(rs, "semantic_status", lambda: {"available": True})
        monkeypatch.setattr(rs, "_sb_indexer", faux, raising=False)
        assert rs._samplebrain_indexer() is faux


class TestOuvertureQuiEchoue:
    """Le chemin d'échec de l'ouverture de l'index.

    Un garde-fou qui ne peut pas rougir est indiscernable d'un garde-fou vert :
    ces deux tests forcent l'échec au lieu de l'espérer. Ils ont besoin du vrai
    paquet installé — pas de son index, seulement du paquet — et se sautent
    proprement là où il n'est pas (la CI, par exemple).
    """

    @pytest.fixture
    def _index_bidon(self, tmp_path, monkeypatch):
        pytest.importorskip("samplebrain", reason="paquet SampleBrain non installé")
        home = tmp_path / "sb"
        home.mkdir(parents=True, exist_ok=True)
        (home / "catalog.sqlite3").write_bytes(b"")  # existe -> le statut passe
        monkeypatch.setenv("SAMPLEBRAIN_HOME", str(home))
        return home

    def test_erreur_d_ouverture_est_retenue_et_lisible(self, _index_bidon, monkeypatch):
        from samplebrain.indexer.service import Indexer

        def _explose(*a, **k):
            raise RuntimeError("disque en carton")

        monkeypatch.setattr(Indexer, "open", staticmethod(_explose))
        assert rs._samplebrain_indexer() is None
        st = rs.semantic_status()
        assert st["available"] is False
        assert "disque en carton" in st["reason"]
        assert "ouverture de l'index impossible" in st["reason"]

    def test_statut_disponible_quand_tout_est_en_place(self, _index_bidon):
        # Catalogue présent, aucune erreur retenue : le statut doit dire oui
        # sans avoir rien chargé de lourd.
        st = rs.semantic_status()
        assert st == {"home": str(_index_bidon), "charge": False,
                      "available": True, "reason": ""}


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


class TestFiltrageDesResultats:
    def test_hors_racine_est_ecarte(self, tmp_path, monkeypatch):
        lib = _bibliotheque(tmp_path)
        ailleurs = tmp_path / "ailleurs"
        ailleurs.mkdir()
        (ailleurs / "intrus.wav").write_bytes(b"RIFF")
        faux = _FauxIndexeur([
            {"content_hash": "a", "distance": 0.5, "paths": [str(ailleurs / "intrus.wav")]},
            {"content_hash": "b", "distance": 0.6, "paths": [str(lib / "pads" / "dark_pad.wav")]},
        ])
        monkeypatch.setattr(rs, "_samplebrain_indexer", lambda: faux)
        hits = rs._search_samplebrain("pad", [lib], limit=10)
        assert [h["name"] for h in hits] == ["dark_pad.wav"]

    def test_chemin_mort_est_ecarte(self, tmp_path, monkeypatch):
        """Un index a le droit d'avoir une longueur de retard sur le disque.

        Rendre un chemin disparu ferait échouer `import_sample` plus loin, avec
        une erreur qui n'aurait plus rien à voir avec la recherche.
        """
        lib = _bibliotheque(tmp_path)
        faux = _FauxIndexeur([
            {"content_hash": "a", "distance": 0.2, "paths": [str(lib / "supprime.wav")]},
            {"content_hash": "b", "distance": 0.6, "paths": [str(lib / "kick_lourd.wav")]},
        ])
        monkeypatch.setattr(rs, "_samplebrain_indexer", lambda: faux)
        hits = rs._search_samplebrain("kick", [lib], limit=10)
        assert [h["name"] for h in hits] == ["kick_lourd.wav"]

    def test_plusieurs_chemins_pour_un_contenu(self, tmp_path, monkeypatch):
        """Un contenu peut vivre à plusieurs chemins : ils sortent tous, une fois."""
        lib = _bibliotheque(tmp_path)
        a, b = lib / "pads" / "dark_pad.wav", lib / "pads" / "bright_pad.wav"
        faux = _FauxIndexeur([
            {"content_hash": "x", "distance": 0.4, "paths": [str(a), str(b), str(a)]},
        ])
        monkeypatch.setattr(rs, "_samplebrain_indexer", lambda: faux)
        hits = rs._search_samplebrain("pad", [lib], limit=10)
        assert [h["name"] for h in hits] == ["dark_pad.wav", "bright_pad.wav"]
        assert {h["score"] for h in hits} == {0.8}

    def test_limite_respectee(self, tmp_path, monkeypatch):
        lib = _bibliotheque(tmp_path)
        faux = _FauxIndexeur([
            {"content_hash": "x", "distance": 0.4,
             "paths": [str(lib / "pads" / "dark_pad.wav"),
                       str(lib / "pads" / "bright_pad.wav"),
                       str(lib / "kick_lourd.wav")]},
        ])
        monkeypatch.setattr(rs, "_samplebrain_indexer", lambda: faux)
        assert len(rs._search_samplebrain("x", [lib], limit=2)) == 2

    def test_champs_du_contrat(self, tmp_path, monkeypatch):
        lib = _bibliotheque(tmp_path)
        faux = _FauxIndexeur([
            {"content_hash": "x", "distance": 0.5,
             "paths": [str(lib / "pads" / "dark_pad.wav")]},
        ])
        monkeypatch.setattr(rs, "_samplebrain_indexer", lambda: faux)
        h = rs._search_samplebrain("pad", [lib], limit=1)[0]
        assert set(h) == {"path", "name", "rel", "root", "score", "via"}
        assert h["via"] == "samplebrain"
        assert h["rel"] == "pads/dark_pad.wav"
        assert h["root"] == str(lib)


class TestChoixDuMoteur:
    def test_mode_inconnu_est_refuse(self):
        with pytest.raises(ValueError, match="mode inconnu"):
            rs.search_samples("kick", mode="magique")

    def test_auto_se_rabat_quand_le_semantique_ne_repond_pas(self, tmp_path, monkeypatch):
        lib = _bibliotheque(tmp_path)
        monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))
        monkeypatch.setattr(rs, "_samplebrain_indexer", lambda: None)
        hits = rs.search_samples("kick")
        assert hits and hits[0]["via"] == "filesystem"
        assert hits[0]["name"] == "kick_lourd.wav"

    def test_samplebrain_assume_son_vide(self, tmp_path, monkeypatch):
        """Mode exigé : pas de repli, sinon le mode ne veut rien dire."""
        lib = _bibliotheque(tmp_path)
        monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))
        monkeypatch.setattr(rs, "_samplebrain_indexer", lambda: None)
        assert rs.search_samples("kick", mode="samplebrain") == []

    def test_filesystem_n_interroge_jamais_l_index(self, tmp_path, monkeypatch):
        lib = _bibliotheque(tmp_path)
        monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))

        def _interdit():
            raise AssertionError("le mode filesystem ne doit pas ouvrir l'index")

        monkeypatch.setattr(rs, "_samplebrain_indexer", _interdit)
        hits = rs.search_samples("kick", mode="filesystem")
        assert hits and hits[0]["via"] == "filesystem"

    def test_requete_vide_part_au_filesystem(self, tmp_path, monkeypatch):
        """« Rien » n'a pas de voisin sémantique, mais lister reste utile."""
        lib = _bibliotheque(tmp_path)
        monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))

        def _interdit():
            raise AssertionError("une requête vide ne doit pas ouvrir l'index")

        monkeypatch.setattr(rs, "_samplebrain_indexer", _interdit)
        hits = rs.search_samples("", limit=10)
        assert len(hits) == 3
        assert {h["via"] for h in hits} == {"filesystem"}

    def test_semantique_prioritaire_quand_il_repond(self, tmp_path, monkeypatch):
        """Le point du câblage : la requête « pad » ne matche AUCUN nom de
        fichier contenant « pad » mieux que le sémantique ne le fait, et c'est
        bien le sémantique qui répond quand il est là."""
        lib = _bibliotheque(tmp_path)
        monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))
        faux = _FauxIndexeur([
            {"content_hash": "x", "distance": 0.3, "paths": [str(lib / "kick_lourd.wav")]},
        ])
        monkeypatch.setattr(rs, "_samplebrain_indexer", lambda: faux)
        hits = rs.search_samples("nappe sombre", limit=5)
        assert [h["via"] for h in hits] == ["samplebrain"]
        assert hits[0]["name"] == "kick_lourd.wav"
        assert faux.appels, "l'index doit avoir été interrogé"

    def test_la_marge_demandee_depasse_la_limite(self, tmp_path, monkeypatch):
        """On demande large à l'index : les filtres racine/existence retirent
        des résultats après coup, demander exactement `limit` en rendrait moins."""
        lib = _bibliotheque(tmp_path)
        monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))
        faux = _FauxIndexeur([])
        monkeypatch.setattr(rs, "_samplebrain_indexer", lambda: faux)
        rs.search_samples("pad", limit=5)
        assert faux.appels[0][1] >= 20
