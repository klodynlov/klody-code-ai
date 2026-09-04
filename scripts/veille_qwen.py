#!/usr/bin/env python3
"""Veille sur la publication des poids Qwen3.8 — piloté par com.klody.veille-qwen (24 h).

Raison d'être : le 2026-08-03 Alibaba annonce Qwen3.8 — `Qwen3.8-Max` (MoE
2,4 T params totaux / 95 B actifs, contexte 1 M, multimodal) et un `Qwen3.8-27B`
dense présenté comme l'option on-premise. Au 2026-08-07, l'org officielle `Qwen/`
sur HuggingFace n'a RIEN publié : son dépôt le plus récent reste
`Qwen/Qwen3.6-27B` (2026-04-21).

Le Max est hors de cette machine par construction — règle RAM-MoE du projet : on
compte les params TOTAUX, pas actifs, soit ~1 200 Go en 4bit contre 80 Go de
budget gateway. Seul le 27B dense est intégrable. C'est lui qu'on attend.

⚠️ POURQUOI CE SCRIPT NE CROIT PAS LES NOMS DE DÉPÔTS. Les 11 dépôts nommés
« Qwen3.8 » présents sur HF au 2026-08-07 sont tous des tiers, et vérification
faite aucun ne contient les poids :

  - `Ma7ee7/Qwen3.8_4B_Distilled` a été créé le 2026-07-30, soit quatre jours
    AVANT l'annonce ; son `config.json` rend `Qwen3ForCausalLM` avec
    `hidden_size=2560` — c'est une arch Qwen3-4B, mal étiquetée ;
  - `huginnfork/Qwen3.8-27B-FP8`, `neroued/Qwen3.8-27B-*` : pas de `config.json`
    du tout (404), 0 téléchargement. Dépôts vides.

D'où les deux règles :
  1. les orgs surveillées sont fixées (`ORGS`), jamais une recherche globale —
     un tiers peut nommer son dépôt comme il veut ;
  2. un dépôt qui matche n'est annoncé qu'après lecture de son `config.json`.
     Un nom de dépôt est une sonde qui ment ; `config.json` est le juge. Un
     dépôt sans config sort en « NON CONFIRMÉ », pas en trouvaille.

`unsloth` et `mlx-community` sont dans la liste au même titre que `Qwen` : ce
sont eux qui produisent les conversions MLX réellement chargeables ici (le brain
et le coder du registre gateway viennent d'`unsloth`), et elles arrivent APRÈS
l'officiel.

⚠️ CE QUE CE SCRIPT NE PEUT PAS VOIR : que launchd a cessé de le lancer. Il ne
parle que quand il tourne. Ce trou-là est couvert par
`scripts/install-launchagents.sh --check`, qui rend `NON CHARGÉ`.

Ce qu'il couvre, lui, c'est l'autre moitié : « il tourne mais l'interrogation
échoue ». Au-delà de `MUETTE_JOURS` sans une seule interrogation RÉUSSIE, il le
NOTIFIE au lieu de se taire. Sans ça, un silence de trois mois se lirait « rien
de neuf » alors qu'il voudrait dire « je n'ai rien pu regarder » — c'est le mode
de défaillance dominant de ce dépôt, un garde-fou incapable de rougir.

Usage :
    veille_qwen.py                     interroge, notifie, écrit l'état
    veille_qwen.py --check             affiche l'état, n'écrit rien, ne notifie pas
    veille_qwen.py --tester-notification  vérifie que le canal d'alerte marche
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

HF_API = "https://huggingface.co/api/models"

# Orgs surveillées. `Qwen` est la source de vérité ; les deux autres publient les
# conversions MLX qui rendent le modèle chargeable sur cette machine.
ORGS = ("Qwen", "mlx-community", "unsloth")

# Ce qu'on cherche. Le point est échappé : `Qwen38` ou `Qwen3-8B` ne doivent PAS
# matcher — `Qwen3-8B` est un modèle qui existe depuis 2025 et n'a aucun rapport.
MOTIF = re.compile(r"qwen3\.8", re.IGNORECASE)

TIMEOUT_S = 20
LIMITE_PAR_ORG = 200

# Au-delà, le silence est suspect et devient lui-même une alerte.
MUETTE_JOURS = 3

# Quand TOUTES les orgs échouent (vraisemblablement réseau local, pas HF), on
# retente une fois après ce délai. Couvre le cas « Mac vient de se réveiller,
# réseau pas encore prêt » — vécu les 2026-09-01 et 09-02 (Errno 61 sur les
# trois orgs, ok quelques heures plus tard).
RETRY_DELAI_S = 60
RETRY_MAX = 1

ETAT_DIR = Path.home() / "Library" / "Caches" / "klody"
ETAT_FICHIER = ETAT_DIR / "veille-qwen.json"


def log(message: str) -> None:
    """Sortie standard — le plist la redirige vers ~/Library/Logs."""
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} veille-qwen: {message}", flush=True)


def notifier(titre: str, corps: str) -> bool:
    """Notification macOS. Passe par `on run argv` : aucun échappement à faire,
    donc aucun nom de dépôt ne peut casser (ou détourner) le script AppleScript."""
    script = (
        "on run argv\n"
        "display notification (item 1 of argv) with title (item 2 of argv)\n"
        "end run"
    )
    try:
        subprocess.run(
            ["osascript", "-e", script, corps, titre],
            check=True,
            capture_output=True,
            timeout=15,
        )
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        log(f"ÉCHEC de la notification ({exc}) — la trouvaille reste dans ce journal")
        return False


HOTE = "https://huggingface.co/"


def _get_json(url: str) -> Any:
    # Les URL sont bâties à partir de constantes et d'identifiants de dépôts
    # rendus par l'API. L'invariant est donc vrai par construction — on le pose
    # quand même : c'est ce qui empêche un futur paramètre d'ouvrir un `file://`.
    if not url.startswith(HOTE):
        raise ValueError(f"URL hors périmètre : {url}")
    requete = urllib.request.Request(url, headers={"User-Agent": "klody-veille-qwen"})
    # nosec B310 — B310 vise l'ouverture d'un schéma arbitraire (`file:`, custom).
    # Le `startswith(HOTE)` juste au-dessus le rend impossible, et
    # `test_get_json_refuse_une_url_hors_huggingface` verrouille ce refus : la
    # marque ne masque donc pas le risque, elle constate qu'il est déjà fermé.
    with urllib.request.urlopen(requete, timeout=TIMEOUT_S) as reponse:  # nosec B310
        return json.load(reponse)


def depots_de_lorg(org: str) -> list[dict[str, Any]]:
    """Dépôts de `org` dont l'id matche MOTIF. Lève en cas d'échec réseau —
    l'appelant DOIT distinguer « rien trouvé » de « rien regardé »."""
    params = urllib.parse.urlencode(
        {"author": org, "limit": LIMITE_PAR_ORG, "sort": "createdAt", "direction": -1}
    )
    depots = _get_json(f"{HF_API}?{params}")
    return [d for d in depots if MOTIF.search(d.get("id", ""))]


