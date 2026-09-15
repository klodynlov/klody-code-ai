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


# ------------------------------------------- recherche PAR LE SON (6.2) --

import wave


def _wav(chemin, secondes=0.3):
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(chemin), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00" * int(2 * 44100 * secondes))
    return chemin


def _hit_kick(nom, distance, secondes=0.4):
    return {"content_hash": nom, "distance": distance, "paths": [f"/S/Kicks/{nom}.wav"],
            "classe": "kick", "secondes": secondes}


def test_par_audio_poste_les_octets_a_chaque_bibliotheque(deux_bibliotheques, monkeypatch, tmp_path):
    requete = _wav(tmp_path / "q.wav")
    vues = []

    def fake(octets, params, base, nom, content_type="audio/wav"):
        vues.append((nom, params, len(octets)))
        return {"model": "clap", "hits": [_hit_kick(nom, 0.5)]}

    monkeypatch.setattr(sb, "_post_audio", fake)
    out = sb.chercher_samples_par_audio(str(requete), k=5, classe="kick", duree_max_sec=1.0)
    assert sorted(n for n, _, _ in vues) == ["externe", "principale"]
    assert all(p == {"k": 5, "classe": "kick", "duree_max": 1.0} for _, p, _ in vues)
    assert all(n_oct == requete.stat().st_size for _, _, n_oct in vues)
    assert out["classement_fusionne"] is True
    assert {r["classe"] for r in out["resultats"]} == {"kick"}
    assert out["resultats"][0]["secondes"] == 0.4
    assert out["requete"]["classe"] == "kick"


def test_par_audio_sans_filtre_n_envoie_pas_de_parametre_vide(deux_bibliotheques, monkeypatch, tmp_path):
    requete = _wav(tmp_path / "q.wav")
    vues = []

    def fake(octets, params, base, nom, content_type="audio/wav"):
        vues.append(params)
        return {"model": "clap", "hits": []}

    monkeypatch.setattr(sb, "_post_audio", fake)
    sb.chercher_samples_par_audio(str(requete))
    assert vues and all(p == {"k": 10} for p in vues)


def test_par_audio_refuse_hors_racines_et_classe_inconnue(deux_bibliotheques, monkeypatch, tmp_path):
    def _interdit(*a, **k):
        raise AssertionError("aucun octet ne doit partir")

    monkeypatch.setattr(sb, "_post_audio", _interdit)
    out = sb.chercher_samples_par_audio("/etc/passwd")
    assert "erreur" in out and "PathGuardViolation" in out["erreur"]
    requete = _wav(tmp_path / "q.wav")
    out = sb.chercher_samples_par_audio(str(requete), classe="tuba")
    assert "classe inconnue" in out["erreur"]
    out = sb.chercher_samples_par_audio(str(tmp_path / "absent.wav"))
    assert "FileNotFoundError" in out["erreur"]


def test_par_audio_bibliotheque_muette_signalee(deux_bibliotheques, monkeypatch, tmp_path):
    requete = _wav(tmp_path / "q.wav")

    def fake(octets, params, base, nom, content_type="audio/wav"):
        if nom == "externe":
            raise RuntimeError("ne répond pas sur http://127.0.0.1:8799")
        return {"model": "clap", "hits": [_hit_kick("a", 0.2)]}

    monkeypatch.setattr(sb, "_post_audio", fake)
    out = sb.chercher_samples_par_audio(str(requete))
    assert len(out["resultats"]) == 1
    assert [m["bibliotheque"] for m in out["bibliotheques_muettes"]] == ["externe"]


@pytest.fixture
def job_kicks(tmp_path, monkeypatch):
    """Un job atelier hub-v2 avec 3 tranches de kick réelles (WAV 0,3 s)."""
    import json
    job = "0123456789abcdef-balanced-core-hub-v2"
    cache = tmp_path / "atelier"
    monkeypatch.setattr(sb, "ATELIER_CACHE_DIR", cache)
    kd = cache / job / "kicks"
    tranches = []
    for i in (1, 2, 3):
        p = _wav(kd / f"kick_{i:02d}.wav")
        tranches.append({"path": str(p), "t_sec": 2.0 * i, "conf": 0.9, "dur_sec": 0.3})
    (kd / "kicks.json").write_text(json.dumps({"n": 3, "n_kicks_total": 42, "tranches": tranches}))
    return job


def test_kicks_pour_morceau_fusionne_par_rangs_reciproques(deux_bibliotheques, monkeypatch, job_kicks):
    """RRF : un kick 2ᵉ pour les 3 tranches bat un kick 1ᵉʳ pour une seule."""
    appels = []

    def fake(octets, params, base, nom, content_type="audio/wav"):
        appels.append((nom, params))
        if nom == "externe":
            return {"model": "clap", "hits": []}
        n = len([a for a in appels if a[0] == "principale"])  # n° de tranche
        premier = _hit_kick(f"solo{n}", 0.10)                   # différent à chaque tranche
        return {"model": "clap", "hits": [premier, _hit_kick("constant", 0.20)]}

    monkeypatch.setattr(sb, "_post_audio", fake)
    out = sb.chercher_kicks_pour_morceau(job_kicks, k=2)
    assert [k["fichier"] for k in out["kicks"]] == ["constant.wav", "solo1.wav"]
    assert out["kicks"][0]["tranches_votantes"] == [1, 2, 3]
    assert out["kicks"][0]["distance_min"] == 0.2
    assert out["kicks"][0]["secondes"] == 0.4
    assert "distance" not in out["kicks"][0]
    assert out["n_kicks_dans_le_morceau"] == 42
    assert len(out["tranches"]) == 3
    # chaque tranche : classe kick + one-shot exigés, 10 candidats
    assert all(p == {"k": 10, "classe": "kick", "duree_max": 1.0} for _, p in appels)
    assert len(appels) == 6   # 3 tranches × 2 bibliothèques


def test_kicks_pour_morceau_refuse_job_id_bancal_et_job_sans_kicks(deux_bibliotheques, monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "ATELIER_CACHE_DIR", tmp_path)
    assert "invalide" in sb.chercher_kicks_pour_morceau("../../etc")["erreur"]
    out = sb.chercher_kicks_pour_morceau("0123456789abcdef-balanced-core-hub-v1")
    assert "aucune tranche" in out["erreur"] and "hub-v2" in out["erreur"]


def test_kicks_pour_morceau_tranche_illisible_signalee(deux_bibliotheques, monkeypatch, job_kicks):
    import json
    meta = sb.ATELIER_CACHE_DIR / job_kicks / "kicks" / "kicks.json"
    d = json.loads(meta.read_text())
    d["tranches"][1]["path"] = "/etc/hosts"     # hors racines
    meta.write_text(json.dumps(d))
    monkeypatch.setattr(sb, "_post_audio",
                        lambda octets, params, base, nom, content_type="audio/wav":
                        {"model": "clap", "hits": [_hit_kick("a", 0.3)]})
    out = sb.chercher_kicks_pour_morceau(job_kicks)
    assert out["kicks"][0]["tranches_votantes"] == [1, 3]
    assert out["bibliotheques_muettes"][0]["tranche"] == 2


def test_urls_partagees_entre_les_deux_modules(monkeypatch):
    from klody_mcp import _samplebrain_urls, reaper_samples
    monkeypatch.setenv("SAMPLEBRAIN_URLS", "a=http://x:1/,b=http://y:2")
    attendu = {"a": "http://x:1", "b": "http://y:2"}
    assert _samplebrain_urls.lire_bibliotheques() == attendu
    assert reaper_samples._bibliotheques() == attendu
    assert sb._lire_bibliotheques() == attendu
