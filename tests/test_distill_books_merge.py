"""Tests des helpers purs de la fusion multi-livres (scripts/distill_books_merge.py).

On ne teste pas les appels au proxy (réseau/LLM) : seulement le parsing des
arguments et la normalisation du JSON fusionné avant validation schéma.
"""
from scripts.distill_books_merge import _finalize_merged, _parse_book


def test_parse_book_variants():
    assert _parse_book("Algorithms|Sedgewick|2011") == ("Algorithms", "Sedgewick", 2011)
    assert _parse_book("X|Y|-") == ("X", "Y", None)
    assert _parse_book("X|Y") == ("X", "Y", None)
    assert _parse_book("X") == ("X", "", None)
    assert _parse_book("X|Y|pas_une_annee") == ("X", "Y", None)


def test_finalize_clamps_principles_to_seven():
    data = {"principles": [f"p{i}" for i in range(12)]}
    out = _finalize_merged(data, skill_name="S", domain="computing")
    assert len(out["principles"]) == 7
    assert out["principles"][0] == "p0"  # garde l'ordre, tronque la fin


def test_finalize_drops_source_and_forces_skill_domain():
    data = {
        "source": {"book": "Un seul livre", "author": "X"},
        "skill": "mauvais nom",
        "domain": "MAUVAIS",
        "principles": ["a"],
    }
    out = _finalize_merged(data, skill_name="Maîtriser les algorithmes", domain="computing")
    assert "source" not in out  # un skill multi-livres n'a pas de source unique
    assert out["skill"] == "Maîtriser les algorithmes"
    assert out["domain"] == "computing"


def test_finalize_is_idempotent():
    data = {"principles": ["a", "b"], "skill": "S", "domain": "computing"}
    once = _finalize_merged(dict(data), skill_name="S", domain="computing")
    twice = _finalize_merged(dict(once), skill_name="S", domain="computing")
    assert once == twice
