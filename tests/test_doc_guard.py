"""Tests du garde-fou « décisions du projet jamais ouvertes ».

Mesuré le 2026-07-30 sur le palier `discovery` du banc : quand l'agent ouvre un
document du projet il tient la contrainte non écrite 8 fois sur 8, quand il ne
l'ouvre pas 0 fois sur 10 (n=18, Fisher p = 2,3e-05). Il ne s'arrête pas faute de
budget — il s'arrête sur un VERT de tests préexistants, qui ne peuvent pas couvrir
ce qu'il vient d'ajouter. Le garde attaque ce critère d'arrêt.

Deux propriétés comptent autant que le déclenchement lui-même :
  - un dépôt SANS documentation ne déclenche jamais (les 25 tâches non-`discovery`
    du banc ne peuvent pas régresser) ;
  - voir un nom de fichier (`list_files`) ne vaut pas l'avoir lu — c'est l'erreur
    exacte que corrige le garde LibraryBrain côté catalogue.
"""
from pathlib import Path

from agent.orchestrator import (
    _DOC_NUDGE_MAX,
    _DOC_SCAN_MAX,
    Orchestrator,
    _est_documentation,
)


class TestPredicatDocumentation:
    def test_markdown_racine(self):
        assert _est_documentation("README.md") is True

    def test_document_sous_docs(self):
        assert _est_documentation("docs/INCIDENTS.md") is True

    def test_dossier_de_decisions_sans_extension_connue(self):
        # Un ADR peut être un .txt ; c'est le dossier qui le qualifie.
        assert _est_documentation("adr/0007-cache-copie-profonde.txt") is True

    def test_autres_conventions_de_dossier(self):
        assert _est_documentation("doc/guide.rst") is True
        assert _est_documentation("rfcs/0001.adoc") is True

    def test_chemin_windows(self):
        assert _est_documentation(r"docs\DECISIONS.md") is True

    def test_code_python_non(self):
        assert _est_documentation("cache.py") is False

    def test_test_python_non(self):
        assert _est_documentation("tests/test_cache.py") is False

    def test_json_racine_non(self):
        assert _est_documentation("settings.json") is False


def _orch(racine: Path):
    """Orchestrator nu (pas de LLM) avec l'état de garde d'un run neuf."""
    o = Orchestrator.__new__(Orchestrator)
    o._code_ecrit = False
    o._doc_consulte = False
    o._doc_guard_fired = False
    o._doc_inventaire = None
    o._doc_ecrits = set()
    o.file_manager = type("FM", (), {"root": racine})()
    return o


