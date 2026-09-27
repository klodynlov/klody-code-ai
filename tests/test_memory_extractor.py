"""Tests de agent/memory_extractor.py — extraction automatique de mémoire."""

import json
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import MagicMock, patch

import config
import pytest
from agent import memory_extractor
from agent.memory_extractor import (
    _parse_json_facts,
    extract_and_save,
    extract_mid_session,
)
from openai import APIConnectionError

# ── Fixtures ──────────────────────────────────────────────────────────────────

def _lt_mock():
    """LongTermMemory mock simple."""
    lt = MagicMock()
    lt.remember.return_value = "Mémorisé : [context] key"
    return lt


def _msgs(n_user: int = 3) -> list[dict]:
    """Génère une conversation minimale avec n messages user."""
    msgs = []
    for i in range(n_user):
        msgs.append({"role": "user", "content": f"Question {i}"})
        msgs.append({"role": "assistant", "content": f"Réponse {i}"})
    return msgs


def _llm_response(facts: list[dict]) -> MagicMock:
    """Simule une réponse LLM retournant des facts en JSON."""
    choice = MagicMock()
    choice.message.content = json.dumps(facts)
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ── _parse_json_facts ─────────────────────────────────────────────────────────

class TestParseJsonFacts:
    def test_json_valide_direct(self):
        raw = '[{"key": "k", "content": "v", "category": "context"}]'
        result = _parse_json_facts(raw)
        assert len(result) == 1
        assert result[0]["key"] == "k"

    def test_liste_vide(self):
        assert _parse_json_facts("[]") == []

    def test_json_avec_texte_autour(self):
        raw = 'Voici les faits:\n[{"key": "k", "content": "v", "category": "user"}]\nFin.'
        result = _parse_json_facts(raw)
        assert len(result) == 1
        assert result[0]["key"] == "k"

    def test_json_invalide_retourne_vide(self):
        assert _parse_json_facts("pas du json") == []

    def test_json_invalide_partiel(self):
        assert _parse_json_facts("[{broken json") == []

    def test_plusieurs_facts(self):
        facts = [
            {"key": "a", "content": "va", "category": "user"},
            {"key": "b", "content": "vb", "category": "project"},
        ]
        result = _parse_json_facts(json.dumps(facts))
        assert len(result) == 2

    def test_whitespace_autour(self):
        raw = '  \n  [{"key": "k", "content": "v", "category": "context"}]  \n  '
        result = _parse_json_facts(raw)
        assert len(result) == 1

    def test_array_tronque_recupere(self):
        """Réponse coupée sans `]` final (vu en prod 09/06) — on récupère
        les objets complets au lieu de tout jeter."""
        raw = '[{"key": "preferred_3D_software", "content": "Three.js, Blender"},'
        result = _parse_json_facts(raw)
        assert len(result) == 1
        assert result[0]["key"] == "preferred_3D_software"

    def test_array_tronque_multi_objets(self):
        raw = '[{"key": "a", "content": "va"}, {"key": "b", "content": "vb"},'
        result = _parse_json_facts(raw)
        assert len(result) == 2


# ── extract_and_save — session trop courte ────────────────────────────────────

class TestExtractTooShort:
    def test_zero_user_messages(self):
        lt = _lt_mock()
        msgs = [{"role": "assistant", "content": "Bonjour"}]
        result = extract_and_save(msgs, lt)
        assert result == []
        lt.remember.assert_not_called()

    def test_un_seul_user_message(self):
        lt = _lt_mock()
        msgs = [
            {"role": "user", "content": "Question unique"},
            {"role": "assistant", "content": "Réponse"},
        ]
        result = extract_and_save(msgs, lt)
        assert result == []
        lt.remember.assert_not_called()

    def test_messages_sans_contenu_ignores(self):
        lt = _lt_mock()
        msgs = [
            {"role": "user", "content": None},
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "ok"},
        ]
        result = extract_and_save(msgs, lt)
        assert result == []


