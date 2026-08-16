"""Tests pour klody_mcp/memory_server.py — expose la mémoire sémantique en MCP.

Deux exigences structurent ces tests :

1. **La frontière MCP ne lève JAMAIS.** Toute indisponibilité (moteur absent,
   mémoire désactivée) ou toute erreur du moteur doit devenir un dict lisible.
   On le vérifie en faisant lever `sm.remember`/`sm.forget` — le wrapper doit
   renvoyer {ok: False, erreur}, pas propager.

2. **Le serveur DÉLÈGUE, il ne réimplémente pas.** On monkeypatch les fonctions
   de `agent.semantic_memory` et on vérifie que les arguments passent bien
   (titre/kind/replace/top_k) — une seconde copie de la logique divergerait en
   silence, exactement le mode de défaillance dominant du dépôt.

Dans ce conteneur, `klody-memory` est absent (`sm.MEMORY_AVAILABLE is False`) :
le chemin « dégradé » est donc testé en conditions réelles, pas seulement simulé.
"""
from __future__ import annotations

import config
from agent import semantic_memory as sm
from klody_mcp import memory_server as ms

# ── _kind_or_none ─────────────────────────────────────────────────────────────


class TestKindOrNone:
    def test_vide_devient_none(self):
        assert ms._kind_or_none("") is None
        assert ms._kind_or_none("   ") is None

    def test_valeur_conservee_et_strippee(self):
        assert ms._kind_or_none("  projet ") == "projet"


# ── Chemin RÉEL de ce conteneur : moteur absent → dégradé lisible ─────────────


class TestMoteurAbsent:
    """klody-memory n'est pas installé ici : les écritures doivent refuser
    proprement en NOMMANT la cause, jamais lever."""

    def test_memoriser_refuse_en_nommant_la_cause(self, monkeypatch):
        monkeypatch.setattr(sm, "MEMORY_AVAILABLE", False)
        monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", True)
        r = ms.memoriser_impl("un fait", titre="t")
        assert r["ok"] is False
        assert "klody-memory" in r["erreur"]

    def test_oublier_refuse_en_nommant_la_cause(self, monkeypatch):
        monkeypatch.setattr(sm, "MEMORY_AVAILABLE", False)
        monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", True)
        r = ms.oublier_impl("t")
        assert r["ok"] is False
        assert "klody-memory" in r["erreur"]

    def test_etat_dit_moteur_absent_avec_raison(self, monkeypatch):
        monkeypatch.setattr(sm, "MEMORY_AVAILABLE", False)
        monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", True)
        etat = ms.etat_memoire_impl()
        assert etat["disponible"] is False
        assert etat["moteur_installe"] is False
        assert etat["active"] is True
        assert "klody-memory" in etat["raison_indispo"]

    def test_rappeler_reste_ok_avec_message_indispo(self, monkeypatch):
        """recall_for_llm ne lève pas : rappeler rend ok=True + un texte qui
        explique l'indisponibilité (ce n'est pas une erreur d'appel)."""
        monkeypatch.setattr(sm, "MEMORY_AVAILABLE", False)
        monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", True)
        r = ms.rappeler_impl("quoi que ce soit")
        assert r["ok"] is True
        assert "indisponible" in r["souvenirs"].lower()


# ── Gate SEMANTIC_MEMORY_ENABLED ──────────────────────────────────────────────


class TestDesactivee:
    def test_memoriser_refuse_si_desactivee(self, monkeypatch):
        monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", False)
        r = ms.memoriser_impl("x", titre="t")
        assert r["ok"] is False
        assert "désactivée" in r["erreur"]

    def test_oublier_refuse_si_desactivee(self, monkeypatch):
        monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", False)
        r = ms.oublier_impl("t")
        assert r["ok"] is False
        assert "désactivée" in r["erreur"]


# ── Validation d'entrée ───────────────────────────────────────────────────────


class TestValidation:
    def test_rappeler_requete_vide_refuse(self):
        r = ms.rappeler_impl("   ")
        assert r["ok"] is False
        assert "vide" in r["erreur"]

    def test_oublier_titre_vide_refuse(self, monkeypatch):
        monkeypatch.setattr(sm, "MEMORY_AVAILABLE", True)
        monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", True)
        r = ms.oublier_impl("   ")
        assert r["ok"] is False
        assert "titre" in r["erreur"]


