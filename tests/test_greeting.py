"""L'accueil généré ne doit JAMAIS coûter le démarrage.

La forme du module vient d'une mesure, pas d'une préférence (2026-08-01,
gateway :8090, brain → Qwen3.6-35B-A3B-8bit) : 0,37 s à chaud, **6,52 s quand
le gateway dort**. C'est la variance qui interdit le synchrone, et c'est elle
que ces tests verrouillent — une échéance qui ne serait pas respectée ferait
exactement le gel qu'on cherche à éviter, sans que rien ne rougisse.

Trois propriétés, dans l'ordre d'importance :
  1. `recuperer()` n'attend jamais au-delà de son échéance et ne lève jamais ;
  2. l'appel ne porte AUCUN schéma d'outil (~12,5 k tokens contre ~150) ;
  3. le repli local n'appelle rien du tout.
"""
from __future__ import annotations

import threading
import time

import pytest
from agent import greeting
from agent.greeting import AccueilEnTacheDeFond, ContexteAccueil, accueil_local, nettoyer


class _FauxClient:
    """Client OpenAI minimal : enregistre les appels, peut tarder ou échouer."""

    def __init__(self, contenu: str = "Bonjour !", *, exc: Exception | None = None,
                 delai: float = 0.0) -> None:
        self.contenu = contenu
        self.exc = exc
        self.delai = delai
        self.appels: list[dict] = []
        self.chat = self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.appels.append(kwargs)
        if self.delai:
            time.sleep(self.delai)
        if self.exc:
            raise self.exc

        class _Message:
            content = self.contenu

        class _Choix:
            message = _Message()

        class _Reponse:
            choices = [_Choix()]

        return _Reponse()


CTX = ContexteAccueil(projet="klody-code-ai", sessions_passees=12)


class TestAccueilLocal:
    def test_premiere_session(self):
        assert "première session" in accueil_local(ContexteAccueil("proj", 0))
        assert "première session" in accueil_local(ContexteAccueil("proj", 1))

    def test_sessions_multiples(self):
        texte = accueil_local(ContexteAccueil("proj", 12))
        assert "12 sessions" in texte and "proj" in texte

    def test_n_appelle_rien(self, monkeypatch):
        """Le socle doit tenir même sans réseau — c'est tout son intérêt."""
        def _interdit(*_a, **_k):
            raise AssertionError("le repli local ne doit contacter personne")

        monkeypatch.setattr(greeting.AccueilEnTacheDeFond, "_construire_client", _interdit)
        assert accueil_local(CTX)


class TestNettoyer:
    def test_retire_le_markdown_et_les_guillemets(self):
        assert nettoyer('**« Bonjour »**') == "Bonjour"

    def test_ramene_sur_une_ligne(self):
        assert nettoyer("Bonjour\n\n  le   monde") == "Bonjour le monde"

    def test_tronque_sur_une_fin_de_phrase(self):
        texte = nettoyer("A" * 130 + ". " + "B" * 200)
        assert len(texte) <= greeting._CAP
        assert texte.endswith(".")

    def test_tronque_meme_sans_ponctuation(self):
        texte = nettoyer("A" * 400)
        assert len(texte) <= greeting._CAP + 1
        assert texte.endswith("…")

    def test_vide_reste_vide(self):
        assert nettoyer("") == ""
        assert nettoyer("   \n ") == ""


