"""Tests klody_mcp.samplebrain_server — serveurs SampleBrain mockés (_request)."""
from __future__ import annotations

import pytest
from klody_mcp import samplebrain_server as sb


@pytest.fixture
def deux_bibliotheques(monkeypatch):
    """Le cas de production : l'index interne et celui du disque externe."""
    monkeypatch.setattr(sb, "BIBLIOTHEQUES", {
        "principale": "http://127.0.0.1:8788",
        "externe": "http://127.0.0.1:8799",
    })


def _hit(nom, distance, extra=None):
    h = {"content_hash": nom, "distance": distance, "paths": [f"/S/{nom}.wav"]}
    if extra:
        h.update(extra)
    return h


# ------------------------------------------------------- lecture de conf --

def test_repli_sur_la_variable_historique(monkeypatch):
    """`SAMPLEBRAIN_URL` seule doit rester valide telle quelle."""
    monkeypatch.delenv("SAMPLEBRAIN_URLS", raising=False)
    monkeypatch.setenv("SAMPLEBRAIN_URL", "http://127.0.0.1:9999/")
    assert sb._lire_bibliotheques() == {"principale": "http://127.0.0.1:9999"}


def test_lecture_de_plusieurs_bibliotheques(monkeypatch):
    monkeypatch.setenv(
        "SAMPLEBRAIN_URLS",
        " principale=http://127.0.0.1:8788 , externe=http://127.0.0.1:8799/ ",
    )
    assert sb._lire_bibliotheques() == {
        "principale": "http://127.0.0.1:8788",
        "externe": "http://127.0.0.1:8799",
    }


@pytest.mark.parametrize("brut", ["", "   ", ",,", "=http://x", "nom="])
def test_conf_vide_ou_bancale_rend_un_defaut_utilisable(monkeypatch, brut):
    """Une conf illisible ne doit pas rendre l'outil muet et sans explication."""
    monkeypatch.setenv("SAMPLEBRAIN_URLS", brut)
    monkeypatch.delenv("SAMPLEBRAIN_URL", raising=False)
    assert sb._lire_bibliotheques() == {"principale": "http://127.0.0.1:8788"}


def test_bibliotheque_inconnue_est_signalee(deux_bibliotheques):
    """Rendre une liste vide la rendrait indiscernable d'un corpus sans
    résultat — c'est une faute de frappe, elle doit se voir."""
    with pytest.raises(RuntimeError, match="bibliothèque inconnue"):
        sb.chercher_samples("kick", bibliotheque="externne")


# ------------------------------------------------------------ recherche --

def test_les_deux_bibliotheques_sont_interrogees(deux_bibliotheques, monkeypatch):
    vues = []

    def fake(path, params=None, base=None, nom="principale"):
        vues.append((nom, base, params["k"]))
        return {"model": "clap", "hits": [_hit(nom, 0.5)]}

    monkeypatch.setattr(sb, "_request", fake)
    out = sb.chercher_samples("kick", k=3)

    assert sorted(n for n, _, _ in vues) == ["externe", "principale"]
    assert {b for _, b, _ in vues} == {"http://127.0.0.1:8788",
                                       "http://127.0.0.1:8799"}
    assert out["classement_fusionne"] is True
    assert {r["bibliotheque"] for r in out["resultats"]} == {"principale", "externe"}


def test_le_classement_fusionne_trie_par_distance(deux_bibliotheques, monkeypatch):
    def fake(path, params=None, base=None, nom="principale"):
        if nom == "principale":
            return {"model": "clap", "hits": [_hit("loin", 0.9), _hit("moyen", 0.5)]}
        return {"model": "clap", "hits": [_hit("proche", 0.1)]}

    monkeypatch.setattr(sb, "_request", fake)
    out = sb.chercher_samples("kick", k=10)

    assert [r["distance"] for r in out["resultats"]] == [0.1, 0.5, 0.9]
    assert out["resultats"][0]["bibliotheque"] == "externe"


def test_k_borne_le_total_pas_chaque_bibliotheque(deux_bibliotheques, monkeypatch):
    """`k` compte les résultats RENDUS : deux bibliothèques ne doivent pas en
    rendre 2k."""
    def fake(path, params=None, base=None, nom="principale"):
        return {"model": "clap",
                "hits": [_hit(f"{nom}{i}", i / 100) for i in range(10)]}

    monkeypatch.setattr(sb, "_request", fake)
    assert len(sb.chercher_samples("kick", k=6)["resultats"]) == 6


@pytest.mark.parametrize("demande,attendu", [(999, 50), (-3, 1)])
def test_k_est_borne(deux_bibliotheques, monkeypatch, demande, attendu):
    captures = []

    def fake(path, params=None, base=None, nom="principale"):
        captures.append(params["k"])
        return {"model": "clap", "hits": []}

    monkeypatch.setattr(sb, "_request", fake)
    sb.chercher_samples("kick", k=demande)
    assert set(captures) == {attendu}


def test_une_seule_bibliotheque_sur_demande(deux_bibliotheques, monkeypatch):
    vues = []

    def fake(path, params=None, base=None, nom="principale"):
        vues.append(nom)
        return {"model": "clap", "hits": [_hit(nom, 0.4)]}

    monkeypatch.setattr(sb, "_request", fake)
    out = sb.chercher_samples("kick", bibliotheque="externe")
    assert vues == ["externe"]
    assert out["bibliotheques_interrogees"] == ["externe"]


