"""Tests pour tools/code_search — embeddings + cosine.

Les tests qui dépendent d'Ollama sont marqués @slow et skipés si Ollama down.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from tools.code_search import (
    MISS_EMPTY_INDEX,
    MISS_ENGINE,
    MISS_QUERY_EMBED,
    EmbeddingIndex,
    SearchHit,
    _cosine,
    format_hits,
    format_miss,
)

# ── Cosine ─────────────────────────────────────────────────────────────────────


class TestCosine:
    def test_identique(self):
        assert _cosine([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_orthogonal(self):
        assert _cosine([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_oppose(self):
        assert _cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)

    def test_vecteur_nul(self):
        assert _cosine([0, 0], [1, 1]) == 0.0

    def test_dimensions_differentes(self):
        assert _cosine([1, 0], [1, 0, 0]) == 0.0

    def test_vide(self):
        assert _cosine([], [1, 2]) == 0.0


# ── Formatters ─────────────────────────────────────────────────────────────────


class TestFormatHits:
    def test_vide_nomme_la_panne_et_ne_conclut_pas(self):
        # Audit 27/07 : « Aucun fichier pertinent trouvé. » se lisait comme
        # « ce code n'existe pas ». search() n'a AUCUN seuil de pertinence →
        # zéro hit = panne moteur/index, jamais un verdict sur le code.
        s = format_hits([])
        assert "PANNE" in s
        assert "Ne conclus donc PAS" in s
        assert "search_in_files" in s
        assert "Aucun fichier pertinent" not in s

    def test_avec_resultats(self):
        hits = [
            SearchHit(rel_path="a.py", score=0.9, preview="contenu a"),
            SearchHit(rel_path="b/c.py", score=0.7, preview="contenu c"),
        ]
        s = format_hits(hits)
        assert "0.900" in s
        assert "a.py" in s
        assert "b/c.py" in s


# ── EmbeddingIndex avec embeddings simulés ────────────────────────────────────


class TestEmbeddingIndexMocked:
    def test_unavailable_si_moteur_embeddings_absent(self, tmp_path, monkeypatch):
        # Depuis la migration vers le memory bus (2026-07-18), l'indisponibilité
        # n'est plus un échec réseau mais une dépendance manquante.
        from tools import embeddings

        monkeypatch.setattr(embeddings, "is_available", lambda: False)
        idx = EmbeddingIndex(tmp_path)
        assert idx.is_available() is False
        # refresh ne doit rien faire si indispo
        assert idx.refresh() == 0
        # search retourne vide
        assert idx.search("anything") == []

    def test_moteur_absent_pose_la_cause(self, tmp_path, monkeypatch):
        from tools import embeddings

        monkeypatch.setattr(embeddings, "is_available", lambda: False)
        idx = EmbeddingIndex(tmp_path)
        assert idx.search("anything") == []
        assert idx.last_miss == MISS_ENGINE

    def test_index_vide_pose_la_cause(self, tmp_path, monkeypatch):
        # Moteur VIVANT mais racine sans fichier indexable : l'ancien code
        # tombait sur « Aucun fichier pertinent trouvé » — un verdict, pas une
        # panne. Ce chemin n'était couvert par AUCUN garde (audit 27/07).
        from tools import embeddings

        monkeypatch.setattr(embeddings, "is_available", lambda: True)
        idx = EmbeddingIndex(tmp_path)  # tmp_path vide
        assert idx.search("anything") == []
        assert idx.last_miss == MISS_EMPTY_INDEX

    def test_embed_de_requete_vide_pose_la_cause(self, tmp_path, monkeypatch):
        # Cas le plus vicieux : index peuplé, moteur « available », mais
        # l'embedding de la requête rend vide (bge-m3 muet, cf. incident
        # nightly vec-down). Zéro hit → panne, surtout pas « rien de pertinent ».
        from tools import embeddings

        (tmp_path / "a.py").write_text("def foo(): pass\n", encoding="utf-8")
        calls = {"n": 0}

        def fake_batch(texts):
            calls["n"] += 1
            # 1er appel = indexation des fichiers (OK), 2e = requête (KO).
            return [] if calls["n"] > 1 else [[1.0, 0.0, 0.0] for _ in texts]

        monkeypatch.setattr(embeddings, "is_available", lambda: True)
        monkeypatch.setattr(embeddings, "embed_batch", fake_batch)

        idx = EmbeddingIndex(tmp_path)
        assert idx.refresh() == 1
        assert idx.search("anything") == []
        assert idx.last_miss == MISS_QUERY_EMBED

    def test_last_miss_remis_a_zero_quand_la_recherche_rend_des_hits(
        self, tmp_path, monkeypatch
    ):
        from tools import embeddings

        (tmp_path / "a.py").write_text("def foo(): pass\n", encoding="utf-8")
        monkeypatch.setattr(embeddings, "is_available", lambda: True)
        monkeypatch.setattr(
            embeddings, "embed_batch", lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
        )
        idx = EmbeddingIndex(tmp_path)
        idx.last_miss = MISS_ENGINE  # résidu d'une recherche précédente
        assert idx.search("anything", k=1)
        assert idx.last_miss is None

    def test_format_miss_nomme_chaque_cause(self):
        for reason in (MISS_ENGINE, MISS_EMPTY_INDEX, MISS_QUERY_EMBED):
            msg = format_miss(reason)
            assert reason in msg
            assert "PANNE" in msg
            assert "Ne conclus donc PAS" in msg

    def test_format_miss_sans_cause_reste_prudent(self):
        msg = format_miss(None)
        assert "cause inconnue" in msg
        assert "Ne conclus donc PAS" in msg

    def test_skip_venv_et_node_modules(self, tmp_path):
        (tmp_path / "ok.py").write_text("print('hello')", encoding="utf-8")
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "fake.py").write_text("x = 1", encoding="utf-8")
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {}", encoding="utf-8")
        idx = EmbeddingIndex(tmp_path)
        files = list(idx._iter_source_files())
        names = {f.name for f in files}
        assert "ok.py" in names
        assert "fake.py" not in names
        assert "index.js" not in names

    def test_search_via_mock(self, tmp_path, monkeypatch):
        """Flow end-to-end avec un moteur d'embeddings simulé.

        Le mock porte sur `tools.embeddings` (la couture depuis 2026-07-18). Sans
        lui le test calculerait de VRAIS vecteurs bge-m3 : lent, et il ne testerait
        plus le flow mais le modèle.
        """
        (tmp_path / "a.py").write_text("def foo(): pass\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("def bar(): pass\n", encoding="utf-8")

        from tools import embeddings

        def fake_batch(texts):
            # Vecteur déterministe dépendant du contenu.
            return [[(len(t) % 7) / 10.0, 1.0 - (len(t) % 7) / 10.0, 0.5] for t in texts]

        monkeypatch.setattr(embeddings, "is_available", lambda: True)
        monkeypatch.setattr(embeddings, "embed_batch", fake_batch)

        idx = EmbeddingIndex(tmp_path)
        n = idx.refresh()
        assert n == 2
        hits = idx.search("query", k=2)
        assert len(hits) == 2
        # Scores entre -1 et 1 (tolérance FP)
        for h in hits:
            assert -1.001 <= h.score <= 1.001
        # Triés décroissant
        assert hits[0].score >= hits[1].score


# ── Persistance de l'index (2026-11-19) ───────────────────────────────────────
#
# Vécu le 2026-11-19 (enquête latence Siri/iMessage) : la première recherche de
# CHAQUE processus ré-encodait ~550 fichiers, 160-171 s sur bge-m3/mps. L'index
# est désormais persisté par (racine, modèle). Chaque test simule un « nouveau
# processus » par une NOUVELLE instance d'EmbeddingIndex sur le même cache_dir.


class _FauxMoteur:
    """Moteur d'embeddings simulé qui COMPTE ce qu'on lui fait encoder.

    Le compteur porte sur les textes de FICHIERS (préfixe « # Fichier: »),
    séparés des requêtes : c'est lui qui prouve qu'un rechargement n'a rien
    ré-encodé.
    """

    def __init__(self, dim: int = 3):
        self.dim = dim
        self.fichiers: list[str] = []
        self.requetes: list[str] = []
        self.modele = "st:test/modele-a"

    def embed_batch(self, texts):
        out = []
        for t in texts:
            if t.startswith("# Fichier: "):
                self.fichiers.append(t.split("\n", 1)[0][len("# Fichier: "):])
            else:
                self.requetes.append(t)
            base = [float((len(t) + i) % 5 + 1) for i in range(self.dim)]
            out.append(base)
        return out


@pytest.fixture
def moteur(monkeypatch):
    from tools import embeddings

    m = _FauxMoteur()
    monkeypatch.setattr(embeddings, "is_available", lambda: True)
    monkeypatch.setattr(embeddings, "embed_batch", m.embed_batch)
    monkeypatch.setattr(embeddings, "model_id", lambda: m.modele)
    return m


@pytest.fixture
def projet(tmp_path):
    root = tmp_path / "projet"
    root.mkdir()
    for nom in ("a.py", "b.py", "c.md"):
        (root / nom).write_text(f"contenu de {nom}\n", encoding="utf-8")
    return root


def _nouveau_processus(projet, tmp_path):
    return EmbeddingIndex(projet, cache_dir=tmp_path / "cache")


class TestPersistance:
    def test_rechargement_sans_reencodage(self, moteur, projet, tmp_path):
        premier = _nouveau_processus(projet, tmp_path)
        hits_avant = premier.search("requête", k=3)
        assert sorted(moteur.fichiers) == ["a.py", "b.py", "c.md"]
        assert premier.cache_path(moteur.modele).is_file()

        moteur.fichiers.clear()
        second = _nouveau_processus(projet, tmp_path)
        hits_apres = second.search("requête", k=3)

        assert moteur.fichiers == []  # RIEN ré-encodé : c'est tout l'enjeu
        assert moteur.requetes == ["requête", "requête"]
        assert [(h.rel_path, h.preview) for h in hits_apres] == [
            (h.rel_path, h.preview) for h in hits_avant
        ]
        for h_av, h_ap in zip(hits_avant, hits_apres, strict=True):
            assert h_ap.score == pytest.approx(h_av.score, abs=1e-6)
        assert second._index["a.py"].vec == premier._index["a.py"].vec

    def test_rien_n_est_reecrit_quand_rien_n_a_change(self, moteur, projet, tmp_path):
        _nouveau_processus(projet, tmp_path).refresh()
        chemin = _nouveau_processus(projet, tmp_path).cache_path(moteur.modele)
        avant = chemin.stat().st_mtime_ns
        assert _nouveau_processus(projet, tmp_path).refresh() == 0
        assert chemin.stat().st_mtime_ns == avant

    def test_invalidation_par_mtime(self, moteur, projet, tmp_path):
        _nouveau_processus(projet, tmp_path).refresh()
        moteur.fichiers.clear()

        f = projet / "b.py"
        f.write_text("contenu modifié\n", encoding="utf-8")
        st = f.stat()
        os.utime(f, (st.st_atime, st.st_mtime + 10))

        idx = _nouveau_processus(projet, tmp_path)
        assert idx.refresh() == 1
        assert moteur.fichiers == ["b.py"]  # seul le fichier modifié
        assert "contenu modifié" in idx._index["b.py"].preview

        # Et la mise à jour est elle-même persistée.
        moteur.fichiers.clear()
        assert _nouveau_processus(projet, tmp_path).refresh() == 0
        assert moteur.fichiers == []

    def test_mtime_qui_recule_reencode_aussi(self, moteur, projet, tmp_path):
        # Un `git checkout` ou une restauration peut RECULER un mtime : le cache
        # survit au processus, `>=` aurait servi l'ancien vecteur à jamais.
        _nouveau_processus(projet, tmp_path).refresh()
        moteur.fichiers.clear()
        f = projet / "a.py"
        st = f.stat()
        os.utime(f, (st.st_atime, st.st_mtime - 3600))
        assert _nouveau_processus(projet, tmp_path).refresh() == 1
        assert moteur.fichiers == ["a.py"]

    def test_fichier_supprime_purge_et_persiste(self, moteur, projet, tmp_path):
        _nouveau_processus(projet, tmp_path).refresh()
        (projet / "c.md").unlink()

        idx = _nouveau_processus(projet, tmp_path)
        assert idx.refresh() == 1
        assert "c.md" not in idx._index

        relu = _nouveau_processus(projet, tmp_path)
        relu._synchroniser_modele()  # charge le cache sans rafraîchir
        assert sorted(relu._index) == ["a.py", "b.py"]

    @pytest.mark.parametrize(
        "contenu",
        [
            b"",
            b"\x00\xff pas du json",
            b'{"format": 1, "root": "tronqu',
            b"[]",
            b'{"format": 999}',
        ],
        ids=["vide", "binaire", "tronque", "pas-un-objet", "autre-format"],
    )
    def test_cache_corrompu_reconstruit_sans_planter(
        self, moteur, projet, tmp_path, contenu
    ):
        idx = _nouveau_processus(projet, tmp_path)
        chemin = idx.cache_path(moteur.modele)
        chemin.parent.mkdir(parents=True)
        chemin.write_bytes(contenu)

        assert len(idx.search("requête", k=3)) == 3
        assert sorted(moteur.fichiers) == ["a.py", "b.py", "c.md"]  # complet
        # Le cache corrompu a été remplacé par un cache valide.
        moteur.fichiers.clear()
        assert _nouveau_processus(projet, tmp_path).refresh() == 0
        assert moteur.fichiers == []

    def test_vecteur_de_mauvaise_dimension_rejette_tout_le_cache(
        self, moteur, projet, tmp_path
    ):
        import json

        idx = _nouveau_processus(projet, tmp_path)
        idx.refresh()
        chemin = idx.cache_path(moteur.modele)
        doc = json.loads(chemin.read_text(encoding="utf-8"))
        # Vecteur VALIDE mais de 2 dimensions au lieu de 3 : décodable, donc seul
        # le contrôle de dimension peut le refuser (un base64 tronqué échouerait
        # plus tôt, au décodage, et ne prouverait rien).
        import base64
        from array import array

        doc["files"]["a.py"]["vec"] = base64.b64encode(
            array("f", [1.0, 2.0]).tobytes()).decode("ascii")
        chemin.write_text(json.dumps(doc), encoding="utf-8")

        moteur.fichiers.clear()
        assert _nouveau_processus(projet, tmp_path).refresh() == 3
        assert sorted(moteur.fichiers) == ["a.py", "b.py", "c.md"]

    def test_cache_d_une_autre_racine_rejete(self, moteur, projet, tmp_path):
        # Collision de nom (ou fichier copié) : le contenu dit sa racine, et
        # le chargement la vérifie.
        import json

        idx = _nouveau_processus(projet, tmp_path)
        idx.refresh()
        chemin = idx.cache_path(moteur.modele)
        doc = json.loads(chemin.read_text(encoding="utf-8"))
        doc["root"] = "/ailleurs"
        chemin.write_text(json.dumps(doc), encoding="utf-8")

        moteur.fichiers.clear()
        assert _nouveau_processus(projet, tmp_path).refresh() == 3

    def test_changement_de_modele_reconstruit(self, moteur, projet, tmp_path):
        _nouveau_processus(projet, tmp_path).refresh()
        moteur.fichiers.clear()

        moteur.modele = "st:test/modele-b"
        idx = _nouveau_processus(projet, tmp_path)
        assert idx.refresh() == 3
        assert sorted(moteur.fichiers) == ["a.py", "b.py", "c.md"]
        # Deux fichiers distincts : revenir au modèle A ne ré-encode rien.
        assert idx.cache_path("st:test/modele-a") != idx.cache_path("st:test/modele-b")
        moteur.fichiers.clear()
        moteur.modele = "st:test/modele-a"
        assert _nouveau_processus(projet, tmp_path).refresh() == 0
        assert moteur.fichiers == []

    def test_cache_d_un_autre_modele_rejete_meme_sous_le_bon_nom(
        self, moteur, projet, tmp_path
    ):
        # Le nom du fichier hache le modèle, mais le CONTENU le répète et le
        # chargement le vérifie : un fichier copié ou renommé à la main ne sert
        # pas les vecteurs d'un autre espace.
        idx = _nouveau_processus(projet, tmp_path)
        idx.refresh()
        a = idx.cache_path("st:test/modele-a")
        b = idx.cache_path("st:test/modele-b")
        b.write_bytes(a.read_bytes())

        moteur.fichiers.clear()
        moteur.modele = "st:test/modele-b"
        assert _nouveau_processus(projet, tmp_path).refresh() == 3
        assert sorted(moteur.fichiers) == ["a.py", "b.py", "c.md"]

    def test_changement_de_modele_en_cours_de_processus(self, moteur, projet, tmp_path):
        idx = _nouveau_processus(projet, tmp_path)
        idx.refresh()
        moteur.fichiers.clear()
        moteur.modele = "st:test/modele-b"
        assert idx.refresh() == 3  # vecteurs de A jetés, pas mélangés à B
        assert sorted(moteur.fichiers) == ["a.py", "b.py", "c.md"]

    def test_changement_de_dimension_reconstruit(self, moteur, projet, tmp_path):
        # Même identifiant, autre espace (modèle remplacé sous le même nom) :
        # sans garde, `_cosine` rendrait 0.0 partout — k fichiers au hasard
        # présentés comme pertinents.
        _nouveau_processus(projet, tmp_path).refresh()
        moteur.fichiers.clear()
        moteur.dim = 5

        idx = _nouveau_processus(projet, tmp_path)
        hits = idx.search("requête", k=3)
        assert sorted(moteur.fichiers) == ["a.py", "b.py", "c.md"]
        assert {len(fe.vec) for fe in idx._index.values()} == {5}
        assert all(h.score > 0 for h in hits)

    def test_modele_inconnu_ne_persiste_pas(self, moteur, projet, tmp_path):
        # Un cache écrit sous une clé inconnue ne pourrait jamais être invalidé.
        moteur.modele = ""
        idx = _nouveau_processus(projet, tmp_path)
        assert idx.search("requête", k=3)
        assert not (tmp_path / "cache").exists()

    def test_ecriture_atomique_garde_l_ancien_cache(
        self, moteur, projet, tmp_path, monkeypatch
    ):
        from tools import code_search as cs

        idx = _nouveau_processus(projet, tmp_path)
        idx.refresh()
        chemin = idx.cache_path(moteur.modele)
        avant = chemin.read_bytes()

        def dump_qui_meurt(doc, f, **kw):
            f.write('{"format": 1, "files": {')  # écriture à moitié faite
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(cs.json, "dump", dump_qui_meurt)
        (projet / "d.py").write_text("nouveau\n", encoding="utf-8")
        assert idx.refresh() == 1  # ne lève pas

        assert chemin.read_bytes() == avant  # l'ancien cache est intact
        assert list(chemin.parent.glob("*.tmp")) == []  # pas d'orphelin

    def test_dossier_par_defaut_suit_memory_dir(self, projet, monkeypatch, tmp_path):
        # Résolu paresseusement : la suite redirige KLODY_DATA_DIR, un chemin
        # figé à l'import écrirait dans le vrai ~/.klody/data.
        import config

        monkeypatch.setattr(config, "MEMORY_DIR", tmp_path / "data")
        idx = EmbeddingIndex(projet)
        assert idx.cache_path("st:x").parent == tmp_path / "data" / "index_code"


class TestModelId:
    def _settings(self, monkeypatch, settings=None, erreur=None):
        import sys
        import types

        mod = types.ModuleType("klody_memory.runtime")

        def get_settings():
            if erreur:
                raise erreur
            return settings

        mod.get_settings = get_settings
        monkeypatch.setitem(sys.modules, "klody_memory.runtime", mod)
        if "klody_memory" not in sys.modules:
            monkeypatch.setitem(sys.modules, "klody_memory", types.ModuleType("klody_memory"))

    def test_provider_st(self, monkeypatch):
        from types import SimpleNamespace

        from tools import embeddings

        self._settings(monkeypatch, SimpleNamespace(
            embed_provider="ST", embed_st_model="BAAI/bge-m3", embed_model="bge-m3"))
        assert embeddings.model_id() == "st:BAAI/bge-m3"

    def test_provider_ollama(self, monkeypatch):
        from types import SimpleNamespace

        from tools import embeddings

        self._settings(monkeypatch, SimpleNamespace(
            embed_provider="ollama", embed_st_model="BAAI/bge-m3", embed_model="bge-m3"))
        assert embeddings.model_id() == "ollama:bge-m3"

    def test_non_configure_rend_vide(self, monkeypatch):
        from tools import embeddings

        self._settings(monkeypatch, erreur=RuntimeError("non configuré"))
        assert embeddings.model_id() == ""