# ── extract_and_save — LLM mocké ─────────────────────────────────────────────

class TestExtractWithMockedLLM:
    @patch("agent.memory_extractor.OpenAI")
    def test_facts_sauvegardes(self, mock_openai_cls):
        facts = [
            {"key": "langage", "content": "Python", "category": "preference"},
            {"key": "projet", "content": "Klody AI", "category": "project"},
        ]
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response(facts)

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        assert len(result) == 2
        assert lt.remember.call_count == 2

    @patch("agent.memory_extractor.OpenAI")
    def test_llm_retourne_liste_vide(self, mock_openai_cls):
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response([])

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        assert result == []
        lt.remember.assert_not_called()

    @patch("agent.memory_extractor.OpenAI")
    def test_categorie_invalide_corrigee(self, mock_openai_cls):
        facts = [{"key": "k", "content": "v", "category": "inconnu"}]
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response(facts)

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        assert result[0]["category"] == "context"

    @patch("agent.memory_extractor.OpenAI")
    def test_fact_sans_key_ignore(self, mock_openai_cls):
        facts = [{"content": "v", "category": "context"}]  # pas de key
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response(facts)

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        assert result == []

    @patch("agent.memory_extractor.OpenAI")
    def test_fact_sans_content_ignore(self, mock_openai_cls):
        facts = [{"key": "k", "category": "context"}]  # pas de content
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response(facts)

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        assert result == []

    @patch("agent.memory_extractor.OpenAI")
    def test_erreur_llm_retourne_vide(self, mock_openai_cls):
        mock_openai_cls.return_value.chat.completions.create.side_effect = Exception("timeout")

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        assert result == []
        lt.remember.assert_not_called()

    @patch("agent.memory_extractor.OpenAI")
    def test_llm_repond_json_avec_texte(self, mock_openai_cls):
        """LLM qui entoure le JSON de texte — doit quand même parser."""
        choice = MagicMock()
        choice.message.content = 'Voici:\n[{"key": "k", "content": "v", "category": "user"}]'
        resp = MagicMock()
        resp.choices = [choice]
        mock_openai_cls.return_value.chat.completions.create.return_value = resp

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        assert len(result) == 1

    @patch("agent.memory_extractor.OpenAI")
    def test_messages_tronques_a_30(self, mock_openai_cls):
        """Ne plante pas avec une très longue conversation."""
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response([])
        lt = _lt_mock()
        long_msgs = _msgs(50)  # 100 messages
        result = extract_and_save(long_msgs, lt)
        assert result == []
        # Vérifier que le LLM a bien été appelé (pas skippé)
        mock_openai_cls.return_value.chat.completions.create.assert_called_once()

    @patch("agent.memory_extractor.OpenAI")
    def test_content_liste_ne_crashe_pas(self, mock_openai_cls):
        """Le LLM renvoie content sous forme de liste (`["Three.js", "Blender"]`).
        Régression du bug prod `'list' object has no attribute 'strip'` qui
        faisait perdre TOUTE la fournée."""
        facts = [{"key": "soft_3d", "content": ["Three.js", "Blender"], "category": "preference"}]
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response(facts)

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        assert len(result) == 1
        assert result[0]["content"] == "Three.js, Blender"

    @patch("agent.memory_extractor.OpenAI")
    def test_un_fact_pourri_ne_jette_pas_les_bons(self, mock_openai_cls):
        """Un fait malformé (key=liste) est ignoré individuellement ; les
        faits valides de la même fournée sont quand même sauvegardés."""
        facts = [
            {"key": ["x", "y"], "content": "valeur", "category": "context"},  # key non-string
            "ceci n'est pas un dict",                                          # fact non-dict
            {"key": "bon", "content": "Python", "category": "preference"},     # valide
        ]
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response(facts)

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        keys = {r["key"] for r in result}
        assert "bon" in keys
        assert lt.remember.called

    @patch("agent.memory_extractor.OpenAI")
    def test_categories_valides_conservees(self, mock_openai_cls):
        facts = [
            {"key": "a", "content": "va", "category": "user"},
            {"key": "b", "content": "vb", "category": "project"},
            {"key": "c", "content": "vc", "category": "preference"},
            {"key": "d", "content": "vd", "category": "context"},
        ]
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response(facts)

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        cats = {r["category"] for r in result}
        assert cats == {"user", "project", "preference", "context"}