# ── Chemin DISPONIBLE : délégation vérifiée (arguments passés) ────────────────


class TestDelegation:
    def test_memoriser_passe_les_bons_arguments(self, monkeypatch):
        captures = {}

        def faux_remember(text, *, title, kind="context", author=None,
                          replace=False, db_path=None):
            captures.update(text=text, title=title, kind=kind, replace=replace)
            return 42

        monkeypatch.setattr(sm, "MEMORY_AVAILABLE", True)
        monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", True)
        monkeypatch.setattr(sm, "remember", faux_remember)

        r = ms.memoriser_impl("un fait durable", titre="Projet X",
                              kind="projet", remplacer=True)
        assert r == {"ok": True, "id": 42, "titre": "Projet X",
                     "kind": "projet", "remplace": True}
        assert captures == {"text": "un fait durable", "title": "Projet X",
                            "kind": "projet", "replace": True}

    def test_kind_vide_retombe_sur_context(self, monkeypatch):
        captures = {}
        monkeypatch.setattr(sm, "MEMORY_AVAILABLE", True)
        monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", True)
        monkeypatch.setattr(
            sm, "remember",
            lambda text, *, title, kind="context", author=None, replace=False,
            db_path=None: captures.update(kind=kind) or 1,
        )
        ms.memoriser_impl("x", titre="t", kind="   ")
        assert captures["kind"] == "context"

    def test_rappeler_passe_top_k_et_kind(self, monkeypatch):
        captures = {}

        def faux_recall_for_llm(query, top_k=5, kind=None):
            captures.update(query=query, top_k=top_k, kind=kind)
            return "Souvenirs : ..."

        monkeypatch.setattr(sm, "recall_for_llm", faux_recall_for_llm)
        r = ms.rappeler_impl("ma requête", top_k=9, kind="profil")
        assert r["ok"] is True
        assert r["souvenirs"] == "Souvenirs : ..."
        assert r["kind"] == "profil"
        assert captures == {"query": "ma requête", "top_k": 9, "kind": "profil"}

    def test_oublier_retourne_le_compte(self, monkeypatch):
        monkeypatch.setattr(sm, "MEMORY_AVAILABLE", True)
        monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", True)
        monkeypatch.setattr(sm, "forget",
                            lambda title, *, kind=None, db_path=None: 3)
        r = ms.oublier_impl("Projet X", kind="projet")
        assert r["ok"] is True
        assert r["supprimes"] == 3


# ── Contrat « ne lève JAMAIS » même si le moteur casse ────────────────────────


class TestNeLevePas:
    def test_memoriser_capture_une_exception_moteur(self, monkeypatch):
        monkeypatch.setattr(sm, "MEMORY_AVAILABLE", True)
        monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", True)

        def boom(*a, **k):
            raise RuntimeError("sqlite-vec absent")

        monkeypatch.setattr(sm, "remember", boom)
        r = ms.memoriser_impl("x", titre="t")
        assert r["ok"] is False
        assert "sqlite-vec absent" in r["erreur"]

    def test_memoriser_texte_vide_message_direct(self, monkeypatch):
        """ValueError (texte/titre vide) est une erreur d'APPEL : message direct,
        sans préfixe de classe."""
        monkeypatch.setattr(sm, "MEMORY_AVAILABLE", True)
        monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", True)

        def refuse(*a, **k):
            raise ValueError("remember(): texte vide")

        monkeypatch.setattr(sm, "remember", refuse)
        r = ms.memoriser_impl("", titre="t")
        assert r == {"ok": False, "erreur": "remember(): texte vide"}

    def test_oublier_capture_une_exception_moteur(self, monkeypatch):
        monkeypatch.setattr(sm, "MEMORY_AVAILABLE", True)
        monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", True)

        def boom(*a, **k):
            raise RuntimeError("base verrouillée")

        monkeypatch.setattr(sm, "forget", boom)
        r = ms.oublier_impl("t")
        assert r["ok"] is False
        assert "base verrouillée" in r["erreur"]
