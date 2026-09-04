"""La veille Qwen3.8 doit pouvoir ROUGIR — sinon elle est indiscernable du vert.

C'est le mode de défaillance dominant de ce dépôt : un garde-fou qui ne peut pas
échouer ressemble exactement à un garde-fou satisfait. Une veille est le cas
extrême, puisque son état nominal EST le silence. Trois propriétés se
verrouillent donc ici :

1. « rien trouvé » et « rien regardé » ne rendent PAS le même code de sortie ;
2. un dépôt n'est annoncé qu'après lecture de son `config.json` — un nom de
   dépôt ne prouve rien (`huginnfork/Qwen3.8-27B-FP8` porte le bon nom et n'a
   aucun poids) ;
3. un silence prolongé se DÉNONCE lui-même au lieu de se lire « rien de neuf ».
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import veille_qwen as mod


@pytest.fixture
def etat(tmp_path, monkeypatch):
    """État isolé — jamais celui de ~/Library/Caches/klody."""
    fichier = tmp_path / "veille-qwen.json"
    monkeypatch.setattr(mod, "ETAT_DIR", tmp_path)
    monkeypatch.setattr(mod, "ETAT_FICHIER", fichier)
    return fichier


@pytest.fixture
def notifications(monkeypatch):
    envoyees: list[tuple[str, str]] = []
    monkeypatch.setattr(
        mod, "notifier", lambda titre, corps: (envoyees.append((titre, corps)), True)[1]
    )
    return envoyees


def _bouchon(reponses: dict[str, object]):
    """Rend un `_get_json` bouchonné. Une valeur `Exception` est levée."""

    def faux(url: str):
        for motif, valeur in reponses.items():
            if motif in url:
                if isinstance(valeur, Exception):
                    raise valeur
                return valeur
        raise AssertionError(f"URL non bouchonnée : {url}")

    return faux


def _depot(repo_id: str, cree: str = "2026-08-10T00:00:00.000Z") -> dict:
    return {"id": repo_id, "createdAt": cree}


# --- 1. « rien regardé » ≠ « rien trouvé » -----------------------------------


def test_echec_total_rend_1_pas_0(etat, notifications, monkeypatch):
    """Le cas qui rendrait la veille inutile en silence : l'API est injoignable
    et le script rend 0. Un cron vert pendant six mois sans avoir rien regardé."""
    monkeypatch.setattr(
        mod, "_get_json", _bouchon({"api/models?": urllib.error.URLError("réseau mort")})
    )
    monkeypatch.setattr(mod, "RETRY_DELAI_S", 0)
    code = mod.executer(check_seulement=False)

    assert code == 1, "une interrogation qui n'a RIEN pu regarder doit rendre 1"
    assert notifications, "l'échec total doit se dire, pas se taire"
    assert "MUETTE" in notifications[0][0]


def test_retry_recupere_apres_echec_transitoire(etat, notifications, monkeypatch):
    """Vécu le 2026-09-01 : Connection refused sur les 3 orgs (réseau pas prêt
    après réveil du Mac). Le retry doit rattraper un échec transitoire."""
    appels = {"n": 0}

    def faux(url: str):
        if "api/models?" not in url:
            raise AssertionError(url)
        appels["n"] += 1
        if appels["n"] <= len(mod.ORGS):
            raise urllib.error.URLError("[Errno 61] Connection refused")
        return []

    monkeypatch.setattr(mod, "_get_json", faux)
    monkeypatch.setattr(mod, "RETRY_DELAI_S", 0)
    code = mod.executer(check_seulement=False)

    assert code == 0, "le retry doit rattraper un échec transitoire"
    assert appels["n"] == 2 * len(mod.ORGS), "doit avoir interrogé deux fois"


def test_retry_ne_masque_pas_un_echec_persistant(etat, notifications, monkeypatch):
    """Si le réseau reste mort après le retry, le code reste 1."""
    monkeypatch.setattr(
        mod, "_get_json", _bouchon({"api/models?": urllib.error.URLError("réseau mort")})
    )
    monkeypatch.setattr(mod, "RETRY_DELAI_S", 0)
    code = mod.executer(check_seulement=False)

    assert code == 1


def test_rien_de_neuf_rend_0_et_rafraichit_letat(etat, notifications, monkeypatch):
    monkeypatch.setattr(mod, "_get_json", _bouchon({"api/models?": []}))
    assert mod.executer(check_seulement=False) == 0
    assert not notifications, "aucune trouvaille ⇒ aucune notification"

    ecrit = json.loads(etat.read_text(encoding="utf-8"))
    assert ecrit["dernier_succes"] is not None, (
        "sans horodatage de réussite, l'alerte « muette » ne peut jamais partir"
    )


def test_echec_partiel_ne_masque_pas_le_succes(etat, notifications, monkeypatch):
    """Une org sur trois injoignable : on a quand même regardé, donc code 0 —
    mais l'échec est consigné, jamais avalé."""
    appels = {"n": 0}

    def faux(url: str):
        if "api/models?" in url:
            appels["n"] += 1
            if appels["n"] == 1:
                raise urllib.error.URLError("org 1 injoignable")
            return []
        raise AssertionError(url)

    monkeypatch.setattr(mod, "_get_json", faux)
    assert mod.executer(check_seulement=False) == 0
    assert json.loads(etat.read_text(encoding="utf-8"))["derniere_erreur"]


