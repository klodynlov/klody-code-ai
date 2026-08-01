"""L'accueil interactif ne doit JAMAIS coûter le démarrage — ni son interactivité.

La forme du module vient d'une mesure (2026-08-01 : 0,37 s à chaud, 6,52 s quand
le gateway dort — c'est la variance qui interdit le synchrone). Ces tests
verrouillent en plus le contrat propre à l'interactivité :

  1. `recuperer()` n'attend jamais au-delà de son échéance et ne lève jamais ;
  2. l'appel ne porte AUCUN schéma d'outil ;
  3. le SOCLE local rend toujours au moins une proposition — sans quoi
     « interactif » deviendrait conditionnel à la disponibilité du gateway ;
  4. le repli sur un format partiel du modèle est PARTIEL : une salutation
     réussie ne fait pas perdre les propositions du socle.
"""
from __future__ import annotations

import json
import threading
import time

import pytest
from agent import greeting
from agent.greeting import (
    Accueil,
    AccueilEnTacheDeFond,
    ContexteAccueil,
    accueil_local,
    analyser,
    nettoyer,
)


class _FauxClient:
    """Client OpenAI minimal : enregistre les appels, peut tarder ou échouer."""

    def __init__(self, contenu: str = "BONJOUR: Salut !", *, exc: Exception | None = None,
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
CTX_AVEC_HIER = ContexteAccueil(
    projet="klody-code-ai", sessions_passees=12,
    derniere_tache="mesurer le coût du garde doc", derniere_date="hier",
)

_REPONSE_COMPLETE = (
    "BONJOUR: Bonjour, content de te retrouver.\n"
    "RAPPEL: Hier tu mesurais le coût du garde doc.\n"
    "- Reprendre la mesure du garde\n"
    "- Relancer le banc discovery\n"
    "- Vérifier la CI de la PR 192\n"
)


class TestSocleLocal:
    def test_premiere_session(self):
        rendu = accueil_local(ContexteAccueil("proj", 0))
        assert "première session" in rendu.salutation

    def test_sessions_multiples(self):
        rendu = accueil_local(CTX)
        assert "12 sessions" in rendu.salutation

    def test_le_socle_propose_TOUJOURS_au_moins_une_action(self):
        """Le contrat central : l'interactivité ne dépend pas du gateway."""
        for ctx in (CTX, CTX_AVEC_HIER, ContexteAccueil("p", 0)):
            assert accueil_local(ctx).propositions

    def test_avec_une_session_precedente_le_rappel_cite_la_demande(self):
        rendu = accueil_local(CTX_AVEC_HIER)
        assert "mesurer le coût du garde doc" in rendu.rappel
        assert "Hier" in rendu.rappel
        assert rendu.propositions[0] == "Reprendre là où on s'est arrêté"

    def test_sans_session_precedente_pas_de_rappel_invente(self):
        rendu = accueil_local(CTX)
        assert rendu.rappel == ""
        assert "Reprendre" not in rendu.propositions[0]

    def test_n_appelle_rien(self, monkeypatch):
        def _interdit(*_a, **_k):
            raise AssertionError("le socle ne doit contacter personne")

        monkeypatch.setattr(greeting.AccueilEnTacheDeFond, "_construire_client", _interdit)
        assert accueil_local(CTX).salutation


class TestEnTexte:
    def test_numerote_les_propositions_a_l_oral(self):
        """Quelqu'un qui n'a que le son doit pouvoir répondre « 2 »."""
        texte = Accueil("Bonjour.", "Hier : X.", ("A", "B")).en_texte()
        assert "1. A" in texte and "2. B" in texte
        assert texte.index("Bonjour.") < texte.index("Hier : X.") < texte.index("1. A")

    def test_sans_rappel_ni_propositions(self):
        assert Accueil("Bonjour.").en_texte() == "Bonjour."


class TestAnalyser:
    def test_reponse_complete(self):
        rendu = analyser(_REPONSE_COMPLETE, accueil_local(CTX))
        assert rendu.salutation == "Bonjour, content de te retrouver."
        assert "garde doc" in rendu.rappel
        assert len(rendu.propositions) == 3

    def test_repli_PARTIEL_champ_par_champ(self):
        """Une salutation réussie ne fait pas perdre les propositions du socle."""
        socle = accueil_local(CTX)
        rendu = analyser("BONJOUR: Salut à toi.", socle)
        assert rendu.salutation == "Salut à toi."
        assert rendu.propositions == socle.propositions

    def test_texte_illisible_rend_le_socle_entier(self):
        socle = accueil_local(CTX_AVEC_HIER)
        assert analyser("n'importe quoi sans format", socle) == socle

    def test_les_propositions_excedentaires_sont_coupees(self):
        brut = "BONJOUR: S.\n" + "\n".join(f"- p{i}" for i in range(8))
        rendu = analyser(brut, accueil_local(CTX))
        assert len(rendu.propositions) == greeting._MAX_PROPOSITIONS

    def test_le_markdown_est_nettoye(self):
        rendu = analyser("BONJOUR: **« Salut »**\n- `run` les *tests*",
                         accueil_local(CTX))
        assert rendu.salutation == "Salut"
        assert rendu.propositions == ("run les tests",)


class TestNettoyer:
    def test_ramene_sur_une_ligne(self):
        assert nettoyer("Bonjour\n\n  le   monde") == "Bonjour le monde"

    def test_tronque(self):
        assert len(nettoyer("A" * 500)) <= greeting._CAP_LIGNE

    def test_vide_reste_vide(self):
        assert nettoyer("   \n ") == ""


class TestTacheDeFond:
    def test_desactive_ne_lance_aucun_thread(self):
        client = _FauxClient()
        accueil = AccueilEnTacheDeFond(CTX, actif=False, client=client)
        accueil.demarrer()
        assert accueil.recuperer() == accueil_local(CTX)
        assert client.appels == []

    def test_reponse_du_modele_remplace_le_socle(self):
        accueil = AccueilEnTacheDeFond(CTX, actif=True, echeance_s=5,
                                       client=_FauxClient(_REPONSE_COMPLETE))
        accueil.demarrer()
        rendu = accueil.recuperer()
        assert rendu.salutation == "Bonjour, content de te retrouver."
        assert accueil.genere is True

    def test_l_appel_ne_porte_aucun_schema_d_outil(self):
        client = _FauxClient()
        accueil = AccueilEnTacheDeFond(CTX, actif=True, echeance_s=5, client=client)
        accueil.demarrer()
        accueil.recuperer()
        assert client.appels and "tools" not in client.appels[0]

    def test_echec_de_l_appel_retombe_sur_le_socle_ENTIER(self):
        accueil = AccueilEnTacheDeFond(
            CTX_AVEC_HIER, actif=True, echeance_s=5,
            client=_FauxClient(exc=RuntimeError("503")),
        )
        accueil.demarrer()
        rendu = accueil.recuperer()
        assert rendu == accueil_local(CTX_AVEC_HIER)
        assert rendu.propositions  # l'interactivité survit à la panne

    def test_l_echeance_est_TENUE_quand_le_gateway_traine(self):
        """LE test du module : 6,52 s mesurées à froid ne doivent pas se voir."""
        accueil = AccueilEnTacheDeFond(
            CTX, actif=True, echeance_s=0.05, client=_FauxClient(delai=3.0),
        )
        accueil.demarrer()
        t0 = time.monotonic()
        rendu = accueil.recuperer()
        ecoule = time.monotonic() - t0
        assert rendu == accueil_local(CTX)
        assert ecoule < 1.0, f"attente de {ecoule:.2f}s — l'échéance n'a pas tenu"

    def test_le_thread_est_demon(self):
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
        ctx = ContexteAccueil("mon-projet", 7, faits=("aime le français",),
                              derniere_tache="corriger /status", derniere_date="hier")
        accueil = AccueilEnTacheDeFond(ctx, actif=True, echeance_s=5, client=client)
        accueil.demarrer()
        accueil.recuperer()
        envoye = client.appels[0]["messages"][-1]["content"]
        assert "mon-projet" in envoye
        assert "corriger /status" in envoye
        assert "aime le français" in envoye


class TestCollecterContexte:
    @staticmethod
    def _session(dossier, nom, messages):
        (dossier / f"memory_{nom}.json").write_text(
            json.dumps({"session_id": nom, "messages": messages,
                        "created_at": "2026-08-01"}),
            encoding="utf-8",
        )

    def test_compte_les_sessions_et_lit_la_derniere_demande(self, monkeypatch, tmp_path):
        self._session(tmp_path, "a", [{"role": "user", "content": "vieille demande"}])
        time.sleep(0.01)  # mtime distinct : « la plus récente » doit être b
        self._session(tmp_path, "b", [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "mesurer le coût du garde"},
        ])
        monkeypatch.setattr(greeting, "MEMORY_DIR", tmp_path)
        monkeypatch.setattr(greeting, "PROJECT_ROOT", tmp_path / "klody-code-ai")
        ctx = greeting.collecter_contexte()
        assert ctx.sessions_passees == 2
        assert ctx.derniere_tache == "mesurer le coût du garde"
        assert ctx.derniere_date  # relative, dépend de l'horloge — non figée ici

    def test_session_corrompue_n_empeche_pas_l_accueil(self, monkeypatch, tmp_path):
        (tmp_path / "memory_x.json").write_text("{pas du json", encoding="utf-8")
        monkeypatch.setattr(greeting, "MEMORY_DIR", tmp_path)
        monkeypatch.setattr(greeting, "PROJECT_ROOT", tmp_path)
        ctx = greeting.collecter_contexte()
        assert ctx.derniere_tache == ""
        assert accueil_local(ctx).propositions

    def test_memoire_long_terme_indisponible_n_empeche_pas_l_accueil(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(greeting, "MEMORY_DIR", tmp_path)
        monkeypatch.setattr(greeting, "PROJECT_ROOT", tmp_path)

        import agent.long_term_memory as ltm

        def _casse():
            raise RuntimeError("SQLite absent")

        monkeypatch.setattr(ltm, "get_long_term_memory", _casse)
        assert greeting.collecter_contexte().faits == ()


class TestDateRelative:
    @pytest.mark.parametrize(("ecart_s", "attendu"), [
        (3600, "plus tôt aujourd'hui"),
        (90000, "hier"),
        (86400 * 3, "il y a 3 jours"),
        (86400 * 30, "la dernière fois"),
    ])
    def test_paliers(self, ecart_s, attendu):
        assert greeting._date_relative(1000.0, maintenant=1000.0 + ecart_s) == attendu


def test_le_vrai_client_ne_reessaie_pas():
    client = AccueilEnTacheDeFond(CTX, actif=False)._construire_client()
    assert client.max_retries == 0
