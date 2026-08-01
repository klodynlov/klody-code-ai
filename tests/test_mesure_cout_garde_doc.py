"""L'instrument qui mesure le coût du garde « décisions jamais ouvertes ».

Deux choses comptent ici, et aucune n'est la latence :

1. Le script appelle la VRAIE méthode `Orchestrator._documentation_du_projet`,
   pas une copie. Mesurer une réimplémentation donnerait un chiffre sur du code
   qui ne tourne pas.
2. Il sait dire que la mesure ne vaut rien là où elle est prise : un clone frais
   n'a pas les branches lourdes (.venv…) dont l'élagage constitue tout le coût.
"""
from __future__ import annotations

import pytest
from agent.orchestrator import _DOC_SCAN_MAX
from scripts import mesure_cout_garde_doc as mod


def _poser_docs(racine, noms):
    for nom in noms:
        chemin = racine / nom
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text("# doc\n", encoding="utf-8")


class TestSonde:
    def test_appelle_la_vraie_methode_de_l_orchestrator(self, tmp_path):
        """Si un jour la méthode change de nom, ce test tombe — c'est voulu."""
        _poser_docs(tmp_path, ["README.md", "docs/OPS.md"])
        sonde = mod._sonde(tmp_path)
        assert sonde._documentation_du_projet.__qualname__.startswith("Orchestrator")
        assert set(sonde._documentation_du_projet()) == {"README.md", "docs/OPS.md"}

    def test_la_sonde_ne_construit_pas_un_orchestrator_complet(self, tmp_path):
        """Sinon on tirerait le client LLM et la découverte MCP — du réseau."""
        sonde = mod._sonde(tmp_path)
        assert not hasattr(sonde, "llm")
        assert not hasattr(sonde, "mcp")


class TestPlafond:
    def test_le_plafond_tronque_et_l_ecart_est_rapporte(self, tmp_path):
        _poser_docs(tmp_path, [f"doc_{i:03d}.md" for i in range(_DOC_SCAN_MAX + 5)])
        mesure = mod.mesurer(tmp_path, passes=1, avec_temoin=False)
        assert mesure["documents"] == _DOC_SCAN_MAX
        assert mesure["documents_sans_plafond"] == _DOC_SCAN_MAX + 5
        assert mesure["tronque_scan"] is True
        assert len(mesure["ecartes_par_le_plafond"]) == 5

    def test_sous_le_plafond_rien_n_est_ecarte(self, tmp_path):
        _poser_docs(tmp_path, ["README.md", "docs/A.md", "docs/B.md"])
        mesure = mod.mesurer(tmp_path, passes=1, avec_temoin=False)
        assert mesure["tronque_scan"] is False
        assert mesure["ecartes_par_le_plafond"] == []

    def test_l_inventaire_sans_plafond_suit_les_memes_regles_d_elagage(self, tmp_path):
        """Sinon l'écart affiché mélangerait deux périmètres et serait faux."""
        _poser_docs(tmp_path, [
            "README.md",
            "docs/OPS.md",
            "a/b/c/trop_profond.md",   # profondeur ≥ _DOC_SCAN_DEPTH
            ".cache/ignore.md",        # dossier masqué
            "_build/ignore.md",        # dossier préfixé _
            "notes.txt",               # mauvaise extension
        ])
        tous = mod._inventaire_sans_plafond(tmp_path)
        assert tous == ["README.md", "docs/OPS.md"]


class TestCopieDeTravail:
    def test_un_clone_frais_est_signale(self, tmp_path):
        _poser_docs(tmp_path, ["README.md"])
        assert mod.mesurer(tmp_path, passes=1, avec_temoin=False)["copie_de_travail"] is False

    @pytest.mark.parametrize("lourd", [".venv", "node_modules", "_downloads", "venv"])
    def test_une_copie_de_travail_est_reconnue(self, tmp_path, lourd):
        _poser_docs(tmp_path, ["README.md"])
        (tmp_path / lourd).mkdir()
        assert mod.mesurer(tmp_path, passes=1, avec_temoin=False)["copie_de_travail"] is True

    def test_l_avertissement_est_affiche_sur_un_clone_frais(self, tmp_path, capsys):
        _poser_docs(tmp_path, ["README.md"])
        mod._resume(mod.mesurer(tmp_path, passes=1, avec_temoin=False))
        sortie = capsys.readouterr().out
        assert "ARBRE SANS DOSSIERS LOURDS" in sortie
        # Le message est enveloppé sur plusieurs lignes : on cherche un fragment
        # qui tient sur une seule, sinon le test échoue sur la mise en forme.
        assert "N'EST PAS le dépôt qui compte" in sortie


class TestGardesDeSortie:
    def test_racine_introuvable_rend_une_absence_de_resultat(self, tmp_path):
        with pytest.raises(mod.MesureImpossible):
            mod.mesurer(tmp_path / "nexiste_pas", passes=1, avec_temoin=False)

    def test_main_rend_1_sur_racine_introuvable(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv",
            ["mesure_cout_garde_doc.py", "--racine", str(tmp_path / "nexiste_pas"),
             "--sans-temoin"],
        )
        assert mod.main() == 1
        assert "MESURE IMPOSSIBLE" in capsys.readouterr().err

    def test_main_rend_0_sur_une_racine_valide(self, tmp_path, monkeypatch):
        _poser_docs(tmp_path, ["README.md"])
        monkeypatch.setattr(
            "sys.argv",
            ["mesure_cout_garde_doc.py", "--racine", str(tmp_path), "--sans-temoin",
             "--passes", "1"],
        )
        assert mod.main() == 0


def test_le_cache_rend_le_2e_appel_gratuit(tmp_path):
    """C'est le chemin réel des tours suivants du même run — vérifié, pas supposé."""
    _poser_docs(tmp_path, ["README.md", "docs/OPS.md"])
    mesure = mod.mesurer(tmp_path, passes=1, avec_temoin=False)
    assert mesure["latence_cache_s"] < mesure["mediane_s"]


def test_le_nudge_ne_nomme_qu_une_poignee_de_documents(tmp_path):
    _poser_docs(tmp_path, [f"doc_{i:03d}.md" for i in range(20)])
    mesure = mod.mesurer(tmp_path, passes=1, avec_temoin=False)
    assert len(mesure["nommes_dans_le_nudge"]) == mod._DOC_NUDGE_MAX
    assert mesure["nommes_dans_le_nudge"] == sorted(mesure["nommes_dans_le_nudge"])