# -------------------------------------------------------- dégradations --

def test_une_bibliotheque_muette_n_emporte_pas_les_autres(deux_bibliotheques,
                                                          monkeypatch):
    """Le disque externe n'est pas toujours branché : son serveur peut être
    arrêté sans que la bibliothèque interne cesse de répondre."""
    def fake(path, params=None, base=None, nom="principale"):
        if nom == "externe":
            raise RuntimeError("ne répond pas sur http://127.0.0.1:8799")
        return {"model": "clap", "hits": [_hit("interne", 0.3)]}

    monkeypatch.setattr(sb, "_request", fake)
    out = sb.chercher_samples("kick")

    assert len(out["resultats"]) == 1
    assert out["resultats"][0]["bibliotheque"] == "principale"
    assert [m["bibliotheque"] for m in out["bibliotheques_muettes"]] == ["externe"]
    assert "8799" in out["bibliotheques_muettes"][0]["raison"]


def test_modeles_differents_interdisent_la_fusion(deux_bibliotheques, monkeypatch):
    """Deux backends = deux échelles. Les classer ensemble rendrait un
    classement d'apparence normale et sans signification."""
    def fake(path, params=None, base=None, nom="principale"):
        modele = "clap" if nom == "principale" else "autre-modele"
        return {"model": modele, "hits": [_hit(nom, 0.9 if nom == "principale" else 0.1)]}

    monkeypatch.setattr(sb, "_request", fake)
    out = sb.chercher_samples("kick")

    assert out["classement_fusionne"] is False
    assert "NON comparables" in out["avertissement"]
    # groupé par bibliothèque, donc PAS trié par distance croissante
    assert [r["bibliotheque"] for r in out["resultats"]] == ["externe", "principale"]


# ------------------------------------------------------------ formatage --

def test_les_variantes_groupees_remontent_en_chemins(deux_bibliotheques,
                                                     monkeypatch):
    """Le serveur regroupe les quasi-doublons ; un agent qui cherche un fichier
    à placer a besoin de TOUS les chemins qui marchent."""
    def fake(path, params=None, base=None, nom="principale"):
        if nom != "principale":
            return {"model": "clap", "hits": []}
        return {"model": "clap", "hits": [{
            "content_hash": "aaa", "distance": 0.87,
            "paths": ["/S/CHORDS/Strum.wav", "/S/MIDI/Strum.wav"],
            "variants": [{"content_hash": "bbb", "paths": ["/S/AIF/Strum.aif"]}],
        }]}

    monkeypatch.setattr(sb, "_request", fake)
    hit = sb.chercher_samples("warm rhodes", k=2)["resultats"][0]

    assert hit["fichier"] == "Strum.wav"
    assert hit["chemin"] == "/S/CHORDS/Strum.wav"
    assert hit["autres_chemins"] == ["/S/MIDI/Strum.wav", "/S/AIF/Strum.aif"]
    assert hit["distance"] == 0.87


def test_un_hit_sans_chemin_est_ignore(deux_bibliotheques, monkeypatch):
    def fake(path, params=None, base=None, nom="principale"):
        return {"model": "clap", "hits": [
            _hit("ok", 0.5),
            {"content_hash": "vide", "distance": 0.6, "paths": []},
        ]}

    monkeypatch.setattr(sb, "_request", fake)
    out = sb.chercher_samples("kick")
    assert all(r["chemin"] for r in out["resultats"])
    assert len(out["resultats"]) == 2  # un par bibliothèque, les vides écartés


def test_description_vide_ne_touche_pas_le_reseau(deux_bibliotheques, monkeypatch):
    def boom(*a, **kw):  # pragma: no cover
        raise AssertionError("ne doit pas être appelé")

    monkeypatch.setattr(sb, "_request", boom)
    assert sb.chercher_samples("   ") == {"erreur": "description vide"}


# --------------------------------------------------------------- statut --

def test_statut_couvre_chaque_bibliotheque(deux_bibliotheques, monkeypatch):
    def fake(path, params=None, base=None, nom="principale"):
        if nom == "externe":
            raise RuntimeError("ne répond pas sur http://127.0.0.1:8799")
        return {"catalog": {"live_contents": 1106}, "model": "clap"}

    monkeypatch.setattr(sb, "_request", fake)
    out = sb.statut_index()

    assert out["bibliotheques"]["principale"]["catalog"]["live_contents"] == 1106
    assert out["bibliotheques"]["principale"]["url"] == "http://127.0.0.1:8788"
    assert "8799" in out["bibliotheques"]["externe"]["erreur"]


def test_serveur_absent_message_actionnable(monkeypatch):
    import re

    import httpx

    def down(url, params=None, timeout=None):
        raise httpx.ConnectError("refus")

    monkeypatch.setattr(sb.httpx, "get", down)
    with pytest.raises(RuntimeError, match=re.escape("samplebrain.indexer.cli serve")):
        sb._request("/api/status", base="http://127.0.0.1:8788", nom="principale")
