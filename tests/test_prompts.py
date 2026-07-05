"""Tests pour agent.prompts — chargement et composition des system prompts."""
from __future__ import annotations

import pytest
from agent.prompts import (
    _TASK_PROMPT_FILES,
    PROMPTS_DIR,
    available_task_types,
    compose_system_prompt,
    load_prompt_file,
)


class TestFichiersExistent:
    def test_dossier_prompts_present(self):
        assert PROMPTS_DIR.exists() and PROMPTS_DIR.is_dir()

    def test_tous_les_fichiers_prompts_existent(self):
        """base.md, default.md + 1 fichier par task_type."""
        expected = ["base.md", "default.md", *_TASK_PROMPT_FILES.values()]
        for name in expected:
            assert (PROMPTS_DIR / name).exists(), f"Manquant: prompts/{name}"

    def test_aucun_fichier_vide(self):
        for name in ["base.md", "default.md", *_TASK_PROMPT_FILES.values()]:
            content = (PROMPTS_DIR / name).read_text(encoding="utf-8").strip()
            assert len(content) > 50, f"prompts/{name} trop court ({len(content)} chars)"


class TestComposition:
    def test_default_contient_base(self):
        """Le prompt composé sans task_type doit inclure le BASE."""
        s = compose_system_prompt(None)
        base = load_prompt_file("base.md")
        assert base[:80] in s

    def test_easy_edit_focalise(self):
        """Le prompt easy_edit doit être court et mentionner read/write."""
        s = compose_system_prompt("edit")
        assert "read_file" in s
        assert "write_file" in s
        # Doit être beaucoup plus court que default (focalisation = moins de bruit)
        default = compose_system_prompt(None)
        assert len(s) < len(default), "easy_edit doit être plus court que default"

    def test_bug_fix_mentionne_test_et_sandbox(self):
        s = compose_system_prompt("bug_fix")
        assert "test" in s.lower()
        assert "sandbox" in s.lower()

    def test_explain_interdit_write(self):
        s = compose_system_prompt("explain")
        assert "write_file" in s  # mentionné explicitement comme interdit
        # Doit clairement signaler qu'on ne modifie rien
        assert ("INTERDICTION" in s) or ("JAMAIS" in s) or ("AUCUNE modification" in s)

    def test_feature_court_et_mentionne_action(self):
        """Feature prompt simplifié : court, mentionne preview_code/write_file."""
        s = compose_system_prompt("feature")
        # Budget recalibré 2026-07-02 : l'ancien seuil 2000 datait d'un base.md
        # de 771 chars ; base a grossi de gotchas validés (sandbox #57) et du
        # routage dream-x-world (#82, dédupliqué). Composé ≈ 2280 aujourd'hui.
        # Si ce seuil casse : chercher d'abord de la redondance dans base.md
        # (partagé par tous les task_types) avant de le relever.
        assert len(s) < 2600
        # L'intention « court » = focalisation : toujours < prompt default
        assert len(s) < len(compose_system_prompt(None))
        # Et mentionner les outils d'action
        assert "preview_code" in s or "write_file" in s
        assert "tool_call" in s.lower() or "outil" in s.lower()

    def test_refactor_mentionne_comportement(self):
        s = compose_system_prompt("refactor")
        assert "comportement" in s.lower()

    def test_task_type_inconnu_utilise_default(self):
        """Un task_type bidon → fallback default (qui inclut GitHub/LibraryBrain/etc)."""
        s = compose_system_prompt("foobar_unknown_xyz")
        default = compose_system_prompt(None)
        assert s == default

    def test_task_type_none_utilise_default(self):
        s = compose_system_prompt(None)
        default = load_prompt_file("default.md")
        assert default[:80] in s


class TestCache:
    def test_load_prompt_file_cache(self):
        """Deux appels successifs doivent renvoyer le même objet (LRU cache)."""
        a = load_prompt_file("base.md")
        b = load_prompt_file("base.md")
        assert a is b

    def test_load_fichier_inexistant_renvoie_vide(self):
        assert load_prompt_file("does_not_exist.md") == ""


class TestAvailableTypes:
    def test_types_disponibles(self):
        types = available_task_types()
        assert set(types) == {
            "edit", "refactor", "bug_fix", "feature", "explain", "self_dev",
            "review", "test_gen", "security", "docs", "perf", "migrate",
        }


class TestCapacitesEtendues:
    """Prompts focalisés des task_types étendus (Roadmap v2 #10)."""

    def test_review_lecture_seule(self):
        s = compose_system_prompt("review")
        assert "revue" in s.lower()
        assert "read_file" in s

    def test_test_gen_mentionne_sandbox(self):
        s = compose_system_prompt("test_gen")
        assert "test" in s.lower()
        assert "sandbox" in s.lower() or "pytest" in s.lower()

    def test_security_mentionne_owasp(self):
        s = compose_system_prompt("security")
        assert "owasp" in s.lower()
        assert "sécurité" in s.lower() or "vulnérabilit" in s.lower()

    def test_docs_mentionne_documentation(self):
        s = compose_system_prompt("docs")
        assert "documentation" in s.lower() or "docstring" in s.lower()

    def test_perf_mentionne_mesure_et_memoire(self):
        s = compose_system_prompt("perf")
        assert "mesure" in s.lower()
        assert "mémoire" in s.lower()

    def test_migrate_mentionne_dependances(self):
        s = compose_system_prompt("migrate")
        assert "migration" in s.lower()
        assert "dépendance" in s.lower() or "analyze_dependencies" in s

    def test_prompts_etendus_plus_courts_que_default(self):
        default = compose_system_prompt(None)
        for tt in ("review", "test_gen", "security", "docs", "perf", "migrate"):
            assert len(compose_system_prompt(tt)) < len(default), tt
