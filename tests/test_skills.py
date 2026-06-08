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


# ── select_skills (retrieval par mots-clés) ─────────────────────────────────────

# Fixtures synthétiques : découplées des vrais fichiers skills/ pour que le test
# reste vrai même si les descriptions réelles évoluent.
_ALGOS = {
    "name": "Maîtriser les algorithmes", "slug": "maitriser_les_algorithmes",
    "description": "tri fusion rapide, arbres AVL rouge-noir, BFS DFS Dijkstra, insertion", "content": "",
}
_DISTILL = {
    "name": "Distiller plusieurs livres en un skill", "slug": "distiller_plusieurs_livres",
    "description": "distiller/fusionner plusieurs livres en un skill via scripts/klody-distill.sh + outil await_distillation",
    "content": "",
}
_USER = {"name": "Profil", "slug": "utilisateur_profil", "description": "contexte permanent", "content": ""}
_ALL = [_ALGOS, _DISTILL, _USER]


class TestSelectSkills:
    def _slugs(self, query, k=5):
        from tools.skills import select_skills
        return [s["slug"] for s in select_skills(_ALL, query, k=k)]

    def test_query_vide_renvoie_seulement_always(self):
        # Sans requête, on n'injecte que le contexte permanent (utilisateur_/conventions_).
        assert self._slugs("") == ["utilisateur_profil"]

    def test_always_toujours_injecte(self):
        # Le skill profil est présent même pour une requête sans rapport.
        assert "utilisateur_profil" in self._slugs("météo à Paris demain")

    def test_match_topique_fort(self):
        slugs = self._slugs("comment équilibrer un arbre AVL rouge-noir à l'insertion ?")
        assert "maitriser_les_algorithmes" in slugs

    def test_outil_ne_declenche_pas_distiller(self):
        # RÉGRESSION : « code-moi un outil … » ne doit PLUS pêcher distiller_*
        # (« outil » est un mot générique non discriminant, désormais stopword).
        slugs = self._slugs(
            "Crée un visualiseur d'arbres : codez un outil qui montre les rotations AVL et rouge-noir"
        )
        assert "maitriser_les_algorithmes" in slugs
        assert "distiller_plusieurs_livres" not in slugs

    def test_vraie_demande_distiller_toujours_selectionnee(self):
        # Pas de sous-sélection : une vraie demande de distillation pêche bien le skill.
        assert "distiller_plusieurs_livres" in self._slugs(
            "distille ces livres et fusionne-les en un seul skill"
        )

    def test_outil_seul_score_zero(self):
        from tools.skills import _score_skill, _skill_terms
        # Un partage uniquement sur « outil » donne un score nul (mot ignoré).
        assert _score_skill(_skill_terms("fabrique-moi un outil"), _DISTILL) == 0

    def test_homonyme_fusion_terme_unique_partage_rejete(self):
        # « fusion » est partagé par algos (tri fusion) ET distiller (fusionner) :
        # un unique match sur ce terme partagé ne doit PAS pêcher distiller.
        slugs = self._slugs("visualise le tri fusion")
        assert "maitriser_les_algorithmes" in slugs   # match multi-termes (tri, fusion)
        assert "distiller_plusieurs_livres" not in slugs

    def test_terme_unique_single_match_qualifie(self):
        # Un unique terme mais UNIQUE au skill (df==1) reste un signal fort.
        slugs = self._slugs("explique-moi dijkstra")
        assert "maitriser_les_algorithmes" in slugs


# ── list_skills ────────────────────────────────────────────────────────────────

class TestListSkills:
    def test_aucune_competence(self):
        from tools.skills import list_skills
        assert "Aucune" in list_skills()

    def test_resume_contient_noms_et_compte(self, tmp_path):
        from tools.skills import list_skills, save_skill
        save_skill("Alpha Skill", "desc A", "c")
        save_skill("Beta Skill", "desc B", "c")
        out = list_skills()
        assert "Alpha Skill" in out and "Beta Skill" in out
        assert "2 compétence" in out


# ── delete_skill ───────────────────────────────────────────────────────────────

