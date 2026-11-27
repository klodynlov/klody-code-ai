"""Chantier 3 (2026-11-27) : plan de tâche et validateur de schéma.

`tools/plan_tache.py` et `tools/validateur.py` sont PURS ; on les teste
directement, puis à travers le `FileManager` réel (racines autorisées) et la
mémoire de session réelle (sauvegarde / relecture). La garde de fin de tour est
jouée dans la vraie boucle ReAct : `tests/integration/test_plan_garde_replay.py`.
"""
from __future__ import annotations

import json

import config
import pytest
from agent.memory import ConversationMemory
from tools import plan_tache as pt, validateur as v
from tools.context_selection import CORE_TOOLS, select_native
from tools.file_manager import FileManager
from tools.registry import get_tools

# ── Plan de tâche ───────────────────────────────────────────────────────────

def _defini(*etapes: str) -> list[dict[str, str]]:
    plan, texte = pt.executer([], {"action": "definir", "etapes": list(etapes)})
    assert texte.startswith("Plan défini"), texte
    return plan


class TestDefinir:
    def test_premiere_etape_en_cours_les_autres_a_faire(self):
        plan = _defini("Lire", "Écrire", "Tester")
        assert [e["statut"] for e in plan] == [pt.EN_COURS, pt.A_FAIRE, pt.A_FAIRE]

    def test_rendu(self):
        _, texte = pt.executer([], {"action": "definir", "etapes": ["Lire", "Écrire"]})
        assert "Plan (0/2 faite) :" in texte
        assert "1. ▶ Lire" in texte and "2. ☐ Écrire" in texte

    def test_une_seule_etape_refusee(self):
        plan, texte = pt.executer([], {"action": "definir", "etapes": ["Tout faire"]})
        assert plan == [] and texte.startswith("Plan inchangé")
        assert "au moins 2" in texte

    def test_trop_d_etapes_refuse(self):
        plan, texte = pt.executer([], {"action": "definir",
                                       "etapes": [f"e{i}" for i in range(pt.ETAPES_MAX + 1)]})
        assert plan == [] and "trop" in texte

    def test_douze_etapes_acceptees(self):
        assert len(_defini(*[f"e{i}" for i in range(pt.ETAPES_MAX)])) == pt.ETAPES_MAX

    def test_etapes_en_texte_multiligne(self):
        # Un modèle local envoie parfois une chaîne au lieu d'une liste.
        plan, _ = pt.executer([], {"action": "definir", "etapes": "Lire\n\nÉcrire\n"})
        assert [e["titre"] for e in plan] == ["Lire", "Écrire"]

    def test_etapes_ni_liste_ni_texte(self):
        plan, texte = pt.executer([], {"action": "definir", "etapes": 3})
        assert plan == [] and "liste" in texte

    def test_titres_vides_ignores_et_sauts_de_ligne_aplatis(self):
        plan, _ = pt.executer([], {"action": "definir",
                                   "etapes": ["  ", "Lire\nle code", {"titre": "Écrire"}]})
        assert [e["titre"] for e in plan] == ["Lire le code", "Écrire"]

    def test_titre_borne(self):
        plan = _defini("x" * 500, "y")
        assert len(plan[0]["titre"]) == pt.TITRE_MAX and plan[0]["titre"].endswith("…")

    def test_remplacer_un_plan_ouvert_le_dit(self):
        _, texte = pt.executer(_defini("A", "B"), {"action": "definir", "etapes": ["C", "D"]})
        assert "remplacé" in texte


