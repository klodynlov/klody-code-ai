"""Le prompt système ne doit pas changer d'un message à l'autre sans raison.

Le cache de préfixe de mlx_lm ne réutilise qu'un préfixe EXACT. Mesuré le
2026-09-27 sur le gateway : le dernier token du système changé ⇒ cached=0, 11,2 s ;
rejeu exact ⇒ cached=11 517/11 518, 0,55 s. Le profil injectait « Requêtes : N »
et des « (N×) », incrémentés à chaque message : 0 % des 218 paires de messages
réels consécutifs gardaient un profil identique (84 % après correctif).
"""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace

import agent.profiler as prof
import pytest
from api.streaming import journaliser_cache, tokens_en_cache


@pytest.fixture
def profileur(tmp_path, monkeypatch):
    monkeypatch.setattr(prof, "_PROFILE_FILE", tmp_path / "user_profile.json")
    return prof.UserProfiler()


def test_le_profil_ne_bouge_pas_d_un_message_au_suivant(profileur):
    for _ in range(4):
        profileur.track_request("corrige le bug python dans l'api fastapi")
    avant = profileur.get_profile_for_prompt()
    assert avant, "profil attendu non vide après 4 requêtes"
    profileur.track_request("corrige le bug python dans l'api fastapi")
    assert profileur.get_profile_for_prompt() == avant


def test_aucun_compteur_dans_le_profil(profileur):
    for _ in range(5):
        profileur.track_request("déploie le service docker python")
    texte = profileur.get_profile_for_prompt()
    assert "Requêtes" not in texte and "Sessions" not in texte
    assert not re.search(r"\(\d+×\)", texte), texte


class TestJournalCache:
    def test_lit_cached_tokens_de_mlx_lm(self):
        u = SimpleNamespace(prompt_tokens=100, prompt_tokens_details=SimpleNamespace(cached_tokens=90))
        assert tokens_en_cache(u) == 90

    @pytest.mark.parametrize("usage", [None, SimpleNamespace(prompt_tokens=10),
                                       SimpleNamespace(prompt_tokens=10, prompt_tokens_details=None)])
    def test_absent_rend_none(self, usage):
        assert tokens_en_cache(usage) is None

    def test_une_ligne_par_appel(self, caplog):
        u = SimpleNamespace(prompt_tokens=11518, prompt_tokens_details=SimpleNamespace(cached_tokens=11517))
        with caplog.at_level(logging.INFO, logger="agent.cache_prefixe"):
            journaliser_cache(u, "brain", 0.55)
        assert "[cache] brain prompt=11518 cached=11517 (100%)" in caplog.text

    def test_sans_usage_aucune_ligne(self, caplog):
        with caplog.at_level(logging.INFO, logger="agent.cache_prefixe"):
            journaliser_cache(None, "brain", 1.0)
        assert "[cache]" not in caplog.text