def _diagnostic_sans_config(repo_id: str) -> str:
    """Pourquoi ce dépôt n'a pas de `config.json` — VIDE n'est pas la seule
    réponse, et l'écrire quand même serait un message qui nomme la mauvaise
    cause. Vécu à la mise au point le 2026-08-07 : `unsloth/…-GGUF` sortait en
    « dépôt vide » alors qu'il porte des poids ; un GGUF n'a simplement jamais de
    config.json. Ce dépôt a déjà payé une enquête entière pour un message qui
    accusait la mauvaise dépendance."""
    try:
        infos = _get_json(f"{HOTE}api/models/{repo_id}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return f"pas de config.json, et liste des fichiers illisible ({exc})"

    fichiers = [s.get("rfilename", "") for s in (infos or {}).get("siblings", [])]
    if any(f.endswith(".gguf") for f in fichiers):
        return "GGUF (pas de config.json par nature ; format llama.cpp, pas chargeable par mlx_lm)"
    if not any(f.endswith((".safetensors", ".bin", ".npz")) for f in fichiers):
        return f"aucun poids dans le dépôt ({len(fichiers)} fichier(s)) — placeholder"
    return "poids présents mais pas de config.json — dépôt incomplet"


def confirmer(repo_id: str) -> tuple[bool, str]:
    """Lit le `config.json` du dépôt. C'est LUI le juge, pas le nom.

    Rend (confirmé, description). Un dépôt sans config.json n'est pas chargeable
    par `mlx_lm` : c'est le cas de `huginnfork/Qwen3.8-27B-FP8` (404, 0
    téléchargement), mais aussi, pour une raison toute différente, de tout dépôt
    GGUF — d'où le diagnostic séparé."""
    url = f"https://huggingface.co/{repo_id}/raw/main/config.json"
    try:
        cfg = _get_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False, _diagnostic_sans_config(repo_id)
        return False, f"config.json illisible (HTTP {exc.code})"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return False, f"config.json illisible ({exc})"

    if not isinstance(cfg, dict):
        return False, "config.json inattendu (pas un objet)"

    arch = ", ".join(cfg.get("architectures") or []) or "?"
    morceaux = [f"arch={arch}"]
    for cle, etiquette in (
        ("hidden_size", "hidden"),
        ("num_hidden_layers", "couches"),
        ("num_experts", "experts"),
        ("num_local_experts", "experts"),
        ("max_position_embeddings", "ctx"),
    ):
        if cle in cfg:
            morceaux.append(f"{etiquette}={cfg[cle]}")
    return True, " ".join(morceaux)


def charger_etat() -> dict[str, Any]:
    try:
        etat = json.loads(ETAT_FICHIER.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"vus": [], "dernier_succes": None, "derniere_erreur": None}
    if not isinstance(etat, dict):
        return {"vus": [], "dernier_succes": None, "derniere_erreur": None}
    etat.setdefault("vus", [])
    etat.setdefault("dernier_succes", None)
    etat.setdefault("derniere_erreur", None)
    return etat


def ecrire_etat(etat: dict[str, Any]) -> None:
    ETAT_DIR.mkdir(parents=True, exist_ok=True)
    ETAT_FICHIER.write_text(json.dumps(etat, indent=2, ensure_ascii=False), encoding="utf-8")


def age_en_jours(horodatage: float | None) -> float | None:
    return None if horodatage is None else (time.time() - horodatage) / 86400


def alerter_si_muette(etat: dict[str, Any]) -> None:
    """Un silence prolongé doit se dire. Sinon il se lit « rien de neuf »."""
    age = age_en_jours(etat.get("dernier_succes"))
    if age is None:
        log("aucune interrogation réussie depuis l'installation")
        notifier(
            "Veille Qwen3.8 MUETTE",
            "Aucune interrogation HuggingFace n'a jamais abouti. "
            "Voir ~/Library/Logs/klody-veille-qwen.log",
        )
        return
    if age > MUETTE_JOURS:
        log(f"MUETTE : dernière interrogation réussie il y a {age:.1f} j (> {MUETTE_JOURS})")
        notifier(
            "Veille Qwen3.8 MUETTE",
            f"Rien regardé depuis {age:.1f} jours — ce silence n'est pas « rien de neuf ». "
            f"Dernière erreur : {etat.get('derniere_erreur')}",
        )


def _interroger_orgs() -> tuple[list[dict[str, Any]], list[str]]:
    """Interroge chaque org et rend (trouvés, échecs)."""
    trouves: list[dict[str, Any]] = []
    echecs: list[str] = []
    for org in ORGS:
        try:
            trouves.extend(depots_de_lorg(org))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            echecs.append(f"{org}: {exc}")
    return trouves, echecs


def executer(check_seulement: bool) -> int:
    etat = charger_etat()
    vus: set[str] = set(etat["vus"])

    trouves, echecs = _interroger_orgs()

    if echecs and len(echecs) == len(ORGS):
        for tentative in range(1, RETRY_MAX + 1):
            log(f"échec total — tentative {tentative}/{RETRY_MAX} dans {RETRY_DELAI_S} s")
            time.sleep(RETRY_DELAI_S)
            trouves, echecs = _interroger_orgs()
            if len(echecs) < len(ORGS):
                break

    if echecs and len(echecs) == len(ORGS):
        message = " | ".join(echecs)
        log(f"ÉCHEC total de l'interrogation — {message}")
        if not check_seulement:
            etat["derniere_erreur"] = message
            ecrire_etat(etat)
            alerter_si_muette(etat)
        return 1

    for echec in echecs:
        log(f"org injoignable (partiel) — {echec}")

    nouveaux = [d for d in trouves if d["id"] not in vus]

    if not check_seulement:
        etat["dernier_succes"] = time.time()
        etat["derniere_erreur"] = " | ".join(echecs) if echecs else None

    if not nouveaux:
        age = age_en_jours(etat.get("dernier_succes"))
        log(
            f"{len(trouves)} dépôt(s) Qwen3.8 connu(s) sur {ORGS}, aucun nouveau"
            + (f" (état frais, {age:.2f} j)" if age is not None else "")
        )
        if not check_seulement:
            ecrire_etat(etat)
        return 0

    lignes: list[str] = []
    confirmes: list[str] = []
    for depot in sorted(nouveaux, key=lambda d: d["id"]):
        repo_id = depot["id"]
        ok, detail = confirmer(repo_id)
        marque = "CONFIRMÉ" if ok else "NON CONFIRMÉ"
        cree = (depot.get("createdAt") or "")[:10]
        lignes.append(f"  [{marque}] {repo_id} (créé {cree}) — {detail}")
        if ok:
            confirmes.append(repo_id)

    log(f"{len(nouveaux)} nouveau(x) dépôt(s) Qwen3.8 :")
    for ligne in lignes:
        log(ligne)

    if check_seulement:
        log("--check : état non écrit, aucune notification envoyée")
        return 0

    if confirmes:
        notifier(
            "Qwen3.8 : poids publiés",
            f"{len(confirmes)} dépôt(s) confirmé(s) par config.json : "
            + ", ".join(confirmes[:3])
            + ("…" if len(confirmes) > 3 else ""),
        )
    else:
        log("aucun dépôt confirmé — que des placeholders, pas de notification")

    # On mémorise TOUS les nouveaux, confirmés ou non : sans ça un placeholder
    # ferait re-parler la veille à chaque tick, et une veille qui crie tous les
    # jours cesse d'être lue.
    etat["vus"] = sorted(vus | {d["id"] for d in nouveaux})
    ecrire_etat(etat)
    return 0


def main(argv: list[str]) -> int:
    if "--tester-notification" in argv:
        ok = notifier("Veille Qwen3.8", "Canal d'alerte vérifié — ceci est un test.")
        log(f"test de notification : {'OK' if ok else 'ÉCHEC'}")
        return 0 if ok else 1

    check = "--check" in argv
    if check:
        etat = charger_etat()
        age = age_en_jours(etat.get("dernier_succes"))
        log(f"état : {ETAT_FICHIER}")
        log(f"  dépôts déjà vus       : {len(etat['vus'])}")
        log(
            "  dernière réussite     : "
            + ("jamais" if age is None else f"il y a {age:.2f} j")
        )
        log(f"  dernière erreur       : {etat.get('derniere_erreur')}")
    return executer(check_seulement=check)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