class TestCocher:
    def test_fait_passe_la_suivante_en_cours(self):
        plan, texte = pt.executer(_defini("A", "B", "C"), {"action": "cocher", "numero": 1})
        assert [e["statut"] for e in plan] == [pt.FAIT, pt.EN_COURS, pt.A_FAIRE]
        assert "Étape 1 : ✅" in texte

    def test_derniere_etape_annonce_la_cloture(self):
        plan = _defini("A", "B")
        plan, _ = pt.executer(plan, {"action": "cocher", "numero": 1})
        plan, texte = pt.executer(plan, {"action": "cocher", "numero": 2, "statut": "fait"})
        assert pt.etapes_ouvertes(plan) == []
        assert "Toutes les étapes sont closes." in texte

    @pytest.mark.parametrize("statut", ["bloque", "abandonne", "bloqué", "abandonné"])
    def test_bloque_ou_abandonne_exige_une_note(self, statut):
        plan = _defini("A", "B")
        nouveau, texte = pt.executer(plan, {"action": "cocher", "numero": 1, "statut": statut})
        assert nouveau == plan and "pourquoi" in texte

    def test_abandonne_avec_note(self):
        plan, _ = pt.executer(_defini("A", "B"), {"action": "cocher", "numero": 1,
                                                  "statut": "abandonne", "note": "inutile"})
        assert plan[0] == {"titre": "A", "statut": pt.ABANDONNE, "note": "inutile"}
        assert plan[1]["statut"] == pt.EN_COURS

    def test_bloque_reste_ouvert(self):
        plan, _ = pt.executer(_defini("A", "B"), {"action": "cocher", "numero": 1,
                                                  "statut": "bloque", "note": "attente"})
        assert pt.etapes_ouvertes(plan) == [1, 2]

    def test_une_seule_etape_en_cours(self):
        plan, _ = pt.executer(_defini("A", "B", "C"),
                              {"action": "cocher", "numero": 3, "statut": "en_cours"})
        assert [e["statut"] for e in plan] == [pt.A_FAIRE, pt.A_FAIRE, pt.EN_COURS]

    def test_alias_de_statut(self):
        plan, _ = pt.executer(_defini("A", "B"), {"action": "cocher", "numero": "1",
                                                  "statut": "Done"})
        assert plan[0]["statut"] == pt.FAIT

    @pytest.mark.parametrize("numero", [0, 3, "x", None])
    def test_numero_invalide(self, numero):
        plan = _defini("A", "B")
        nouveau, texte = pt.executer(plan, {"action": "cocher", "numero": numero})
        assert nouveau == plan and texte.startswith("Plan inchangé")

    def test_statut_inconnu(self):
        plan = _defini("A", "B")
        nouveau, texte = pt.executer(plan, {"action": "cocher", "numero": 1, "statut": "fini?"})
        assert nouveau == plan and "statut" in texte

    def test_sans_plan(self):
        plan, texte = pt.executer([], {"action": "cocher", "numero": 1})
        assert plan == [] and "definir" in texte

    def test_le_plan_d_entree_n_est_pas_mute(self):
        plan = _defini("A", "B")
        copie = [dict(e) for e in plan]
        pt.executer(plan, {"action": "cocher", "numero": 1})
        assert plan == copie


class TestAjouterVoirEffacer:
    def test_ajouter_a_la_fin(self):
        plan, _ = pt.executer(_defini("A", "B"), {"action": "ajouter", "etape": "C"})
        assert [e["titre"] for e in plan] == ["A", "B", "C"]

    def test_ajouter_apres(self):
        plan, texte = pt.executer(_defini("A", "B"), {"action": "ajouter", "etape": "A2",
                                                      "apres": 1})
        assert [e["titre"] for e in plan] == ["A", "A2", "B"]
        assert "position 2" in texte

    def test_ajouter_en_tete(self):
        plan, _ = pt.executer(_defini("A", "B"), {"action": "ajouter", "etape": "Z", "apres": 0})
        assert plan[0]["titre"] == "Z"

    def test_ajouter_a_un_plan_clos_relance_le_suivi(self):
        plan = [{"titre": "A", "statut": pt.FAIT, "note": ""}]
        plan, _ = pt.executer(plan, {"action": "ajouter", "etape": "B"})
        assert plan[1]["statut"] == pt.EN_COURS

    def test_ajouter_refuse_au_plafond(self):
        plan = _defini(*[f"e{i}" for i in range(pt.ETAPES_MAX)])
        nouveau, texte = pt.executer(plan, {"action": "ajouter", "etape": "de trop"})
        assert nouveau == plan and "déjà" in texte

    def test_ajouter_sans_titre(self):
        _, texte = pt.executer(_defini("A", "B"), {"action": "ajouter"})
        assert texte.startswith("Plan inchangé")

    def test_voir_et_effacer(self):
        plan = _defini("A", "B")
        assert pt.executer(plan, {"action": "voir"}) == (plan, pt.rendre(plan))
        assert pt.executer(plan, {})[1] == pt.rendre(plan)  # défaut : voir
        assert pt.executer(plan, {"action": "effacer"}) == ([], "Plan effacé.")
        assert pt.rendre([]) == "Aucun plan en cours."

    def test_action_inconnue(self):
        _, texte = pt.executer([], {"action": "supprimer"})
        assert "inconnue" in texte