class TestDeleteSkill:
    def test_supprime_existant(self, tmp_path):
        from tools.skills import delete_skill, save_skill
        save_skill("Jetable", "d", "c")
        msg = delete_skill("jetable")
        assert "supprimée" in msg
        assert not (tmp_path / "jetable.json").exists()

    def test_introuvable_donne_indice(self, tmp_path):
        from tools.skills import delete_skill, save_skill
        save_skill("Existe", "d", "c")
        msg = delete_skill("nexistepas")
        assert "introuvable" in msg
        assert "existe" in msg  # indice listant les slugs disponibles

    def test_introuvable_sans_skills(self):
        from tools.skills import delete_skill
        assert "introuvable" in delete_skill("rien")

    def test_refuse_fichier_domaine(self, tmp_path):
        from tools.skills import delete_skill
        (tmp_path / "python.json").write_text(json.dumps([{"title": "t", "content": "c", "tags": []}]))
        msg = delete_skill("python")
        assert "ERREUR" in msg
        assert (tmp_path / "python.json").exists()  # non supprimé (lecture seule)

    def test_json_casse_supprime_quand_meme(self, tmp_path):
        from tools.skills import delete_skill
        (tmp_path / "casse.json").write_text("{ pas du json")
        msg = delete_skill("casse")
        assert "supprimée" in msg
        assert not (tmp_path / "casse.json").exists()


# ── _is_user_skill ─────────────────────────────────────────────────────────────

class TestIsUserSkill:
    def test_dict_est_user_skill(self, tmp_path):
        from tools.skills import _is_user_skill
        p = tmp_path / "s.json"; p.write_text(json.dumps({"name": "x"}))
        assert _is_user_skill(p) is True

    def test_liste_nest_pas_user_skill(self, tmp_path):
        from tools.skills import _is_user_skill
        p = tmp_path / "d.json"; p.write_text(json.dumps([{"title": "t"}]))
        assert _is_user_skill(p) is False

    def test_json_casse_nest_pas_user_skill(self, tmp_path):
        from tools.skills import _is_user_skill
        p = tmp_path / "b.json"; p.write_text("{ cassé")
        assert _is_user_skill(p) is False


# ── code_compatible : skills sur tâches de code ─────────────────────────────────

class TestCodeCompatible:
    def test_flag_absent_est_false(self):
        from tools.skills import _skill_is_code_compatible
        assert _skill_is_code_compatible({"name": "x"}) is False

    def test_flag_true(self):
        from tools.skills import _skill_is_code_compatible
        assert _skill_is_code_compatible({"name": "x", "code_compatible": True}) is True

    def test_flag_non_booleen_truthy_rejete(self):
        # On exige is True, pas juste truthy (évite un "true" string accidentel).
        from tools.skills import _skill_is_code_compatible
        assert _skill_is_code_compatible({"code_compatible": "yes"}) is False
        assert _skill_is_code_compatible({"code_compatible": 1}) is False

    def test_save_skill_par_defaut_n_ecrit_pas_le_flag(self, tmp_path):
        from tools.skills import save_skill
        save_skill("Sans flag", "desc", "contenu")
        data = json.loads(next(iter(tmp_path.glob("*.json"))).read_text())
        assert "code_compatible" not in data

    def test_save_skill_ecrit_le_flag_si_true(self, tmp_path):
        from tools.skills import save_skill
        save_skill("Avec flag", "desc", "contenu", code_compatible=True)
        data = json.loads(next(iter(tmp_path.glob("*.json"))).read_text())
        assert data["code_compatible"] is True


# ── format_skills_compact : rendu minuscule pour le coder ───────────────────────

class TestFormatSkillsCompact:
    def test_vide_retourne_chaine_vide(self):
        from tools.skills import format_skills_compact
        assert format_skills_compact([]) == ""

    def test_n_emet_pas_de_bloc_code_fence(self):
        # Contrairement à format_skills_for_prompt : pas de ``` (déstabilise le coder).
        from tools.skills import format_skills_compact
        out = format_skills_compact([{"name": "S", "description": "d", "content": "x = 1"}])
        assert "```" not in out
        assert "### S" in out and "d" in out

    def test_tronque_le_content_au_plafond(self):
        from tools.skills import format_skills_compact
        long_content = "A" * 5000
        out = format_skills_compact([{"name": "S", "description": "d", "content": long_content}], max_chars=800)
        assert "[…]" in out
        # Bien plus court que le content brut (troncature effective).
        assert len(out) < 1000

    def test_content_court_non_tronque(self):
        from tools.skills import format_skills_compact
        out = format_skills_compact([{"name": "S", "description": "d", "content": "court"}], max_chars=800)
        assert "court" in out and "[…]" not in out