# ── extract_mid_session — extraction proactive mid-session ────────────────────

class TestExtractMidSession:
    """Le compteur global _last_mid_extraction_count gate par intervalle ;
    on le remet à 0 (auto-restauré par monkeypatch) avant chaque test."""

    def _reset(self, monkeypatch):
        monkeypatch.setattr("agent.memory_extractor._last_mid_extraction_count", 0)

    def test_trop_peu_de_messages(self, monkeypatch):
        self._reset(monkeypatch)
        lt = _lt_mock()
        assert extract_mid_session(_msgs(1), lt) == []
        lt.remember.assert_not_called()

    def test_intervalle_non_atteint(self, monkeypatch):
        self._reset(monkeypatch)
        lt = _lt_mock()
        # 5 messages user < intervalle (8) depuis la dernière extraction (0)
        assert extract_mid_session(_msgs(5), lt) == []
        lt.remember.assert_not_called()

    @patch("agent.memory_extractor.OpenAI")
    def test_chemin_complet_sauvegarde(self, mock_openai_cls, monkeypatch):
        self._reset(monkeypatch)
        facts = [{"key": "pref", "content": "indentation tabs", "category": "preference"}]
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response(facts)
        lt = _lt_mock()
        result = extract_mid_session(_msgs(8), lt)  # 8 user ≥ intervalle ⇒ tourne
        assert len(result) == 1
        assert lt.remember.call_count == 1

    @patch("agent.memory_extractor.OpenAI")
    def test_categorie_invalide_corrigee(self, mock_openai_cls, monkeypatch):
        self._reset(monkeypatch)
        facts = [{"key": "k", "content": "v", "category": "n_importe_quoi"}]
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response(facts)
        lt = _lt_mock()
        result = extract_mid_session(_msgs(8), lt)
        assert result[0]["category"] == "context"

    @patch("agent.memory_extractor.OpenAI")
    def test_erreur_llm_retourne_vide(self, mock_openai_cls, monkeypatch):
        self._reset(monkeypatch)
        mock_openai_cls.return_value.chat.completions.create.side_effect = Exception("timeout")
        lt = _lt_mock()
        assert extract_mid_session(_msgs(8), lt) == []
        lt.remember.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# Incident du 2026-09-27 : l'extraction visait Ollama en mode mlx
# ══════════════════════════════════════════════════════════════════════════════
#
# `_client_llm` construisait son client sur OLLAMA_BASE_URL avec MODEL_FALLBACK
# (`mistral:latest`) QUEL QUE SOIT `BACKEND`. En mlx — le nominal — Ollama
# n'écoute pas : dernière extraction réussie le 2026-07-18 02:20, puis 164
# « [Extractor] Erreur LLM : Connection error. » en WARNING, lus par personne.
#
# Les tests ci-dessous posent la configuration RÉELLE de la machine (valeurs du
# `.env` de production) et rougissent si le client repart vers :11434 ou si le
# modèle redevient un nom Ollama.

_GATEWAY = "http://localhost:8090/v1"
_OLLAMA = "http://localhost:11434/v1"


def _faux_openai(contenu: str = "[]", erreur: BaseException | None = None):
    """Classe de faux client OpenAI, FRAÎCHE par test (registres non partagés)."""

    class Faux:
        crees: ClassVar[list] = []
        appels: ClassVar[list] = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.ferme = False
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
            Faux.crees.append(self)

        def _create(self, **params):
            Faux.appels.append(params)
            if erreur is not None:
                raise erreur
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=contenu))]
            )

        def close(self):
            self.ferme = True

    return Faux