def _projet_documente(tmp_path: Path) -> Path:
    """Réplique la forme de `discovery/hidden_invariant` : du code, une suite de
    tests qui ne couvre pas la contrainte, et un document qui la porte."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "cache.py").write_text("class Cache:\n    pass\n", encoding="utf-8")
    (tmp_path / "test_cache.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    (tmp_path / "docs" / "INCIDENTS.md").write_text("# Journal\n", encoding="utf-8")
    return tmp_path


class TestInventaire:
    def test_trouve_les_documents(self, tmp_path):
        o = _orch(_projet_documente(tmp_path))
        assert o._documentation_du_projet() == ["docs/INCIDENTS.md"]

    def test_depot_sans_documentation(self, tmp_path):
        (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
        assert _orch(tmp_path)._documentation_du_projet() == []

    def test_ignore_les_dossiers_techniques(self, tmp_path):
        # `.git/` et `_build/` ne sont pas la documentation du projet.
        for cache in (".git", "_build"):
            (tmp_path / cache).mkdir()
            (tmp_path / cache / "NOTES.md").write_text("bruit\n", encoding="utf-8")
        assert _orch(tmp_path)._documentation_du_projet() == []

    def test_calcule_une_seule_fois(self, tmp_path):
        o = _orch(_projet_documente(tmp_path))
        assert o._documentation_du_projet() == ["docs/INCIDENTS.md"]
        (tmp_path / "AJOUT.md").write_text("neuf\n", encoding="utf-8")
        # Le cache tient : le garde ne re-balaie pas le disque à chaque tour.
        assert o._documentation_du_projet() == ["docs/INCIDENTS.md"]

    def test_racine_illisible_ne_leve_pas(self, tmp_path):
        o = _orch(tmp_path / "inexistant")
        assert o._documentation_du_projet() == []

    def test_profondeur_bornee(self, tmp_path):
        # 3 segments passent, 4 non. Le parcours est ÉLAGUÉ à l'entrée du dossier :
        # `sorted(rglob("*"))` énumérait 88 706 entrées en 0,79 s sur ce dépôt (il
        # descendait dans .venv/ avant de filtrer), contre 0,001 s ici.
        (tmp_path / "docs" / "adr").mkdir(parents=True)
        (tmp_path / "docs" / "adr" / "0007.md").write_text("ok\n", encoding="utf-8")
        (tmp_path / "docs" / "adr" / "trop" / "loin").mkdir(parents=True)
        (tmp_path / "docs" / "adr" / "trop" / "loin" / "X.md").write_text("no\n", encoding="utf-8")
        assert _orch(tmp_path)._documentation_du_projet() == ["docs/adr/0007.md"]

    def test_plafond_du_balayage(self, tmp_path):
        # Un dépôt bavard ne doit pas faire enfler l'inventaire sans borne.
        for i in range(_DOC_SCAN_MAX + 15):
            (tmp_path / f"D{i:03d}.md").write_text("x\n", encoding="utf-8")
        assert len(_orch(tmp_path)._documentation_du_projet()) == _DOC_SCAN_MAX


class TestSuiviDesAppels:
    def test_write_file_arme(self, tmp_path):
        o = _orch(tmp_path)
        o._note_doc_probe("write_file", {"path": "cache.py"}, "écrit")
        assert o._code_ecrit is True
        assert o._doc_consulte is False

    def test_lecture_de_doc_desarme(self, tmp_path):
        o = _orch(tmp_path)
        o._note_doc_probe("read_file", {"path": "docs/INCIDENTS.md"}, "# Journal")
        assert o._doc_consulte is True

    def test_lecture_de_code_ne_desarme_pas(self, tmp_path):
        o = _orch(tmp_path)
        o._note_doc_probe("read_file", {"path": "cache.py"}, "class Cache: ...")
        assert o._doc_consulte is False

    def test_grep_qui_ramene_un_doc_desarme(self, tmp_path):
        # Découverte par recherche : l'agent a bien VU passer la décision.
        o = _orch(tmp_path)
        o._note_doc_probe(
            "search_in_files", {"pattern": "copie"},
            "docs/INCIDENTS.md:42: il en stocke une copie profonde",
        )
        assert o._doc_consulte is True

    def test_grep_sans_doc_ne_desarme_pas(self, tmp_path):
        o = _orch(tmp_path)
        o._note_doc_probe(
            "search_in_files", {"pattern": "set"}, "cache.py:7: def set(self, key, value):")
        assert o._doc_consulte is False

    def test_list_files_ne_desarme_pas(self, tmp_path):
        # Voir le NOM du fichier n'est pas lire ce qu'il contient — c'est l'erreur
        # que le garde LibraryBrain corrige côté catalogue.
        o = _orch(tmp_path)
        o._note_doc_probe("list_files", {"path": "."}, "docs/INCIDENTS.md\ncache.py")
        assert o._doc_consulte is False

    def test_lecture_sans_chemin_ne_leve_pas(self, tmp_path):
        o = _orch(tmp_path)
        o._note_doc_probe("read_file", {}, "")
        assert o._doc_consulte is False


class TestDeclenchement:
    _CONCLUSION = "C'est fait : `set_many` est ajoutée et les 3 tests passent."

    def test_scenario_mesure(self, tmp_path):
        # Écrit le code, lance les tests, conclut sur leur vert. Jamais ouvert docs/.
        o = _orch(_projet_documente(tmp_path))
        o._note_doc_probe("read_file", {"path": "cache.py"}, "class Cache: ...")
        o._note_doc_probe("write_file", {"path": "cache.py"}, "écrit")
        assert o._should_force_doc_read(self._CONCLUSION, 4, 12) is True

    def test_pas_de_relance_si_doc_deja_lue(self, tmp_path):
        o = _orch(_projet_documente(tmp_path))
        o._note_doc_probe("write_file", {"path": "cache.py"}, "écrit")
        o._note_doc_probe("read_file", {"path": "docs/INCIDENTS.md"}, "# Journal")
        assert o._should_force_doc_read(self._CONCLUSION, 4, 12) is False

    def test_pas_de_relance_sans_ecriture(self, tmp_path):
        # Une question sur le code ne modifie rien : rien à confronter aux décisions.
        o = _orch(_projet_documente(tmp_path))
        o._note_doc_probe("read_file", {"path": "cache.py"}, "class Cache: ...")
        assert o._should_force_doc_read("Cache range ses clés dans _data.", 4, 12) is False

    def test_pas_de_relance_sans_documentation(self, tmp_path):
        # La propriété qui protège les 25 tâches non-`discovery` du banc.
        (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
        o = _orch(tmp_path)
        o._note_doc_probe("write_file", {"path": "main.py"}, "écrit")
        assert o._should_force_doc_read(self._CONCLUSION, 4, 12) is False

    def test_une_seule_relance_par_run(self, tmp_path):
        o = _orch(_projet_documente(tmp_path))
        o._note_doc_probe("write_file", {"path": "cache.py"}, "écrit")
        assert o._should_force_doc_read(self._CONCLUSION, 4, 12) is True
        o._doc_guard_fired = True
        assert o._should_force_doc_read(self._CONCLUSION, 5, 12) is False

    def test_pas_de_relance_sans_marge_d_iteration(self, tmp_path):
        # Relancer au dernier tour = mourir sur le cap sans réponse.
        o = _orch(_projet_documente(tmp_path))
        o._note_doc_probe("write_file", {"path": "cache.py"}, "écrit")
        assert o._should_force_doc_read(self._CONCLUSION, 11, 12) is False

    def test_le_contenu_ne_desarme_pas(self, tmp_path):
        # Contraste assumé avec le garde LibraryBrain : là-bas la faute est dans ce
        # que le modèle AFFIRME, ici dans ce qu'il n'a PAS fait. Aucune formulation
        # ne rend la conclusion soutenable.
        o = _orch(_projet_documente(tmp_path))
        o._note_doc_probe("write_file", {"path": "cache.py"}, "écrit")
        prudent = "J'ai ajouté `set_many`. Je n'ai pas vérifié les conventions du projet."
        assert o._should_force_doc_read(prudent, 4, 12) is True


class TestDocumentEcritParLAgent:
    """« Crée un README.md » ne doit pas déclencher le garde sur ce README.

    Faux positif RÉEL : les scénarios de rejeu 04 et 12 sont passés au rouge
    là-dessus. L'agent écrivait le seul document du dossier, puis le garde lui
    demandait de le lire — un tour brûlé, et une consigne absurde.
    """

    def test_seul_document_est_celui_ecrit(self, tmp_path):
        o = _orch(tmp_path)
        o._note_doc_probe("write_file", {"path": "README.md"}, "créé")
        (tmp_path / "README.md").write_text("# Projet\n", encoding="utf-8")
        assert o._documentation_du_projet() == []
        assert o._should_force_doc_read("C'est créé.", 2, 6) is False

    def test_chemin_absolu_normalise(self, tmp_path):
        # L'outil rend tantôt du relatif, tantôt de l'absolu ; l'exclusion doit
        # mordre sur les deux, sinon elle ne protège qu'une forme sur deux.
        o = _orch(tmp_path)
        o._note_doc_probe("write_file", {"path": str(tmp_path / "README.md")}, "créé")
        (tmp_path / "README.md").write_text("# Projet\n", encoding="utf-8")
        assert o._documentation_du_projet() == []

    def test_les_documents_preexistants_restent_comptes(self, tmp_path):
        # La distinction qui compte : écrire un doc n'excuse pas d'ignorer les autres.
        o = _orch(_projet_documente(tmp_path))
        o._note_doc_probe("write_file", {"path": "README.md"}, "créé")
        (tmp_path / "README.md").write_text("# Projet\n", encoding="utf-8")
        assert o._documentation_du_projet() == ["docs/INCIDENTS.md"]
        assert o._should_force_doc_read("C'est fait.", 2, 6) is True

    def test_chemin_hors_racine_ignore(self, tmp_path):
        o = _orch(tmp_path)
        assert o._chemin_relatif("/etc/hosts") is None
        assert o._chemin_relatif("") is None


class TestNudge:
    def test_nomme_les_documents(self, tmp_path):
        o = _orch(_projet_documente(tmp_path))
        msg = o._doc_guard_nudge(o._documentation_du_projet())
        assert "`docs/INCIDENTS.md`" in msg
        assert "read_file" in msg

    def test_dit_pourquoi_le_vert_ne_prouve_rien(self, tmp_path):
        # Le cœur du garde : sans cette phrase, il redemande une lecture sans
        # démonter le signal qui a fait conclure.
        o = _orch(_projet_documente(tmp_path))
        msg = o._doc_guard_nudge(o._documentation_du_projet())
        assert "AVANT ta modification" in msg

    def test_liste_bornee(self, tmp_path):
        docs = [f"docs/D{i}.md" for i in range(_DOC_NUDGE_MAX + 4)]
        msg = _orch(tmp_path)._doc_guard_nudge(docs)
        assert msg.count("`docs/D") == _DOC_NUDGE_MAX
        assert "+4 autre(s)" in msg
