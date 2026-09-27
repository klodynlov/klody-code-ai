"""Le `.env` de Klody est chargé AVANT que le premier module de `klody_mcp` ne lise
l'environnement.

Trouvé le 2026-09-27 en réalignant `song_structure` sur le plafond de segment de
local-suno : `vocalbrain_server` et `klody_music_server` importaient
`song_structure` PUIS appelaient `load_dotenv()`. Or `song_structure` lit ses
réglages À L'IMPORT (`KLODY_SONG_*`, `ACESTEP_*`, `ACE_STEP_VERSION`) : toute
valeur posée dans le `.env` était ignorée en silence, et le calcul de couverture
tournait sur les défauts. Même panne pour `_pathguard.AUDIO_ROOTS`
(`KLODY_MCP_AUDIO_ROOTS`), importé avant `load_dotenv()` par six serveurs : une
racine RESTREINTE dans le `.env` laissait le garde sur ses défauts, plus larges.

Ces tests reproduisent le circuit réel, pas une approximation : une COPIE du
paquet dans un dossier temporaire, un `.env` posé à sa racine — là où
`find_dotenv()` le trouve en production, en remontant depuis `klody_mcp/` — et
un import dans un processus neuf, puisque les réglages sont figés au premier
import. Rien n'est écrit dans le dépôt : son vrai `.env` n'est jamais touché, et
il ne peut pas non plus être lu par erreur (`_lancer` vérifie que c'est bien la
copie qui a été importée).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PAQUET = Path(__file__).resolve().parent.parent / "klody_mcp"

# Toutes les variables que ces tests posent. Retirées de l'environnement hérité :
# une valeur exportée dans le shell du développeur (ou par un plist) gagnerait
# sur le `.env` — c'est le contrat — et masquerait le défaut à mesurer.
_VARIABLES = (
    "KLODY_SONG_DUREE_MIN",
    "KLODY_SONG_DUREE_MAX",
    "KLODY_SONG_DEBIT_CIBLE",
    "KLODY_SONG_DEBIT_MIN",
    "KLODY_SONG_DEBIT_MAX",
    "ACE_STEP_VERSION",
    "ACESTEP_MAX_SEGMENT_SEC",
    "ACESTEP_SEGMENT_OVERLAP_SEC",
    "KLODY_MCP_AUDIO_ROOTS",
)

# Toutes ≠ des défauts de `song_structure` : « lu dans le .env » et « défaut »
# ne peuvent pas se confondre. `ACE_STEP_VERSION=v1` sans plafond explicite
# ramène le plafond de 600 s (défaut v1.5) à 120 s.
_ENV_CHANSON = {
    "KLODY_SONG_DUREE_MIN": "15",
    "KLODY_SONG_DUREE_MAX": "420",
    "KLODY_SONG_DEBIT_CIBLE": "2.5",
    "KLODY_SONG_DEBIT_MIN": "1.5",
    "KLODY_SONG_DEBIT_MAX": "3.5",
    "ACE_STEP_VERSION": "v1",
    "ACESTEP_SEGMENT_OVERLAP_SEC": "6",
}
_ATTENDU_CHANSON = {
    "DUREE_MIN_SEC": 15,
    "DUREE_MAX_SEC": 420,
    "DEBIT_CIBLE": 2.5,
    "DEBIT_MIN": 1.5,
    "DEBIT_MAX": 3.5,
    "SEGMENT_MAX_SEC": 120.0,
    "SEGMENT_OVERLAP_SEC": 6.0,
}

_SERVEURS_CHANSON = ("klody_mcp.vocalbrain_server", "klody_mcp.klody_music_server")


def _lancer(racine: Path, module: str, expression: str,
            dotenv: dict[str, str], exporte: dict[str, str] | None = None):
    """Importe `module` depuis la copie du paquet et rend `expression` évaluée.

    Le lanceur est un FICHIER, pas un `python -c` : `find_dotenv()` ne remonte
    depuis l'appelant que si `__main__` a un `__file__` — sinon il part du cwd.
    Le cwd est aussi la racine de la copie, pour que les deux chemins de
    `find_dotenv()` (appelant, ou cwd sous traceur de couverture) désignent le
    même `.env`.
    """
    (racine / ".env").write_text(
        "".join(f"{k}={v}\n" for k, v in dotenv.items()), encoding="utf-8"
    )
    lanceur = racine / "lanceur.py"
    lanceur.write_text(textwrap.dedent(f"""\
        import importlib, json, pathlib, sys
        mod = importlib.import_module({module!r})
        paquet = pathlib.Path(sys.modules["klody_mcp"].__file__).resolve()
        assert paquet.is_relative_to(pathlib.Path({str(racine)!r}).resolve()), (
            f"c'est le paquet du dépôt qui a été importé, pas la copie : {{paquet}}"
        )
        print(json.dumps({expression}))
    """), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in _VARIABLES}
    env.pop("PYTHONPATH", None)
    env.update(exporte or {})
    proc = subprocess.run(
        [sys.executable, str(lanceur)], cwd=racine, env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def racine(tmp_path_factory) -> Path:
    """Copie du paquet `klody_mcp` — le `.env` se pose à côté, comme en production."""
    racine = tmp_path_factory.mktemp("klody")
    shutil.copytree(PAQUET, racine / "klody_mcp",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return racine


_EXPR_CHANSON = f"{{k: getattr(mod.ss, k) for k in {sorted(_ATTENDU_CHANSON)!r}}}"


class TestLeDotenvPrecedeLesReglages:
    """Un réglage posé dans le `.env` atteint `song_structure`, par les deux serveurs."""

    @pytest.mark.parametrize("module", _SERVEURS_CHANSON)
    def test_les_reglages_de_chanson_viennent_du_dotenv(self, racine, module):
        assert _lancer(racine, module, _EXPR_CHANSON, _ENV_CHANSON) == _ATTENDU_CHANSON

    @pytest.mark.parametrize("module", _SERVEURS_CHANSON)
    def test_un_plafond_explicite_du_dotenv_est_lu(self, racine, module):
        # Le chemin « surcharge explicite » de `plafond_segment`, distinct de
        # celui qui déduit le plafond de la version du moteur.
        assert _lancer(racine, module, "mod.ss.SEGMENT_MAX_SEC",
                       {"ACESTEP_MAX_SEGMENT_SEC": "90"}) == 90.0

    def test_les_bornes_recopiees_par_vocalbrain_suivent(self, racine):
        # `vocalbrain_server` recopie les bornes à l'import (`_DUREE_MIN/_MAX`) :
        # c'est ce que ses outils valident, pas `ss.*` directement.
        assert _lancer(racine, "klody_mcp.vocalbrain_server",
                       "[mod._DUREE_MIN, mod._DUREE_MAX]", _ENV_CHANSON) == [15, 420]

    def test_une_variable_exportee_gagne_sur_le_dotenv(self, racine):
        # Contrat de `load_dotenv()` (override=False) : le plist launchd et les
        # scripts `start-*-mcp.sh` exportent ; le `.env` ne complète que ce qui
        # manque. Un `override=True` inverserait ça sans que rien ne rougisse.
        assert _lancer(racine, "klody_mcp.vocalbrain_server", "mod.ss.SEGMENT_MAX_SEC",
                       {"ACESTEP_MAX_SEGMENT_SEC": "90"},
                       exporte={"ACESTEP_MAX_SEGMENT_SEC": "100"}) == 100.0


class TestLeDotenvPrecedeLeGardeDeChemins:
    """`KLODY_MCP_AUDIO_ROOTS` du `.env` atteint `_pathguard` — un réglage de SÉCURITÉ."""

    @pytest.mark.parametrize("module", [
        "klody_mcp.klody_music_server",
        "klody_mcp.reaper_server",
        "klody_mcp.vlc_server",
    ])
    def test_les_racines_audio_viennent_du_dotenv(self, racine, module):
        a, b = racine / "audio_a", racine / "audio_b"
        a.mkdir(exist_ok=True)
        b.mkdir(exist_ok=True)
        racines = _lancer(
            racine, module,
            "[str(p) for p in sys.modules['klody_mcp._pathguard'].AUDIO_ROOTS]",
            {"KLODY_MCP_AUDIO_ROOTS": os.pathsep.join([str(a), str(b)])},
        )
        # Restreindre les racines dans le `.env` doit RESTREINDRE : les défauts
        # (~/Documents, ~/Projets, /tmp…) sont bien plus larges.
        assert racines == [str(a.resolve()), str(b.resolve())]
