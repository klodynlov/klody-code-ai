"""Régression : `insert_midi_notes` doit accepter `notes` sérialisé en CHAÎNE JSON.

Bug in-vivo session 4034ccc7 (22/07/26). Le modèle local a émis
`{"notes": "[{\"pitch\": 53, ...}]"}` — `notes` en STRING, pas en tableau.
FastMCP/Pydantic rejetait avec `list_type` (input_type=str) avant même d'entrer
dans l'outil ; l'agent a cru le serveur REAPER cassé, est tombé en fallback
note-à-note (`insert_midi_note` en rafale) → anti-scan → boucle jusqu'au timeout.

Le correctif (`BeforeValidator(_coerce_json_list)` via l'alias `JsonListOfDict`)
ré-hydrate la chaîne AVANT la validation de type. On teste ici les deux niveaux :
(1) la fonction `_coerce_json_list` seule, (2) la validation Pydantic réelle via
`TypeAdapter(JsonListOfDict)` — exactement le chemin qu'emprunte FastMCP.
"""
from __future__ import annotations

import pytest
from klody_mcp.reaper_server import JsonListOfDict, _coerce_json_list
from pydantic import TypeAdapter, ValidationError

_ADAPTER = TypeAdapter(JsonListOfDict)

# La charge utile EXACTE loggée dans la session morte (tronquée à 3 accords).
_SESSION_NOTES_STR = (
    '[{"pitch": 53, "start": 0.0, "length": 2.0, "velocity": 80}, '
    '{"pitch": 57, "start": 0.0, "length": 2.0, "velocity": 80}, '
    '{"pitch": 60, "start": 0.0, "length": 2.0, "velocity": 80}]'
)


class TestCoerceHelper:
    def test_json_string_list_is_parsed(self):
        out = _coerce_json_list('[{"pitch": 60, "start": 0.0}]')
        assert out == [{"pitch": 60, "start": 0.0}]

    def test_real_list_passes_through(self):
        src = [{"pitch": 60}, {"pitch": 64}]
        assert _coerce_json_list(src) is src  # inchangé, pas de copie inutile

    def test_single_dict_is_wrapped(self):
        assert _coerce_json_list({"pitch": 60}) == [{"pitch": 60}]

    def test_single_dict_from_json_string_is_wrapped(self):
        assert _coerce_json_list('{"pitch": 60}') == [{"pitch": 60}]

    def test_none_stays_none(self):
        assert _coerce_json_list(None) is None

    def test_empty_string_stays_string(self):
        # Chaîne vide → laissée telle quelle → Pydantic lèvera `list_type`.
        assert _coerce_json_list("   ") == "   "

    def test_unparseable_string_is_untouched(self):
        # JSON cassé → on ne masque rien, Pydantic produit l'erreur standard.
        assert _coerce_json_list("[{pitch:") == "[{pitch:"


class TestPydanticValidationPath:
    """Le vrai chemin FastMCP : validation via l'annotation de type."""

    def test_session_payload_now_validates(self):
        # Reproduit le bug : la string qui faisait planter la session.
        out = _ADAPTER.validate_python(_SESSION_NOTES_STR)
        assert isinstance(out, list) and len(out) == 3
        assert out[0]["pitch"] == 53 and out[2]["pitch"] == 60

    def test_proper_array_still_validates(self):
        out = _ADAPTER.validate_python([{"pitch": 60, "start": 0.0}])
        assert out == [{"pitch": 60, "start": 0.0}]

    def test_none_validates(self):
        assert _ADAPTER.validate_python(None) is None

    def test_garbage_string_still_rejected(self):
        # Régression inverse : une vraie saloperie ne doit PAS passer en douce.
        with pytest.raises(ValidationError):
            _ADAPTER.validate_python("pas du tout du json")