class TestRappelEtGarde:
    def test_normaliser_tolere_un_plan_abime(self):
        brut = [{"titre": "A", "statut": "n'importe"}, "pas un dict", {"statut": "fait"},
                {"titre": "B", "statut": "fait", "note": None}]
        assert pt.normaliser(brut) == [{"titre": "A", "statut": pt.A_FAIRE, "note": ""},
                                       {"titre": "B", "statut": pt.FAIT, "note": ""}]
        assert pt.normaliser("pas une liste") == []

    def test_section_tour_vide_si_rien_d_ouvert(self):
        assert pt.section_tour([]) == ""
        assert pt.section_tour([{"titre": "A", "statut": "fait"},
                                {"titre": "B", "statut": "abandonne", "note": "x"}]) == ""

    def test_section_tour_rappelle_le_plan_ouvert(self):
        section = pt.section_tour(_defini("Lire", "Écrire"))
        assert "## Plan en cours" in section and "1. ▶ Lire" in section
        assert "ignore ce plan" in section

    def test_relance_nomme_les_etapes_ouvertes(self):
        plan, _ = pt.executer(_defini("A", "B", "C"), {"action": "cocher", "numero": 1})
        texte = pt.relance_garde(plan)
        assert "2. B" in texte and "3. C" in texte and "1. A" not in texte
        assert "bloque ou abandonne" in texte

    def test_relance_bornee_a_cinq(self):
        texte = pt.relance_garde(_defini(*[f"e{i}" for i in range(8)]))
        assert "(+3)" in texte


# ── Mémoire de session ──────────────────────────────────────────────────────