class TestTacheDeFond:
    def test_desactive_ne_lance_aucun_thread(self):
        client = _FauxClient()
        accueil = AccueilEnTacheDeFond(CTX, actif=False, client=client)
        accueil.demarrer()
        assert accueil.recuperer() == accueil_local(CTX)
        assert client.appels == []
        assert accueil.genere is False

    def test_texte_genere_remplace_le_socle(self):
        accueil = AccueilEnTacheDeFond(CTX, actif=True, echeance_s=5,
                                       client=_FauxClient("Bonjour, on reprend."))
        accueil.demarrer()
        assert accueil.recuperer() == "Bonjour, on reprend."
        assert accueil.genere is True

    def test_l_appel_ne_porte_aucun_schema_d_outil(self):
        """~12,5 k tokens de prefill contre ~150 : deux ordres de grandeur."""
        client = _FauxClient()
        accueil = AccueilEnTacheDeFond(CTX, actif=True, echeance_s=5, client=client)
        accueil.demarrer()
        accueil.recuperer()
        assert client.appels and "tools" not in client.appels[0]

    def test_echec_de_l_appel_retombe_en_silence(self):
        accueil = AccueilEnTacheDeFond(
            CTX, actif=True, echeance_s=5, client=_FauxClient(exc=RuntimeError("503")),
        )
        accueil.demarrer()
        assert accueil.recuperer() == accueil_local(CTX)
        assert accueil.genere is False

    def test_reponse_vide_retombe_sur_le_socle(self):
        accueil = AccueilEnTacheDeFond(CTX, actif=True, echeance_s=5,
                                       client=_FauxClient("   "))
        accueil.demarrer()
        assert accueil.recuperer() == accueil_local(CTX)

    def test_l_echeance_est_TENUE_quand_le_gateway_traine(self):
        """LE test du module : 6,52 s mesurées à froid ne doivent pas se voir."""
        accueil = AccueilEnTacheDeFond(
            CTX, actif=True, echeance_s=0.05, client=_FauxClient(delai=3.0),
        )
        accueil.demarrer()
        t0 = time.monotonic()
        texte = accueil.recuperer()
        ecoule = time.monotonic() - t0
        assert texte == accueil_local(CTX)
        assert ecoule < 1.0, f"attente de {ecoule:.2f}s — l'échéance n'a pas tenu"

    def test_le_thread_est_demon(self):
        """Quitter pendant la génération ne doit pas retenir le processus."""
        accueil = AccueilEnTacheDeFond(CTX, actif=True, echeance_s=0.05,
                                       client=_FauxClient(delai=2.0))
        accueil.demarrer()
        vivants = [t for t in threading.enumerate() if t.name == "klody-accueil"]
        assert vivants and all(t.daemon for t in vivants)

    def test_demarrer_deux_fois_ne_lance_qu_un_appel(self):
        client = _FauxClient()
        accueil = AccueilEnTacheDeFond(CTX, actif=True, echeance_s=5, client=client)
        accueil.demarrer()
        accueil.demarrer()
        accueil.recuperer()
        assert len(client.appels) == 1

    def test_recuperer_sans_demarrer_rend_le_socle_tout_de_suite(self):
        accueil = AccueilEnTacheDeFond(CTX, actif=True, echeance_s=10,
                                       client=_FauxClient())
        t0 = time.monotonic()
        assert accueil.recuperer() == accueil_local(CTX)
        assert time.monotonic() - t0 < 0.5

    def test_le_contexte_est_transmis_au_modele(self):
        client = _FauxClient()
        ctx = ContexteAccueil("mon-projet", 7, faits=("aime le français",))
        accueil = AccueilEnTacheDeFond(ctx, actif=True, echeance_s=5, client=client)
        accueil.demarrer()
        accueil.recuperer()
        envoye = client.appels[0]["messages"][-1]["content"]
        assert "mon-projet" in envoye and "7" in envoye and "aime le français" in envoye


class TestCollecterContexte:
    def test_compte_les_sessions_du_disque(self, monkeypatch, tmp_path):
        for i in range(3):
            (tmp_path / f"memory_{i}.json").write_text("{}", encoding="utf-8")
        (tmp_path / "autre.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(greeting, "MEMORY_DIR", tmp_path)
        monkeypatch.setattr(greeting, "PROJECT_ROOT", tmp_path / "klody-code-ai")
        ctx = greeting.collecter_contexte()
        assert ctx.sessions_passees == 3
        assert ctx.projet == "klody-code-ai"

    def test_memoire_long_terme_indisponible_n_empeche_pas_l_accueil(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(greeting, "MEMORY_DIR", tmp_path)
        monkeypatch.setattr(greeting, "PROJECT_ROOT", tmp_path)

        import agent.long_term_memory as ltm

        def _casse():
            raise RuntimeError("SQLite absent")

        monkeypatch.setattr(ltm, "get_long_term_memory", _casse)
        ctx = greeting.collecter_contexte()
        assert ctx.faits == ()
        assert accueil_local(ctx)

    def test_ne_garde_que_les_faits_utiles(self, monkeypatch, tmp_path):
        monkeypatch.setattr(greeting, "MEMORY_DIR", tmp_path)
        monkeypatch.setattr(greeting, "PROJECT_ROOT", tmp_path)

        import agent.long_term_memory as ltm

        class _Faux:
            @staticmethod
            def list_all():
                return [
                    {"category": "user", "content": "un"},
                    {"category": "project", "content": "ignoré"},
                    {"category": "preference", "content": "deux"},
                    {"category": "user", "content": ""},
                    {"category": "user", "content": "trois"},
                    {"category": "user", "content": "quatre"},
                ]

        monkeypatch.setattr(ltm, "get_long_term_memory", lambda: _Faux())
        ctx = greeting.collecter_contexte()
        assert ctx.faits == ("un", "deux", "trois")


def test_le_vrai_client_ne_reessaie_pas():
    """Un accueil raté n'a aucune valeur au 2ᵉ essai : l'échéance est passée."""
    client = AccueilEnTacheDeFond(CTX, actif=False)._construire_client()
    assert client.max_retries == 0


@pytest.mark.parametrize("sessions", [0, 1, 2, 999])
def test_le_socle_rend_toujours_une_phrase(sessions):
    texte = accueil_local(ContexteAccueil("p", sessions))
    assert texte.startswith("Bonjour") and texte.endswith(".")
