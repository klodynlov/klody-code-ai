"""Chaque test hérite d'un moteur mémoire NEUF — pas seulement d'un
`semantic_memory` remis à zéro.

Vécu le 2026-09-27 (détail dans la docstring de `_semantic_memory_isolee`,
tests/conftest.py) : un vrai tour d'orchestrateur dans
`test_max_tokens_par_type.py` branchait le moteur `klody_memory` via
`embeddings.is_available()`. La fixture remettait `semantic_memory._provider`
à None mais laissait le moteur configuré et la disponibilité en cache : tout
test suivant qui atteignait un embed réel chargeait bge-m3, là où, isolé, le
même appel levait en quelques ms. `test_retrieval_desactive_retourne_vide`
passait seul et rougissait en suite.

Deux passes IDENTIQUES : chacune vérifie en entrée l'état d'un process neuf,
puis reproduit la fuite par son chemin réel (`is_available()` →
`_ensure_ready()` → `configure_memory()`). Quel que soit l'ordre, la seconde
juge le teardown de la première — et chaque passe vérifie qu'elle a bien laissé
quelque chose à juger, sans quoi ce garde ne pourrait pas rougir.
"""
from __future__ import annotations

import pytest
from agent import semantic_memory
from tools import embeddings

if semantic_memory.MEMORY_AVAILABLE:
    import klody_memory.runtime as _runtime
else:  # CI : `klody-memory` absent, seul le cache de disponibilité est jugé.
    _runtime = None


@pytest.mark.parametrize("passe", [1, 2])
def test_chaque_test_herite_d_un_moteur_neuf(passe):
    assert embeddings._available is None, (
        "cache de disponibilité des embeddings hérité d'un test précédent"
    )
    if _runtime is not None:
        assert not _runtime.is_configured(), (
            "moteur klody_memory resté branché par un test précédent — "
            "sur la base tmp_path d'un AUTRE test"
        )

    dispo = embeddings.is_available()

    assert embeddings._available is dispo, "précondition : le cache doit être posé"
    if _runtime is not None:
        assert dispo and _runtime.is_configured(), (
            "précondition : is_available() doit brancher le moteur, sinon la "
            "passe suivante n'a rien à juger"
        )