def _erreur_connexion() -> APIConnectionError:
    import httpx

    return APIConnectionError(request=httpx.Request("POST", f"{_GATEWAY}/chat/completions"))


@pytest.fixture
def extracteur_vierge(monkeypatch):
    """Cache de client, état et compteur mi-session remis à neuf (restaurés en
    sortie par monkeypatch) : ce sont des états de MODULE, qu'un test précédent
    — `side_effect=Exception("timeout")` ci-dessus — a pu faire bouger."""
    monkeypatch.setattr(memory_extractor, "_client_partage", None)
    monkeypatch.setattr(
        memory_extractor,
        "_etat",
        {"echecs_consecutifs": 0, "derniere_reussite": None,
         "premier_echec": None, "derniere_erreur": None},
    )
    monkeypatch.setattr(memory_extractor, "_last_mid_extraction_count", 0)


@pytest.fixture
def prod_mlx(monkeypatch, extracteur_vierge):
    """La configuration de la machine au 2026-09-27 (`.env` de production)."""
    monkeypatch.setattr(config, "BACKEND", "mlx")
    monkeypatch.setattr(config, "LLM_BASE_URL", _GATEWAY)
    monkeypatch.setattr(config, "LLM_API_KEY", "mlx")
    monkeypatch.setattr(config, "LLM_MODEL", "brain")
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", _OLLAMA)
    monkeypatch.setattr(config, "OLLAMA_API_KEY", "ollama")
    monkeypatch.setattr(config, "MODEL_FALLBACK", "mistral:latest")
    monkeypatch.setattr(config, "MEMORY_EXTRACTOR_MODEL", "")


@pytest.fixture
def prod_ollama(monkeypatch, extracteur_vierge):
    monkeypatch.setattr(config, "BACKEND", "ollama")
    monkeypatch.setattr(config, "LLM_BASE_URL", _OLLAMA)
    monkeypatch.setattr(config, "LLM_API_KEY", "ollama")
    monkeypatch.setattr(config, "LLM_MODEL", "qwen2.5-coder:32b")
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", _OLLAMA)
    monkeypatch.setattr(config, "MODEL_FALLBACK", "mistral:latest")
    monkeypatch.setattr(config, "MEMORY_EXTRACTOR_MODEL", "")