class TestMemoire:
    @pytest.fixture(autouse=True)
    def _dossier(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_DIR", tmp_path)

    def test_plan_sauve_et_relu(self, tmp_path):
        m = ConversationMemory(session_id="plan1")
        m.plan = _defini("A", "B")
        m.save()
        relue = ConversationMemory.load_from_file(tmp_path / "memory_plan1.json")
        assert relue.plan == m.plan

    def test_session_ancienne_sans_plan(self, tmp_path):
        (tmp_path / "memory_vieux.json").write_text(json.dumps({
            "session_id": "vieux", "created_at": "2026-01-01T00:00:00", "messages": []}))
        assert ConversationMemory.load_from_file(tmp_path / "memory_vieux.json").plan == []

    def test_plan_corrompu_sur_disque(self, tmp_path):
        (tmp_path / "memory_c.json").write_text(json.dumps({
            "session_id": "c", "created_at": "x", "messages": [], "plan": "oups"}))
        assert ConversationMemory.load_from_file(tmp_path / "memory_c.json").plan == []

    def test_clear_efface_le_plan(self):
        m = ConversationMemory(session_id="plan2")
        m.plan = _defini("A", "B")
        m.clear()
        assert m.plan == []


# ── Validateur ──────────────────────────────────────────────────────────────

SCHEMA_PRODUIT = {
    "type": "object",
    "required": ["nom", "prix"],
    "properties": {
        "nom": {"type": "string", "minLength": 1},
        "prix": {"type": "number", "minimum": 0},
        "stock": {"type": "integer"},
        "bio": {"type": "boolean"},
        "date": {"type": "string", "format": "date"},
        "remise": {"type": ["number", "null"]},
    },
    "additionalProperties": False,
}


def _lire(fichiers: dict[str, str]):
    def lire(chemin: str) -> str:
        if chemin not in fichiers:
            raise FileNotFoundError(f"Fichier introuvable: {chemin}")
        return fichiers[chemin]
    return lire


def _valider(fichiers: dict[str, str], chemin: str, **kw) -> str:
    return v.valider_fichier(_lire(fichiers), chemin, **kw)


class TestJson:
    def test_syntaxe_seule(self):
        out = _valider({"a.json": '{"x": [1, 2]}'}, "a.json")
        assert out.startswith("✅ a.json : syntaxe valide (aucun schéma fourni)")

    def test_syntaxe_invalide_localisee(self):
        out = _valider({"a.json": '{\n  "x": 1,\n}'}, "a.json")
        assert out.startswith("❌ a.json : 1 erreur(s)")
        assert "ligne 3" in out

    def test_conforme_au_schema(self):
        out = _valider({"p.json": '{"nom": "accras", "prix": 4.5}'}, "p.json",
                       schema=json.dumps(SCHEMA_PRODUIT))
        assert out.startswith("✅ p.json : conforme au schéma")

    def test_erreurs_localisees_par_chemin(self):
        schema = {"type": "array", "items": SCHEMA_PRODUIT}
        donnee = [{"nom": "a", "prix": 1}, {"nom": "", "prix": -2, "couleur": "rouge"}]
        out = _valider({"p.json": json.dumps(donnee)}, "p.json", schema=schema)
        assert "❌ p.json : 3 erreur(s)" in out
        assert "- [1].nom :" in out and "- [1].prix :" in out and "- [1] :" in out

    def test_format_date_verifie(self):
        out = _valider({"p.json": '{"nom": "a", "prix": 1, "date": "27/11/2026"}'},
                       "p.json", schema=SCHEMA_PRODUIT)
        assert out.startswith("❌") and "date" in out


class TestJsonl:
    def test_erreurs_par_ligne(self):
        texte = '{"nom": "a", "prix": 1}\n\n{"nom": "b"}\n{pas du json}\n'
        out = _valider({"d.jsonl": texte}, "d.jsonl", schema=SCHEMA_PRODUIT)
        assert "3 enregistrement(s)" in out
        assert "- ligne 3, racine : 'prix' is a required property" in out
        assert "- ligne 4 : JSON invalide" in out


class TestCsv:
    def test_point_virgule_et_decimales_francaises(self):
        texte = "nom;prix;stock;bio;remise\naccras;4,50;12;oui;\nsamoussas;3;0;non;0,5\n"
        out = _valider({"p.csv": texte}, "p.csv", schema=SCHEMA_PRODUIT)
        assert out.startswith("✅ p.csv : conforme au schéma"), out
        assert "2 ligne(s), 5 colonne(s), séparateur point-virgule" in out

    def test_cellule_non_convertible_localisee(self):
        texte = "nom,prix,stock\naccras,abc,1\nsamoussas,3,2.5\n"
        out = _valider({"p.csv": texte}, "p.csv", schema=SCHEMA_PRODUIT)
        assert "- ligne 2, colonne prix : 'abc' is not of type 'number'" in out
        assert "- ligne 3, colonne stock : '2.5' is not of type 'integer'" in out

    def test_colonne_obligatoire_absente_dite_une_fois(self):
        texte = "nom,stock\na,1\nb,2\nc,3\n"
        out = _valider({"p.csv": texte}, "p.csv", schema=SCHEMA_PRODUIT)
        assert out.count("prix") == 1
        assert "en-tête : colonne(s) obligatoire(s) absente(s) : prix." in out

    def test_colonne_non_prevue(self):
        out = _valider({"p.csv": "nom,prix,couleur\na,1,rouge\n"}, "p.csv",
                       schema=SCHEMA_PRODUIT)
        assert "colonne(s) non prévue(s) par le schéma : couleur" in out
        assert out.count("couleur") == 1

    def test_ligne_de_mauvaise_largeur(self):
        out = _valider({"p.csv": "nom,prix\na,1\nb\n"}, "p.csv")
        assert "- ligne 3 : 1 cellule(s) pour 2 colonne(s)." in out

    def test_entete_vide_ou_double(self):
        out = _valider({"p.csv": "nom,,nom\n1,2,3\n"}, "p.csv")
        assert "une colonne n'a pas de nom" in out and "« nom » en double" in out

    def test_fichier_vide(self):
        assert "fichier vide" in _valider({"p.csv": ""}, "p.csv")

    def test_bom_excel(self):
        out = _valider({"p.csv": "﻿nom,prix\na,1\n"}, "p.csv", schema=SCHEMA_PRODUIT)
        assert out.startswith("✅"), out

    def test_tsv_et_delimiteur_impose(self):
        assert _valider({"p.tsv": "nom\tprix\na\t1\n"}, "p.tsv").startswith("✅")
        out = _valider({"p.txt": "nom|prix\na|1\n"}, "p.txt", format="csv", delimiteur="|")
        assert out.startswith("✅") and "séparateur barre" in out

    def test_null_accepte_une_cellule_vide(self):
        out = _valider({"p.csv": "nom,prix,remise\na,1,\n"}, "p.csv", schema=SCHEMA_PRODUIT)
        assert out.startswith("✅"), out

    def test_entier_vide_non_nullable_refuse(self):
        out = _valider({"p.csv": "nom,prix,stock\na,1,\n"}, "p.csv", schema=SCHEMA_PRODUIT)
        assert "colonne stock : '' is not of type 'integer'" in out


class TestRapportEtRefus:
    def test_affichage_borne(self):
        lignes = "\n".join(f"x{i},abc" for i in range(40))
        out = _valider({"p.csv": "nom,prix\n" + lignes}, "p.csv", schema=SCHEMA_PRODUIT)
        assert "40 erreur(s)" in out
        assert out.count("\n- ") == v.ERREURS_AFFICHEES
        assert "… et 20 autre(s)." in out
        assert out.rstrip().endswith("revalide-le avant de conclure.")

    def test_comptage_plafonne(self):
        lignes = "\n".join(f"x{i},abc" for i in range(v.ERREURS_MAX + 50))
        out = _valider({"p.csv": "nom,prix\n" + lignes}, "p.csv", schema=SCHEMA_PRODUIT)
        assert f"{v.ERREURS_MAX}+ erreur(s)" in out

    def test_format_inconnu(self):
        out = _valider({"p.xml": "<a/>"}, "p.xml")
        assert out.startswith("❌ Validation impossible") and "format" in out
        assert _valider({"p.xml": "<a/>"}, "p.xml", format="xml").startswith(
            "❌ Validation impossible")

    def test_chemin_vide(self):
        assert "`chemin`" in v.valider_fichier(_lire({}), "  ")

    def test_lecture_refusee_nommee(self):
        out = _valider({}, "absent.json")
        assert out.startswith("❌ Validation impossible : lecture de « absent.json »")

    def test_delimiteur_trop_long(self):
        assert "un seul caractère" in _valider({"p.csv": "a\n"}, "p.csv", delimiteur=";;")

    def test_schema_pas_du_json(self):
        out = _valider({"a.json": "{}"}, "a.json", schema="{type: object}")
        assert "le schéma n'est pas du JSON valide" in out

    def test_schema_pas_un_objet(self):
        assert "objet JSON" in _valider({"a.json": "{}"}, "a.json", schema="[1]")

    def test_schema_invalide_nomme(self):
        out = _valider({"a.json": "{}"}, "a.json", schema='{"type": "entier"}')
        assert "le schéma lui-même est invalide" in out

    def test_schema_trop_long(self):
        out = _valider({"a.json": "{}"}, "a.json", schema="{" + " " * v.SCHEMA_MAX + "}")
        assert "schéma trop long" in out

    def test_schema_en_fichier(self):
        fichiers = {"p.json": '{"nom": "a"}', "s.json": json.dumps(SCHEMA_PRODUIT)}
        out = _valider(fichiers, "p.json", schema_chemin="s.json")
        assert "'prix' is a required property" in out

    def test_schema_en_fichier_absent(self):
        out = _valider({"p.json": "{}"}, "p.json", schema_chemin="s.json")
        assert "lecture du schéma « s.json » échouée" in out

    def test_ref_distante_jamais_telechargee(self, monkeypatch):
        """Aucun accès réseau : un `$ref` distant est NOMMÉ irrésoluble."""
        import socket

        def interdit(*_a, **_k):
            raise AssertionError("accès réseau tenté")

        monkeypatch.setattr(socket, "create_connection", interdit)
        out = _valider({"a.json": "{}"}, "a.json",
                       schema='{"$ref": "https://exemple.invalid/schema.json"}')
        assert out.startswith("❌ Validation impossible pour « a.json »")
        assert "ne peut pas être appliqué" in out


class TestAvecFileManagerReel:
    def test_hors_racine_refuse(self, tmp_path):
        fm = FileManager(root=tmp_path / "projet")
        (tmp_path / "projet").mkdir()
        out = v.valider_fichier(fm.read_file, "/etc/hosts", format="csv")
        assert out.startswith("❌ Validation impossible : lecture") and "racines" in out

    def test_fichier_reel(self, tmp_path):
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "p.csv").write_text("nom;prix\naccras;4,5\n", encoding="utf-8")
        (tmp_path / "s.json").write_text(json.dumps(SCHEMA_PRODUIT), encoding="utf-8")
        fm = FileManager(root=tmp_path)
        out = v.valider_fichier(fm.read_file, "data/p.csv", schema_chemin="s.json")
        assert out.startswith("✅ data/p.csv : conforme au schéma"), out


# ── Registre, routage ───────────────────────────────────────────────────────

def _outil(nom: str) -> dict:
    return next(t for t in get_tools() if t["function"]["name"] == nom)["function"]


def test_schemas_declares():
    plan = _outil("plan_tache")["parameters"]
    assert plan["required"] == ["action"]
    assert set(plan["properties"]["action"]["enum"]) == {"definir", "cocher", "ajouter",
                                                         "voir", "effacer"}
    assert _outil("valider_fichier")["parameters"]["required"] == ["chemin"]


def test_plan_toujours_charge():
    assert "plan_tache" in CORE_TOOLS
    assert "plan_tache" in select_native("bonjour", get_tools())


@pytest.mark.parametrize("demande", [
    "valide ce fichier JSON contre le schéma",
    "vérifie que export.csv est conforme",
    "génère un rapport excel",
    "corrige le bug dans ce script",
])
def test_validateur_route(demande):
    assert "valider_fichier" in select_native(demande, get_tools())


def test_validateur_absent_hors_sujet():
    assert "valider_fichier" not in select_native("quel temps fait-il ?", get_tools())