# --- 2. le juge est config.json, pas le nom du dépôt -------------------------


def test_depot_sans_config_json_nest_PAS_annonce(etat, notifications, monkeypatch):
    """Cas réel du 2026-08-07 : `huginnfork/Qwen3.8-27B-FP8` porte exactement le
    nom attendu, n'a pas de config.json, 0 téléchargement. Notifier là-dessus
    apprendrait à ignorer la veille."""
    monkeypatch.setattr(
        mod,
        "_get_json",
        _bouchon(
            {
                "api/models?": [_depot("mlx-community/Qwen3.8-27B-8bit")],
                "api/models/": {"siblings": [{"rfilename": "README.md"}]},
                "config.json": urllib.error.HTTPError(
                    "u", 404, "Not Found", {}, None  # type: ignore[arg-type]
                ),
            }
        ),
    )
    assert mod.executer(check_seulement=False) == 0
    assert not notifications, "un dépôt sans config.json est un placeholder"

    vus = json.loads(etat.read_text(encoding="utf-8"))["vus"]
    assert "mlx-community/Qwen3.8-27B-8bit" in vus, (
        "le placeholder doit être mémorisé, sinon la veille le re-signale chaque jour"
    )


def test_depot_avec_config_json_EST_annonce(etat, notifications, monkeypatch):
    monkeypatch.setattr(
        mod,
        "_get_json",
        _bouchon(
            {
                "api/models?": [_depot("Qwen/Qwen3.8-27B")],
                "config.json": {
                    "architectures": ["Qwen3ForCausalLM"],
                    "hidden_size": 5120,
                    "num_hidden_layers": 64,
                    "max_position_embeddings": 262144,
                },
            }
        ),
    )
    assert mod.executer(check_seulement=False) == 0
    assert len(notifications) == 1
    assert "Qwen/Qwen3.8-27B" in notifications[0][1]