class TestCibleSuitLeBackend:
    def test_mlx_fin_de_session_vise_le_gateway_et_son_alias(self, prod_mlx, monkeypatch):
        Faux = _faux_openai()
        monkeypatch.setattr(memory_extractor, "OpenAI", Faux)

        memory_extractor.extract_and_save(_msgs(3), _lt_mock())

        assert Faux.crees[0].kwargs["base_url"] == _GATEWAY, (
            "en mlx l'extraction repartait vers Ollama (:11434), fermé — "
            "incident du 2026-09-27, 2,5 mois de mémoire perdue"
        )
        assert Faux.appels[0]["model"] == "brain", (
            "MODEL_FALLBACK est un nom OLLAMA : le gateway le rejette en 404"
        )

    def test_mlx_mi_session_vise_le_gateway_et_son_alias(self, prod_mlx, monkeypatch):
        Faux = _faux_openai()
        monkeypatch.setattr(memory_extractor, "OpenAI", Faux)

        memory_extractor.extract_mid_session(_msgs(8), _lt_mock())

        assert Faux.crees[0].kwargs["base_url"] == _GATEWAY
        assert Faux.appels[0]["model"] == "brain"

    def test_ollama_garde_le_modele_de_repli_historique(self, prod_ollama, monkeypatch):
        """Le mode ollama ne change pas : c'est lui qui avait choisi MODEL_FALLBACK."""
        Faux = _faux_openai()
        monkeypatch.setattr(memory_extractor, "OpenAI", Faux)

        memory_extractor.extract_and_save(_msgs(3), _lt_mock())

        assert Faux.crees[0].kwargs["base_url"] == _OLLAMA
        assert Faux.appels[0]["model"] == "mistral:latest"

    def test_le_reglage_dedie_prime(self, prod_mlx, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_EXTRACTOR_MODEL", "extracteur")
        Faux = _faux_openai()
        monkeypatch.setattr(memory_extractor, "OpenAI", Faux)

        memory_extractor.extract_and_save(_msgs(3), _lt_mock())

        assert Faux.appels[0]["model"] == "extracteur"

    def test_un_modele_explicite_prime_sur_tout(self, prod_mlx, monkeypatch):
        Faux = _faux_openai()
        monkeypatch.setattr(memory_extractor, "OpenAI", Faux)

        memory_extractor.extract_and_save(_msgs(3), _lt_mock(), model="coder")

        assert Faux.appels[0]["model"] == "coder"

    def test_un_changement_de_cible_reconstruit_et_ferme_l_ancien_client(
        self, prod_mlx, monkeypatch
    ):
        """Sans la cible dans la clé du cache, le client d'avant — pointé sur
        l'ancienne URL — resterait servi ; et le remplacer sans le fermer
        rouvrirait la fuite de pools du 2026-08-09."""
        Faux = _faux_openai()
        monkeypatch.setattr(memory_extractor, "OpenAI", Faux)
        memory_extractor.extract_and_save(_msgs(3), _lt_mock())
        memory_extractor.extract_and_save(_msgs(3), _lt_mock())
        assert len(Faux.crees) == 1, "même cible ⇒ client réutilisé (audit 2026-08-09)"

        monkeypatch.setattr(config, "LLM_BASE_URL", "http://ailleurs:8090/v1")
        memory_extractor.extract_and_save(_msgs(3), _lt_mock())

        assert len(Faux.crees) == 2
        assert Faux.crees[1].kwargs["base_url"] == "http://ailleurs:8090/v1"
        assert Faux.crees[0].ferme

    def test_config_reelle_d_un_processus_neuf(self, tmp_path):
        """Les tests ci-dessus posent `config.LLM_*` eux-mêmes : ils ne verraient
        pas une résolution fausse DANS config.py. Celui-ci part d'un processus
        neuf, environnement de la machine, et laisse config.py tout résoudre.

        Les variables explicites gagnent sur le `.env` (`load_dotenv` n'écrase
        rien) : le test ne dépend pas du `.env` du développeur."""
        racine = Path(__file__).resolve().parent.parent
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "KLODY_DATA_DIR": str(tmp_path / "data"),
            "BACKEND": "mlx",
            "MLX_BASE_URL": _GATEWAY,
            "MLX_API_KEY": "mlx",  # pragma: allowlist secret — clé fictive du SDK
            "MLX_MODEL": "brain",
            "OLLAMA_BASE_URL": _OLLAMA,
            "MODEL_FALLBACK": "mistral:latest",
            "MEMORY_EXTRACTOR_MODEL": "",
            "KLODY_JOURNAL": "0",
        }
        sortie = subprocess.run(
            [sys.executable, "-c",
             "from agent.memory_extractor import _cible; print('|'.join(_cible()))"],
            cwd=racine, env=env, capture_output=True, text=True, timeout=60,
        )
        assert sortie.returncode == 0, sortie.stderr[-800:]
        base_url, _cle, modele = sortie.stdout.strip().splitlines()[-1].split("|")
        assert base_url == _GATEWAY
        assert modele == "brain"


