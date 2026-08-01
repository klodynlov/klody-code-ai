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
    4. la synthèse        un vrai appel speak(), compte rendu AFFICHÉ

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
    print(f"  GREETING_VOICE    : {config.GREETING_VOICE}")
    print(f"  VOICE_REPLIES     : {config.VOICE_REPLIES}\n")

    controles = [controler_cli(), controler_lecteur(), controler_dossier_audio()]
    if not all(controles):
        print("\n✗ Un maillon de configuration est cassé — corrige-le avant la synthèse.\n")
        return 1

    if args.sans_synthese:
        print("\n✓ Configuration correcte. Synthèse non testée (--sans-synthese).\n")
        return 0

    if not controler_synthese(args.texte):
        return 1

    if not config.GREETING_VOICE:
        print("  ⚠️  La voix marche, mais GREETING_VOICE est à false : la CLI")
        print("     restera muette. Lance-la avec GREETING_VOICE=true, ou /voix.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
