#!/usr/bin/env python3
"""
Installe l'intégration Siri ↔ Klody.
Génère "Klody.shortcut" sur le Bureau puis l'ouvre dans Shortcuts.app.

Le raccourci utilise un POST HTTP direct vers l'API Klody (pas de shell script).

Usage : python3 scripts/install_siri_shortcut.py
"""

import plistlib
import subprocess
import sys
import uuid
from pathlib import Path

DESKTOP = Path.home() / "Desktop"
SHORTCUT_NAME = "Klody"
SHORTCUT_PATH = DESKTOP / f"{SHORTCUT_NAME}.shortcut"
API_URL = "http://localhost:8000/api/siri"


def build_shortcut() -> bytes:
    """Raccourci Siri à deux actions :
    1. POST http://localhost:8000/api/siri  {"query": <entrée Siri>}
    2. Extraire la clé "response" du JSON retourné
    Siri lit l'output du raccourci automatiquement.
    """
    uuid1 = str(uuid.uuid4()).upper()
    uuid2 = str(uuid.uuid4()).upper()

    data = {
        "WFWorkflowClientVersion": "1300.230.0.1.3",
        "WFWorkflowHasShortcutInputVariables": False,
        "WFWorkflowIcon": {
            "WFWorkflowIconGlyphNumber": 59511,
            "WFWorkflowIconStartColor": 2071128575,
        },
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": ["WFStringContentItem"],
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowOutputContentItemClasses": ["WFStringContentItem"],
        "WFWorkflowTypes": ["WFSiriType"],
        "WFWorkflowActions": [
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                "WFWorkflowActionParameters": {
                    "UUID": uuid1,
                    "WFHTTPMethod": "POST",
                    "WFURL": API_URL,
                    "WFHTTPBodyType": "JSON",
                    "WFJSONValues": {
                        "Value": {
                            "WFDictionaryFieldValueItems": [
                                {
                                    "WFItemType": 0,
                                    "WFKey": {
                                        "Value": {"string": "query"},
                                        "WFSerializationType": "WFTextTokenString",
                                    },
                                    "WFValue": {
                                        "Value": {
                                            "attachmentsByRange": {
                                                "{0, 1}": {"Type": "ExtensionInput"}
                                            },
                                            "string": "￼",
                                        },
                                        "WFSerializationType": "WFTextTokenString",
                                    },
                                }
                            ]
                        },
                        "WFSerializationType": "WFDictionaryFieldValue",
                    },
                },
            },
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
                "WFWorkflowActionParameters": {
                    "UUID": uuid2,
                    "WFDictionaryKey": "response",
                },
            },
        ],
    }
    return plistlib.dumps(data, fmt=plistlib.FMT_XML)


def main() -> None:
    print("\n◆ Installation Siri ↔ Klody\n")

    try:
        payload = build_shortcut()
        SHORTCUT_PATH.write_bytes(payload)
        print(f"  ✓ Shortcut généré : {SHORTCUT_PATH}")
    except Exception as e:
        print(f"  ✗ Génération échouée : {e}")
        sys.exit(1)

    try:
        subprocess.run(
            ["shortcuts", "sign", "--mode", "people-who-know-me",
             "--input", str(SHORTCUT_PATH), "--output", str(SHORTCUT_PATH)],
            capture_output=True, check=True,
        )
        print("  ✓ Signé")
    except Exception as e:
        print(f"  ⚠ Signature échouée ({e}) — import quand même")

    print("  → Ouverture dans Shortcuts.app…")
    try:
        subprocess.run(["open", "-a", "Shortcuts", str(SHORTCUT_PATH)], check=True)
    except subprocess.CalledProcessError:
        print(f"\n  Double-cliquez sur : {SHORTCUT_PATH}")

    print(f"""
  ✓ Raccourci "{SHORTCUT_NAME}" prêt.

  Utilisation :
    "Dis Siri, Klody [votre question]"
    "Hey Siri, Klody quelle est la différence entre async et threading"

  Le serveur Klody doit être démarré :
    uvicorn api.server:app --host 127.0.0.1 --port 8000
""")


if __name__ == "__main__":
    main()