class TestEnTetesDuJournal:
    def test_identite_fixe_et_source_system(self, prod_mlx, monkeypatch):
        """`X-Klody-Source: system` : sans lui, `app=klody-ai` est classé `user`
        et chaque extraction — une par message — deviendrait un tour
        utilisateur pour le miner d'habitudes (JOURNAL-USAGE-SPEC §3.6)."""
        Faux = _faux_openai()
        monkeypatch.setattr(memory_extractor, "OpenAI", Faux)

        memory_extractor.extract_and_save(_msgs(3), _lt_mock())

        en_tetes = Faux.crees[0].kwargs["default_headers"]
        assert en_tetes["X-Klody-App"] == "klody-ai"
        assert en_tetes["X-Klody-Source"] == "system"

    def test_la_session_part_par_requete(self, prod_mlx, monkeypatch):
        """Client partagé entre toutes les sessions ⇒ la session ne peut pas
        vivre dans ses en-têtes par défaut."""
        Faux = _faux_openai()
        monkeypatch.setattr(memory_extractor, "OpenAI", Faux)

        memory_extractor.extract_and_save(_msgs(3), _lt_mock(), session_id="abc123")
        memory_extractor.extract_mid_session(_msgs(8), _lt_mock(), session_id="def456")
        memory_extractor.extract_and_save(_msgs(3), _lt_mock())

        assert Faux.appels[0]["extra_headers"] == {"X-Klody-Session": "abc123"}
        assert Faux.appels[1]["extra_headers"] == {"X-Klody-Session": "def456"}
        assert Faux.appels[2]["extra_headers"] is None
        assert "X-Klody-Session" not in Faux.crees[0].kwargs["default_headers"]


