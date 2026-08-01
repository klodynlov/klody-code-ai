"""Le diagnostic doit distinguer « pas de son » de « son instable ».

Deux pannes différentes, deux remèdes différents, et l'une est SILENCIEUSE :
`vocalbrain generate --voice <valeur-inconnue>` est accepté sans erreur puis
ignoré. Une `VOICE_PRESET` mal orthographiée ne produit donc ni message ni
changement — la panne muette est la plus coûteuse de ce dépôt.

Le juge est la ligne `clonage :` rendue par la CLI, pas notre configuration :
celle-ci ne prouverait que nos intentions.
"""
from __future__ import annotations

import subprocess

import config
import pytest
from scripts import diagnostic_voix as diag

_DRY_RUN = """Modèle sélectionné : mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16 (score=1.172)
dry-run — 1 prise(s) simulée(s) pour 'Klody'
  texte    : 'Test.'
  langue   : french
  clonage  : {clonage}
"""

_AIDE = """╭─ Commands ────────────────────────╮
│ generate   Génère N prises audio  │
│ clone      Entraîne une voix      │
╰───────────────────────────────────╯
"""


@pytest.fixture
def cli(monkeypatch):
    """Bouchonne la CLI VocalBrain et enregistre les commandes lancées."""
    lancees: list[list[str]] = []

    def _poser(clonage: str = "non", aide: str = _AIDE, casse: bool = False):
        def _run(cmd, **_kwargs):
            lancees.append(cmd)
            if casse:
                raise FileNotFoundError(cmd[0])

            class _Proc:
                stderr = ""
                stdout = _AIDE if "--help" in cmd else _DRY_RUN.format(clonage=clonage)

            _Proc.stdout = aide if "--help" in cmd else _DRY_RUN.format(clonage=clonage)
            return _Proc()

        monkeypatch.setattr(subprocess, "run", _run)
        return lancees

    monkeypatch.setattr(config, "VOICE_CLI", "/faux/vocalbrain")
    monkeypatch.setattr(config, "VOICE_PROJECT_ID", "proj")
    monkeypatch.setattr(config, "VOICE_CHARACTER", "Klody")
    monkeypatch.setattr(config, "VOICE_PRESET", "")
    return _poser


class TestControlerClonage:
    def test_clonage_non_est_signale_comme_timbre_non_fige(self, cli, capsys):
        cli(clonage="non")
        assert diag.controler_clonage() is False
        sortie = capsys.readouterr().out
        assert "n'est PAS figé" in sortie
        assert "PLUSIEURS VOIX" in sortie

    def test_clonage_oui_valide_le_timbre(self, cli, monkeypatch, capsys):
        cli(clonage="oui")
        monkeypatch.setattr(config, "VOICE_PRESET", "klody-fr")
        assert diag.controler_clonage() is True
        assert "clonage actif" in capsys.readouterr().out

    def test_preset_renseignee_mais_ignoree_est_DENONCEE(self, cli, monkeypatch, capsys):
        """LE cas piège : la CLI accepte une voix inconnue et l'ignore en silence."""
        cli(clonage="non")
        monkeypatch.setattr(config, "VOICE_PRESET", "faute-de-frappe")
        assert diag.controler_clonage() is False
        sortie = capsys.readouterr().out
        assert "INCONNUE de VocalBrain" in sortie
        assert "faute-de-frappe" in sortie

    def test_la_preset_est_bien_transmise_au_dry_run(self, cli, monkeypatch):
        lancees = cli(clonage="oui")
        monkeypatch.setattr(config, "VOICE_PRESET", "klody-fr")
        diag.controler_clonage()
        cmd = lancees[0]
        assert cmd[cmd.index("--voice") + 1] == "klody-fr"

    def test_sans_preset_l_option_n_est_pas_passee(self, cli):
        lancees = cli(clonage="non")
        diag.controler_clonage()
        assert "--voice" not in lancees[0]

    def test_le_dry_run_ne_synthetise_rien(self, cli):
        """Le contrôle doit rester quasi gratuit : jamais de vraie synthèse."""
        lancees = cli(clonage="non")
        diag.controler_clonage()
        assert "--dry-run" in lancees[0]

    def test_sortie_inattendue_ne_bloque_pas(self, cli, capsys):
        """Une CLI d'une autre version ne doit pas faire échouer le diagnostic :
        inconnu n'est pas cassé."""
        cli(clonage="non", aide=_AIDE)

        def _sans_clonage(cmd, **_k):
            class _Proc:
                stdout = "dry-run — 1 prise(s)\n"
                stderr = ""

            return _Proc()

        import scripts.diagnostic_voix as mod

        mod.subprocess.run = _sans_clonage  # type: ignore[assignment]
        assert diag.controler_clonage() is True
        assert "ne le rapporte pas" in capsys.readouterr().out

    def test_cli_injoignable_rend_faux_sans_lever(self, cli, capsys):
        cli(casse=True)
        assert diag.controler_clonage() is False
        assert "dry-run impossible" in capsys.readouterr().out


class TestListerSousCommandes:
    def test_affiche_les_commandes_pour_trouver_le_clonage(self, cli, capsys):
        cli()
        diag.lister_sous_commandes()
        sortie = capsys.readouterr().out
        assert "clone" in sortie
        assert "generate" in sortie

    def test_cli_injoignable_reste_muet(self, cli, capsys):
        cli(casse=True)
        diag.lister_sous_commandes()
        assert capsys.readouterr().out == ""
