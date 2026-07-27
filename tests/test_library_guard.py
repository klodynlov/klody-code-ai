"""Tests du garde-fou « absence de source affirmée sans avoir cherché le CONTENU ».

Incident 27/07 (session KlodyAI 19:43) : sur une question de fond (soins du
nouveau-né), Klody a appelé 5× `library_catalog` — 'bébé nouveau-né puériculture',
'child care infant', 'parenting', 'baby care', 'enfance' — n'a JAMAIS appelé
`search_books`, puis a affirmé qu'il n'y avait pas de sources dans LibraryBrain.
Faux : le catalogue n'indexe que titre+auteur, il ne voit pas le contenu.

`_claims_no_library_source` est le détecteur pur qui arme la relance forcée.
"""
from agent.orchestrator import Orchestrator, _claims_no_library_source
from tools.mcp_client import _catalog_miss


class TestAbsenceDetectee:
    def test_phrase_de_l_incident(self):
        content = (
            "J'ai cherché dans ta bibliothèque LibraryBrain et il n'y a pas de "
            "sources sur ce sujet. Voici ce que je sais de mon entraînement."
        )
        assert _claims_no_library_source(content) is True

    def test_aucune_source(self):
        assert _claims_no_library_source("Aucune source trouvée sur le sujet.") is True

    def test_aucun_livre_pertinent(self):
        content = "Le catalogue ne remonte aucun livre pertinent sur la puériculture."
        assert _claims_no_library_source(content) is True

    def test_rien_dans_la_bibliotheque(self):
        assert _claims_no_library_source("Je n'ai rien dans la bibliothèque là-dessus.") is True

    def test_bibliotheque_ne_contient_pas(self):
        content = "Ta bibliothèque ne contient pas d'ouvrage sur ce thème."
        assert _claims_no_library_source(content) is True

    def test_pas_indexe(self):
        assert _claims_no_library_source("Ce sujet n'est pas indexé chez toi.") is True

    def test_apostrophe_typographique(self):
        # Le modèle alterne ' et ’ — la normalisation doit couvrir les deux.
        assert _claims_no_library_source("Je n’ai rien trouvé dans les livres.") is True

    def test_pas_d_information(self):
        assert _claims_no_library_source("Je n'ai pas d'information sourcée.") is True


class TestPasDAbsence:
    def test_reponse_sourcee(self):
        content = (
            "D'après *Le Guide du nourrisson* (p. 42), un nouveau-né dort 16 à 18 h "
            "par jour. [search_books → Le Guide du nourrisson]"
        )
        assert _claims_no_library_source(content) is False

    def test_annonce_de_recherche(self):
        # Il ANNONCE une recherche, il n'affirme pas une absence.
        assert _claims_no_library_source("Je vais chercher dans la bibliothèque.") is False

    def test_trouvaille_explicite(self):
        content = "J'ai trouvé 3 livres sur le sujet dans la bibliothèque."
        assert _claims_no_library_source(content) is False

    def test_contenu_vide(self):
        assert _claims_no_library_source("") is False
        assert _claims_no_library_source(None) is False

    def test_sujet_hors_bibliotheque(self):
        content = "Le fichier tools/mcp_client.py expose search_books et library_catalog."
        assert _claims_no_library_source(content) is False


def _orch():
    """Orchestrator nu (pas de LLM) avec l'état de garde-fou d'un run neuf."""
    o = Orchestrator.__new__(Orchestrator)
    o._catalog_missed = False
    o._content_searched = False
    o._library_guard_fired = False
    return o


class TestSuiviSondesLibrary:
    """Un hit EXACT ne doit pas armer le garde-fou ; tout le reste, si."""

    def test_hit_exact_non_arme(self):
        o = _orch()
        o._note_library_probe(
            "library_catalog",
            "2 livre(s) au catalogue pour « clean code » :\n• Clean Code — R. Martin",
        )
        assert o._catalog_missed is False

    def test_miss_arme(self):
        o = _orch()
        o._note_library_probe("library_catalog", _catalog_miss("bébé", 24797))
        assert o._catalog_missed is True

    def test_correspondance_partielle_arme(self):
        # Le cas RÉEL de l'incident : des livres sont listés, mais aucun ne réunit
        # les mots → rien n'est prouvé sur le contenu, le garde-fou reste armé.
        o = _orch()
        o._note_library_probe(
            "library_catalog",
            "Correspondance PARTIELLE pour « baby care » — 3 livre(s) approchant(s)",
        )
        assert o._catalog_missed is True

    def test_search_books_desarme_meme_en_erreur(self):
        o = _orch()
        o._note_library_probe("search_books", "LibraryBrain timeout après 120s")
        assert o._content_searched is True

    def test_autre_outil_neutre(self):
        o = _orch()
        o._note_library_probe("read_file", "Aucun livre nulle part")
        assert o._catalog_missed is False
        assert o._content_searched is False


class TestDeclenchementRelance:
    _ABSENCE = "Je n'ai pas de sources dans ta bibliothèque sur ce sujet."

    def test_scenario_de_l_incident(self):
        o = _orch()
        o._note_library_probe("library_catalog", _catalog_miss("puériculture", 24797))
        assert o._should_force_content_search(self._ABSENCE, 1, 6) is True

    def test_pas_de_relance_si_contenu_deja_cherche(self):
        o = _orch()
        o._note_library_probe("library_catalog", _catalog_miss("x", 10))
        o._note_library_probe("search_books", "Aucun résultat trouvé dans la bibliothèque")
        assert o._should_force_content_search(self._ABSENCE, 1, 6) is False

    def test_pas_de_relance_sans_affirmation_d_absence(self):
        o = _orch()
        o._note_library_probe("library_catalog", _catalog_miss("x", 10))
        reponse = "D'après *Child Development* (p. 12), le nourrisson dort 16 h."
        assert o._should_force_content_search(reponse, 1, 6) is False

    def test_une_seule_relance_par_run(self):
        o = _orch()
        o._note_library_probe("library_catalog", _catalog_miss("x", 10))
        assert o._should_force_content_search(self._ABSENCE, 1, 6) is True
        o._library_guard_fired = True
        assert o._should_force_content_search(self._ABSENCE, 2, 6) is False

    def test_pas_de_relance_sans_marge_d_iteration(self):
        # Relancer au dernier tour = mourir sur le cap sans réponse.
        o = _orch()
        o._note_library_probe("library_catalog", _catalog_miss("x", 10))
        assert o._should_force_content_search(self._ABSENCE, 5, 6) is False

    def test_pas_de_relance_si_catalogue_jamais_interroge(self):
        o = _orch()
        assert o._should_force_content_search(self._ABSENCE, 1, 6) is False
