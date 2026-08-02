#!/usr/bin/env python3
"""Pourquoi n'y a-t-il aucun son ? — diagnostic de la voix Klody.

Écrit après un « aucun son » sans le moindre message à l'écran ni dans le log.
Cause racine du silence : `tools.voice.speak` ne lève JAMAIS d'exception, il
RETOURNE un compte rendu — y compris pour ses échecs (« CLI VocalBrain
introuvable », « modèle TTS absent », « lecture impossible »). L'appelant jetait
cette valeur, donc aucun des quatre modes d'échec n'était observable.

Ce script rend chaque maillon vérifiable SÉPARÉMENT, parce qu'ils tombent
différemment et se réparent différemment :

    1. VOICE_CLI          le binaire vocalbrain existe-t-il, est-il exécutable ?
    2. le lecteur audio   afplay est-il là ? (macOS uniquement)
    3. le dossier audio   VOICE_AUDIO_DIR est-il accessible en écriture ?
    4. le clonage         le timbre est-il FIGÉ ? (juge : le --dry-run de la CLI)
    5. la synthèse        un vrai appel speak(), compte rendu AFFICHÉ

Un test qui n'appellerait pas réellement `speak` ne prouverait rien : le mode
d'échec le plus fréquent (modèle TTS à moitié téléchargé) n'apparaît qu'à
l'exécution.

Usage :
    python scripts/diagnostic_voix.py
    python scripts/diagnostic_voix.py --texte "Bonjour, ceci est un test."
    python scripts/diagnostic_voix.py --sans-synthese   # contrôles seuls, muet

Codes de sortie :
    0 = la voix fonctionne (son émis)
    1 = un maillon est cassé — la sortie dit lequel et quoi faire
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

# `speak` marque ses succès par cet emoji en tête ; tout le reste est un échec
# rendu sous forme de texte. C'est le SEUL discriminant disponible côté appelant.
_MARQUEUR_SUCCES = "🔊"


def _ok(texte: str) -> None:
    print(f"  ✓ {texte}")


def _ko(texte: str, remede: str = "") -> None:
    print(f"  ✗ {texte}")
    if remede:
        print(f"     → {remede}")


def controler_cli() -> bool:
    chemin = Path(config.VOICE_CLI)
    if not chemin.exists():
        _ko(
            f"CLI VocalBrain absente : {chemin}",
            "installer vocalbrain dans le venv local-suno, ou pointer VOICE_CLI "
            "ailleurs dans .env",
        )
        return False
    if not os.access(chemin, os.X_OK):
        _ko(f"CLI présente mais non exécutable : {chemin}", f"chmod +x {chemin}")
        return False
    _ok(f"CLI VocalBrain : {chemin}")
    return True


def controler_lecteur() -> bool:
    lecteur = config.VOICE_PLAY_CMD
    trouve = shutil.which(lecteur)
    if not trouve:
        _ko(
            f"lecteur audio introuvable dans le PATH : {lecteur}",
            "sur macOS afplay est natif ; hors macOS, régler VOICE_PLAY_CMD "
            "(ex. aplay, ffplay)",
        )
        return False
    _ok(f"lecteur audio : {trouve}")
    return True


def controler_dossier_audio() -> bool:
    dossier = Path(config.VOICE_AUDIO_DIR)
    if not dossier.exists():
        # Pas bloquant : VocalBrain le crée à la première synthèse. On le signale
        # quand même, parce qu'un dossier absent ET une synthèse muette pointent
        # vers un projet VocalBrain jamais initialisé.
        print(f"  · dossier audio pas encore créé : {dossier} (normal au 1ᵉʳ usage)")
        return True
    if not os.access(dossier, os.W_OK):
        _ko(f"dossier audio non inscriptible : {dossier}", f"chmod u+w {dossier}")
        return False
    _ok(f"dossier audio : {dossier}")
    return True


def _lancer(args_cli: list[str], timeout: float = 20.0) -> str:
    """Lance la CLI VocalBrain et rend sa sortie. Jamais d'exception."""
    try:
        proc = subprocess.run(
            [config.VOICE_CLI, *args_cli],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"__ECHEC__ {type(exc).__name__}: {exc}"
    return f"{proc.stdout}\n{proc.stderr}"


def controler_clonage() -> bool:
    """Le timbre est-il FIGÉ ? C'est ce qui décide de « différentes voix ».

    Le juge est la ligne `clonage :` du `--dry-run`, rendue par la CLI — pas
    notre configuration, qui ne prouverait que nos propres intentions. Un dry-run
    ne synthétise rien : le contrôle est quasi gratuit.

    ⚠️ Ce contrôle existait d'abord pour attraper une panne SILENCIEUSE :
    `--voice <valeur-inconnue>` était accepté sans erreur puis ignoré, donc une
    `VOICE_PRESET` mal orthographiée ne produisait ni message ni changement. Le
    mécanisme est passé à `--preset`, qui refuse net un preset inconnu — le mode
    muet est mort, mais le juge reste la CLI : c'est ce qui permet de le vérifier
    au lieu de le supposer.
    """
    sortie = _lancer([
        "generate",
        "-p", config.VOICE_PROJECT_ID,
        "-c", config.VOICE_CHARACTER,
        "-t", "Test.",
        "--lang", "french",
        "--dry-run",
        *(["--preset", config.VOICE_PRESET] if config.VOICE_PRESET else []),
    ])
    if sortie.startswith("__ECHEC__"):
        _ko(f"dry-run impossible : {sortie[10:]}")
        return False

    preset = config.VOICE_PRESET or "(aucun)"

    # À lire AVANT la ligne `clonage :`, qui manquera : la CLI sort en erreur sur
    # un preset inconnu, et « pas de ligne clonage » se lirait alors comme « CLI
    # d'une autre version », donc comme un non-problème.
    if "preset de voix introuvable" in sortie.lower():
        _ko(
            f"preset « {preset} » inconnu de VocalBrain — la CLI refuse la synthèse",
            "corrige VOICE_PRESET (la sortie ci-dessous liste les presets connus), "
            "ou vide-la pour renoncer au clonage",
        )
        for ligne in sortie.splitlines():
            if "presets disponibles" in ligne.lower():
                print(f"     {ligne.strip()}")
        return False

    ligne = next(
        (ligne.strip() for ligne in sortie.splitlines()
         if ligne.strip().lower().startswith("clonage")),
        "",
    )
    if not ligne:
        print("  ? clonage : la CLI ne le rapporte pas — version différente ?")
        return True  # inconnu ≠ cassé : on ne bloque pas sur une sortie inattendue

    # La CLI rend la SOURCE du timbre (« klody_e250 (preset « klody ») ») ou le
    # mot « non ». Juger sur l'absence de « non » plutôt que sur la présence d'un
    # « oui » qu'elle n'écrit jamais : sinon le contrôle est vert de naissance et
    # rouge à jamais, sans rapport avec l'état réel.
    valeur = ligne.split(":", 1)[1].strip()
    clone = bool(valeur) and valeur.split()[0].lower() not in ("non", "no", "none", "aucun")
    if clone:
        _ok(f"clonage actif — timbre figé par {valeur} (VOICE_PRESET={preset})")
        return True

    _ko(
        f"clonage : NON (VOICE_PRESET={preset}) — le timbre n'est PAS figé",
        "le modèle est un Qwen3-TTS 0.6B *Base*, sans conditionnement de "
        "locuteur : il échantillonne une voix par segment, donc un texte de "
        "plusieurs phrases sort avec PLUSIEURS VOIX.",
    )
    if config.VOICE_PRESET:
        print("     → et VOICE_PRESET est pourtant renseignée, ET acceptée : le")
        print("       preset existe mais n'impose aucun timbre — vérifie sa fiche.")
    print("     → remède : créer une voix clonée pour le personnage côté")
    print("       VocalBrain, puis la nommer dans VOICE_PRESET (cf. sous-commandes)")
    return False


def lister_sous_commandes() -> None:
    """Affiche les sous-commandes de la CLI — pour trouver celle du clonage.

    Purement informatif : la CLI VocalBrain vit hors de ce dépôt et sa surface
    varie d'une installation à l'autre. Plutôt que de deviner le nom de la
    commande d'entraînement de voix, on la LIT.
    """
    sortie = _lancer(["--help"], timeout=10.0)
    if sortie.startswith("__ECHEC__"):
        return
    lignes = [
        brut.rstrip() for brut in sortie.splitlines()
        if brut.strip().startswith("│") and not brut.strip().startswith("│ -")
    ]
    if not lignes:
        return
    print("\n  sous-commandes VocalBrain disponibles :")
    for ligne in lignes[:20]:
        print(f"    {ligne}")


def controler_synthese(texte: str) -> bool:
    """Appelle réellement `speak` et AFFICHE son compte rendu — le point du script."""
    from tools.voice import speak

    print("\n  … synthèse en cours (quelques secondes, ~6 s à froid)")
    rapport = speak(texte, "fr")
    print(f"\n  compte rendu de speak() :\n    {rapport}\n")

    if not rapport.startswith(_MARQUEUR_SUCCES):
        _ko("la synthèse a échoué — le compte rendu ci-dessus dit pourquoi")
        return False
    if "lecture impossible" in rapport:
        _ko(
            "le WAV a bien été généré, mais le lecteur n'a pas démarré",
            "vérifier VOICE_PLAY_CMD et la sortie audio active du Mac",
        )
        return False
    _ok("synthèse ET lecture parties — tu devrais avoir entendu quelque chose")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--texte", default="Bonjour, ceci est un test de la voix de Klody.",
    )
    parser.add_argument(
        "--sans-synthese", action="store_true",
        help="Contrôles de configuration seuls, sans appeler speak()",
    )
    args = parser.parse_args()

    print("\nDiagnostic de la voix Klody")
    print(f"  projet VocalBrain : {config.VOICE_PROJECT_ID}")
    print(f"  personnage        : {config.VOICE_CHARACTER}")
    print(f"  VOICE_PRESET      : {config.VOICE_PRESET or '(aucune)'}")
    print(f"  GREETING_VOICE    : {config.GREETING_VOICE}")
    print(f"  VOICE_REPLIES     : {config.VOICE_REPLIES}\n")

    controles = [controler_cli(), controler_lecteur(), controler_dossier_audio()]
    if not all(controles):
        print("\n✗ Un maillon de configuration est cassé — corrige-le avant la synthèse.\n")
        return 1

    # Le timbre est un contrôle À PART : un clonage absent ne casse pas la voix,
    # il la rend seulement instable. On le rapporte sans faire échouer le
    # diagnostic — sinon « la voix marche mais change » deviendrait
    # indiscernable de « la voix ne marche pas ».
    timbre_fige = controler_clonage()
    if not timbre_fige:
        lister_sous_commandes()

    if args.sans_synthese:
        print("\n✓ Configuration correcte. Synthèse non testée (--sans-synthese).\n")
        return 0

    if not controler_synthese(args.texte):
        return 1

    if not config.GREETING_VOICE:
        print("  ⚠️  La voix marche, mais GREETING_VOICE est à false : la CLI")
        print("     restera muette. Lance-la avec GREETING_VOICE=true, ou /voix.\n")
    if not timbre_fige:
        print("  ⚠️  La voix marche, mais son TIMBRE n'est pas figé : un texte de")
        print("     plusieurs phrases sortira avec plusieurs voix. Cf. ci-dessus.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