def test_un_depot_deja_vu_ne_renotifie_pas(etat, notifications, monkeypatch):
    etat.write_text(
        json.dumps({"vus": ["Qwen/Qwen3.8-27B"], "dernier_succes": 1.0, "derniere_erreur": None}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_get_json", _bouchon({"api/models?": [_depot("Qwen/Qwen3.8-27B")]}))
    assert mod.executer(check_seulement=False) == 0
    assert not notifications


@pytest.mark.parametrize(
    ("fichiers", "attendu"),
    [
        # Le cas qui a motivé ce diagnostic : trouvé en live le 2026-08-07 sur
        # `unsloth/Qwen3.6-35B-A3B-GGUF`, que la 1re version déclarait « dépôt
        # vide » alors qu'il porte des poids. Un GGUF n'a JAMAIS de config.json.
        (["Qwen3.8-27B-Q4_K_M.gguf", "README.md"], "GGUF"),
        ([{"rfilename": "README.md"}], "placeholder"),
        (["model-00001-of-00012.safetensors"], "incomplet"),
    ],
    ids=["gguf", "placeholder", "poids-sans-config"],
)
def test_le_diagnostic_nomme_la_BONNE_cause(monkeypatch, fichiers, attendu):
    """Un message qui accuse la mauvaise cause coûte une enquête entière — ce
    dépôt l'a déjà payé (« Ollama injoignable » pour une panne de gateway)."""
    siblings = [f if isinstance(f, dict) else {"rfilename": f} for f in fichiers]
    monkeypatch.setattr(mod, "_get_json", _bouchon({"api/models/": {"siblings": siblings}}))
    assert attendu in mod._diagnostic_sans_config("org/depot")


def test_diagnostic_ne_ment_pas_quand_il_ne_sait_pas(monkeypatch):
    """Liste de fichiers illisible ⇒ le dire, surtout pas conclure « vide »."""
    monkeypatch.setattr(
        mod, "_get_json", _bouchon({"api/models/": urllib.error.URLError("coupé")})
    )
    verdict = mod._diagnostic_sans_config("org/depot")
    assert "illisible" in verdict
    assert "placeholder" not in verdict


# --- 3. le silence se dénonce ------------------------------------------------


def test_le_seuil_de_mutisme_vaut_3_jours():
    """Le seuil est un RÉGLAGE, il se verrouille sur un littéral.

    Première version de ce fichier : le test de mutisme calculait son âge depuis
    `MUETTE_JOURS` lui-même. Passer la constante à 99999 — donc désarmer
    totalement l'alerte — laissait la suite VERTE. Un test qui dérive avec la
    valeur qu'il surveille ne peut pas rougir. Vérifié par mutation le
    2026-08-07 : c'est la seule des trois mutations que la suite n'a pas vue."""
    assert mod.MUETTE_JOURS == 3, (
        "changer le seuil est une décision — la refléter ici est le prix à payer"
    )


def test_silence_prolonge_declenche_lalerte_muette(etat, notifications, monkeypatch):
    """Sans ça, trois mois sans réponse de l'API se lisent « rien de neuf ».

    L'âge est un littéral (30 jours), PAS `MUETTE_JOURS + 1` : sinon le test
    suit la constante et ne peut plus dénoncer un seuil désarmé."""
    vieux = mod.time.time() - 30 * 86400
    etat.write_text(
        json.dumps({"vus": [], "dernier_succes": vieux, "derniere_erreur": "timeout"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        mod, "_get_json", _bouchon({"api/models?": urllib.error.URLError("toujours mort")})
    )
    monkeypatch.setattr(mod, "RETRY_DELAI_S", 0)
    assert mod.executer(check_seulement=False) == 1
    assert notifications and "MUETTE" in notifications[0][0]
    assert "30.0" in notifications[0][1], "le corps doit chiffrer l'âge du silence"


# --- garde-fous du motif et du périmètre -------------------------------------


@pytest.mark.parametrize(
    "repo_id",
    [
        "Qwen/Qwen3-8B",  # existe depuis 2025, AUCUN rapport avec Qwen3.8
        "Qwen/Qwen3.6-27B",
        "mlx-community/Qwen38-27B",
    ],
)
def test_le_motif_ne_ramasse_pas_les_voisins(repo_id):
    assert not mod.MOTIF.search(repo_id), f"{repo_id} ne doit pas matcher"


@pytest.mark.parametrize(
    "repo_id", ["Qwen/Qwen3.8-27B", "mlx-community/Qwen3.8-27B-8bit", "unsloth/Qwen3.8-MLX-4bit"]
)
def test_le_motif_attrape_ce_quon_attend(repo_id):
    assert mod.MOTIF.search(repo_id)


def test_get_json_refuse_une_url_hors_huggingface():
    with pytest.raises(ValueError, match="hors périmètre"):
        mod._get_json("file:///etc/passwd")


def test_check_n_ecrit_rien(etat, notifications, monkeypatch):
    monkeypatch.setattr(
        mod,
        "_get_json",
        _bouchon(
            {
                "api/models?": [_depot("Qwen/Qwen3.8-27B")],
                "config.json": {"architectures": ["Qwen3ForCausalLM"]},
            }
        ),
    )
    assert mod.executer(check_seulement=True) == 0
    assert not etat.exists(), "--check ne doit pas écrire l'état"
    assert not notifications, "--check ne doit pas notifier"
