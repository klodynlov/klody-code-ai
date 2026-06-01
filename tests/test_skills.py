"""Tests pour tools/skills.py — save_skill, load_skills, format_skills_for_prompt."""

import json
from pathlib import Path

import pytest

# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_skills_dir(tmp_path, monkeypatch):
    """Redirige SKILLS_DIR vers un répertoire temporaire pour chaque test."""
    import tools.skills as skills_mod
    monkeypatch.setattr(skills_mod, "SKILLS_DIR", tmp_path)
    return tmp_path


# ── save_skill ─────────────────────────────────────────────────────────────────

class TestSaveSkill:
    def test_cree_fichier_json(self, tmp_path):
        from tools.skills import save_skill
        save_skill("Mon Pattern", "desc", "contenu")
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1

    def test_slug_normalisé(self, tmp_path):
        from tools.skills import save_skill
        save_skill("Mon Pattern Complexe", "desc", "contenu")
        assert (tmp_path / "mon_pattern_complexe.json").exists()

    def test_slug_max_40_chars(self, tmp_path):
        from tools.skills import save_skill
        long_name = "A" * 60
        save_skill(long_name, "desc", "contenu")
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        assert len(files[0].stem) <= 40

    def test_contenu_json_valide(self, tmp_path):
        from tools.skills import save_skill
        save_skill("Test", "ma description", "mon contenu")
        f = list(tmp_path.glob("*.json"))[0]
        data = json.loads(f.read_text())
        assert data["name"] == "Test"
        assert data["description"] == "ma description"
        assert data["content"] == "mon contenu"
        assert "slug" in data
        assert "updated" in data

    def test_mise_a_jour_si_existe(self, tmp_path):
        from tools.skills import save_skill
        save_skill("Pattern", "v1", "contenu v1")
        msg = save_skill("Pattern", "v2", "contenu v2")
        assert "mise à jour" in msg
        f = tmp_path / "pattern.json"
        data = json.loads(f.read_text())
        assert data["description"] == "v2"

    def test_retourne_message_creation(self, tmp_path):
        from tools.skills import save_skill
        msg = save_skill("Nouveau", "desc", "contenu")
        assert "Nouveau" in msg
        assert "sauvegardée" in msg

    def test_retourne_message_maj(self, tmp_path):
        from tools.skills import save_skill
        save_skill("Existant", "desc", "v1")
        msg = save_skill("Existant", "desc", "v2")
        assert "mise à jour" in msg

    def test_slash_dans_nom(self, tmp_path):
        from tools.skills import save_skill
        save_skill("Python/FastAPI", "desc", "contenu")
        # le / doit être remplacé par _
        assert (tmp_path / "python_fastapi.json").exists()


# ── load_skills ────────────────────────────────────────────────────────────────

class TestLoadSkills:
    def test_retourne_liste_vide_si_aucun_fichier(self):
        from tools.skills import load_skills
        result = load_skills()
        assert isinstance(result, list)
        assert result == []

    def test_charge_skills_dict(self, tmp_path):
        from tools.skills import load_skills, save_skill
        save_skill("A", "desc A", "contenu A")
        save_skill("B", "desc B", "contenu B")
        result = load_skills()
        assert len(result) == 2

    def test_ignore_fichiers_domaine_tableau(self, tmp_path):
        """Les fichiers domaine (listes) ne doivent pas être chargés."""
        from tools.skills import load_skills
        domain = [{"title": "t", "content": "c", "tags": []}]
        (tmp_path / "python.json").write_text(json.dumps(domain))
        result = load_skills()
        assert result == []

    def test_ignore_json_invalide(self, tmp_path):
        from tools.skills import load_skills
        (tmp_path / "broken.json").write_text("{ invalide json")
        result = load_skills()
        assert result == []

    def test_champs_requis_presents(self, tmp_path):
        from tools.skills import load_skills, save_skill
        save_skill("Test", "desc", "contenu")
        skills = load_skills()
        assert len(skills) == 1
        s = skills[0]
        for field in ("name", "slug", "description", "content", "updated"):
            assert field in s

    def test_ordre_alphabetique(self, tmp_path):
        from tools.skills import load_skills, save_skill
        save_skill("Zebra", "d", "c")
        save_skill("Alpha", "d", "c")
        save_skill("Milieu", "d", "c")
        slugs = [s["slug"] for s in load_skills()]
        assert slugs == sorted(slugs)

    def test_melange_dict_et_liste(self, tmp_path):
        """Doit charger seulement les dicts, ignorer les listes."""
        from tools.skills import load_skills, save_skill
        save_skill("User skill", "d", "c")
        (tmp_path / "python.json").write_text(json.dumps([{"title": "t", "content": "c", "tags": []}]))
        result = load_skills()
        assert len(result) == 1
        assert result[0]["name"] == "User skill"


# ── format_skills_for_prompt ───────────────────────────────────────────────────

class TestFormatSkillsForPrompt:
    def test_retourne_chaine_vide_si_pas_de_skills(self):
        from tools.skills import format_skills_for_prompt
        result = format_skills_for_prompt([])
        assert result == ""

    def test_contient_en_tete(self):
        from tools.skills import format_skills_for_prompt
        skills = [{"name": "Mon Pattern", "description": "desc", "content": "contenu"}]
        result = format_skills_for_prompt(skills)
        assert "Compétences acquises" in result

    def test_contient_nom_skill(self):
        from tools.skills import format_skills_for_prompt
        skills = [{"name": "TypeScript strict", "description": "desc", "content": "toujours --strict"}]
        result = format_skills_for_prompt(skills)
        assert "TypeScript strict" in result

    def test_contient_description_et_contenu(self):
        from tools.skills import format_skills_for_prompt
        skills = [{"name": "N", "description": "Ma description", "content": "Mon contenu"}]
        result = format_skills_for_prompt(skills)
        assert "Ma description" in result
        assert "Mon contenu" in result

    def test_plusieurs_skills(self):
        from tools.skills import format_skills_for_prompt
        skills = [
            {"name": "A", "description": "dA", "content": "cA"},
            {"name": "B", "description": "dB", "content": "cB"},
        ]
        result = format_skills_for_prompt(skills)
        assert "### A" in result
        assert "### B" in result