class TestLaPanneSeVoit:
    def test_trois_verdicts_distincts(self, prod_mlx, monkeypatch):
        """« Jamais tentée » ne doit pas se lire « ça marche »."""
        assert memory_extractor.etat_extraction()["verdict"] == "non_tentee"

        monkeypatch.setattr(memory_extractor, "OpenAI", _faux_openai())
        memory_extractor.extract_and_save(_msgs(3), _lt_mock())
        assert memory_extractor.etat_extraction()["verdict"] == "operationnelle"

        monkeypatch.setattr(memory_extractor, "OpenAI", _faux_openai(erreur=_erreur_connexion()))
        memory_extractor.extract_and_save(_msgs(3), _lt_mock())
        etat = memory_extractor.etat_extraction()
        assert etat["verdict"] == "en_echec"
        assert etat["echecs_consecutifs"] == 1
        assert etat["cible"] == {"backend": "mlx", "base_url": _GATEWAY, "modele": "brain"}

    def test_la_cause_nomme_le_gateway_pas_ollama(self, prod_mlx, monkeypatch):
        """« Erreur LLM : Connection error. » ne disait ni quoi ni où."""
        monkeypatch.setattr(memory_extractor, "OpenAI", _faux_openai(erreur=_erreur_connexion()))

        memory_extractor.extract_and_save(_msgs(3), _lt_mock())

        cause = memory_extractor.etat_extraction()["derniere_erreur"]
        assert _GATEWAY in cause
        assert "gateway" in cause.lower()
        assert "ollama" not in cause.lower()

    def test_seuil_hors_service_est_3(self):
        """Verrouillé sur un LITTÉRAL : un test qui recalcule l'âge à partir du
        seuil qu'il protège suit ce seuil au lieu de le juger (mutation
        `MUETTE_JOURS → 99999` échappée sur la veille Qwen, 2026-08-10)."""
        assert memory_extractor.SEUIL_HORS_SERVICE == 3

    def test_le_troisieme_echec_consecutif_passe_en_error(self, prod_mlx, monkeypatch, caplog):
        monkeypatch.setattr(memory_extractor, "OpenAI", _faux_openai(erreur=_erreur_connexion()))

        with caplog.at_level(logging.WARNING, logger="agent.memory_extractor"):
            for _ in range(3):  # littéral : cf. test_seuil_hors_service_est_3
                memory_extractor.extract_and_save(_msgs(3), _lt_mock())

        niveaux = [r.levelno for r in caplog.records]
        assert niveaux == [logging.WARNING, logging.WARNING, logging.ERROR]
        assert "HORS SERVICE" in caplog.records[-1].getMessage()
        assert _GATEWAY in caplog.records[-1].getMessage()
        assert memory_extractor.etat_extraction()["hors_service"] is True

    def test_mi_session_compte_dans_la_meme_serie(self, prod_mlx, monkeypatch, caplog):
        """Les deux chemins partagent le compteur : ils visent la même cible."""
        monkeypatch.setattr(memory_extractor, "OpenAI", _faux_openai(erreur=_erreur_connexion()))

        with caplog.at_level(logging.WARNING, logger="agent.memory_extractor"):
            memory_extractor.extract_and_save(_msgs(3), _lt_mock())
            memory_extractor.extract_and_save(_msgs(3), _lt_mock())
            memory_extractor.extract_mid_session(_msgs(8), _lt_mock())

        assert caplog.records[-1].levelno == logging.ERROR
        assert caplog.records[-1].getMessage().startswith("[Extractor-mid]")

    def test_une_reussite_retablit_et_le_dit(self, prod_mlx, monkeypatch, caplog):
        monkeypatch.setattr(memory_extractor, "OpenAI", _faux_openai(erreur=_erreur_connexion()))
        for _ in range(3):
            memory_extractor.extract_and_save(_msgs(3), _lt_mock())

        monkeypatch.setattr(memory_extractor, "OpenAI", _faux_openai())
        with caplog.at_level(logging.WARNING, logger="agent.memory_extractor"):
            memory_extractor.extract_and_save(_msgs(3), _lt_mock())

        etat = memory_extractor.etat_extraction()
        assert etat["verdict"] == "operationnelle"
        assert etat["echecs_consecutifs"] == 0 and etat["derniere_erreur"] is None
        assert any("RÉTABLIE" in r.getMessage() for r in caplog.records)

    def test_session_trop_courte_ne_touche_pas_l_etat(self, prod_mlx, monkeypatch):
        """Aucun appel ⇒ aucun verdict : ne pas compter une absence comme un succès."""
        Faux = _faux_openai()
        monkeypatch.setattr(memory_extractor, "OpenAI", Faux)

        memory_extractor.extract_and_save(_msgs(1), _lt_mock())

        assert Faux.appels == []
        assert memory_extractor.etat_extraction()["verdict"] == "non_tentee"


class TestSurfaces:
    """La CLI et l'API DISENT l'état ; avant, seul un WARNING le portait."""

    @pytest.fixture
    def capture(self, monkeypatch):
        import io

        import main
        from rich.console import Console

        tampon = io.StringIO()
        monkeypatch.setattr(main, "console", Console(file=tampon, width=200, no_color=True))
        monkeypatch.setattr(main, "get_long_term_memory", _lt_mock)
        return tampon

    @staticmethod
    def _orchestrateur():
        return SimpleNamespace(memory=SimpleNamespace(messages=_msgs(3), session_id="s1"))

    def test_fin_de_session_cli_dit_la_panne(self, prod_mlx, monkeypatch, capture):
        import main

        monkeypatch.setattr(memory_extractor, "OpenAI", _faux_openai(erreur=_erreur_connexion()))

        main._run_extraction(self._orchestrateur())

        sortie = capture.getvalue()
        assert "Mémoire automatique en panne" in sortie
        assert _GATEWAY in sortie

    def test_fin_de_session_cli_muette_sur_rien_a_retenir(self, prod_mlx, monkeypatch, capture):
        """Une liste vide sans échec = « rien à retenir » : pas d'alarme."""
        import main

        monkeypatch.setattr(memory_extractor, "OpenAI", _faux_openai("[]"))

        main._run_extraction(self._orchestrateur())

        assert "panne" not in capture.getvalue()

    def test_la_cli_transmet_la_session(self, prod_mlx, monkeypatch, capture):
        import main

        Faux = _faux_openai()
        monkeypatch.setattr(memory_extractor, "OpenAI", Faux)

        main._run_extraction(self._orchestrateur())

        assert Faux.appels[0]["extra_headers"] == {"X-Klody-Session": "s1"}

    def test_ligne_status_cli(self, prod_mlx, monkeypatch):
        import main

        assert "non tentée" in main._ligne_extraction()[1]

        monkeypatch.setattr(memory_extractor, "OpenAI", _faux_openai(erreur=_erreur_connexion()))
        memory_extractor.extract_and_save(_msgs(3), _lt_mock())
        _service, etat, detail = main._ligne_extraction()
        assert "1 échec" in etat
        assert _GATEWAY in detail

    def test_l_api_transmet_la_session(self, monkeypatch):
        from api import server

        recus: list[dict] = []
        monkeypatch.setattr(
            server, "extract_and_save",
            lambda messages, lt, **kw: recus.append(kw) or [],
        )

        server._extract_memory_bg(_msgs(3), _lt_mock(), "ws-42")

        assert recus == [{"session_id": "ws-42"}]

    def test_api_status_expose_l_etat(self, prod_mlx, monkeypatch):
        """Informatif : `/api/status` reste 200 — une mémoire qui n'apprend plus
        ne rend pas l'API indisponible."""
        from api import server
        from api.server import app
        from fastapi.testclient import TestClient

        async def _toujours_up(url, timeout=1.5, accept_status=(200,)):
            return True

        class _SansReseau:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, *a, **k):
                raise OSError(f"réseau coupé en test : {url}")

        monkeypatch.setattr(server, "_probe_url", _toujours_up)
        monkeypatch.setattr(server.httpx, "AsyncClient", _SansReseau)
        monkeypatch.setattr(server, "_load_project_info", lambda: {"workdir": "/tmp"})
        monkeypatch.setattr(server, "get_librarybrain_status", lambda: {"up": False})
        monkeypatch.setattr(memory_extractor, "OpenAI", _faux_openai(erreur=_erreur_connexion()))
        memory_extractor.extract_and_save(_msgs(3), _lt_mock())

        r = TestClient(app, base_url="http://127.0.0.1:8000").get("/api/status")

        assert r.status_code == 200
        etat = r.json()["extraction_memoire"]
        assert etat["verdict"] == "en_echec"
        assert etat["cible"]["base_url"] == _GATEWAY


class TestHermeticite:
    """Le garde de session de `tests/conftest.py` mord-il vraiment ?"""

    def test_sans_patch_local_le_client_est_le_faux_de_session(self, prod_mlx):
        from openai import OpenAI as VraiOpenAI

        from tests.conftest import ClientExtractionHorsReseau

        client = memory_extractor._client_llm()

        assert isinstance(client, ClientExtractionHorsReseau)
        assert not isinstance(client, VraiOpenAI), (
            "sans le garde, le thread mem-extractor des tests WebSocket appelle "
            "le vrai brain (2 appels par passe, mesurés le 2026-09-27)"
        )

    def test_la_garde_est_de_portee_session(self, request):
        """Précaution verrouillée : `mem-extractor` est un démon lancé après
        l'envoi de `done`, rien ne garantit qu'il démarre avant la fin du test
        (où un `monkeypatch` de test serait restauré). Course NON observée le
        2026-09-27 (portée test : 0 appel réel sur 5 passes) — la portée
        session la ferme par construction. Ce que la garde évite, lui, est
        mesuré : SANS garde, +3 appels réels à `brain` par passe de
        `test_websocket_chat.py`."""
        defs = request._fixturemanager.getfixturedefs(
            "_extraction_memoire_hors_reseau", request.node
        )
        assert defs, "garde de session absente de tests/conftest.py"
        assert defs[-1].scope == "session"
