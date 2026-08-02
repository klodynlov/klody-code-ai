"""`install-launchagents.sh --check` : ce qu'il juge, et ce qu'il se contente
de signaler.

Le contrôle historique compare les plists versionnés à ceux installés. Un
plist conforme ne dit pourtant RIEN de ce que le service exécute : les agents
lancent des scripts de ce dépôt, avec `WorkingDirectory` sur sa racine, donc le
code en production est celui de l'ARBRE DE TRAVAIL au démarrage. D'où la
section ajoutée le 2026-08-03 — et d'où ces tests, parce qu'elle porte deux
propriétés faciles à casser sans le voir :

* elle **ne doit pas** influencer le code de sortie — diverger d'origin/main
  est l'état normal d'un poste de développement, et faire rougir `--check`
  là-dessus le rendrait inutilisable ;
* elle **doit se taire** quand aucun service ne tourne, sinon elle bruite tous
  les runners de CI.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "install-launchagents.sh"
AGENTS = REPO / "launchagents"

ORIG_REPO = "/Users/klodynlov/Projets/klody-code-ai"
ORIG_HOME = "/Users/klodynlov"


@pytest.fixture
def home_synchronise(tmp_path):
    """Un `$HOME` où les agents installés sont exactement ceux du dépôt.

    Reproduit le rendu du script (mêmes substitutions) plutôt que de l'appeler
    sans `--check` : l'appeler installerait ET bootstrapperait de vrais
    services sur la machine qui exécute les tests.
    """
    dest = tmp_path / "Library" / "LaunchAgents"
    dest.mkdir(parents=True)
    for src in AGENTS.glob("*.plist"):
        rendu = src.read_text().replace(ORIG_REPO, str(REPO)).replace(ORIG_HOME, str(tmp_path))
        (dest / src.name).write_text(rendu)
    return tmp_path


def _lancer(home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(home))
    return subprocess.run(
        ["/bin/sh", str(SCRIPT), "--check"],
        capture_output=True, text=True, env=env, cwd=str(REPO), timeout=180,
    )


class TestCodeDeSortie:
    def test_aucun_ecart_de_plist_rend_zero(self, home_synchronise):
        """Même si la branche diverge d'origin/main et que des services
        tournent : la nouvelle section informe, elle ne juge pas."""
        p = _lancer(home_synchronise)
        assert p.returncode == 0, p.stdout + p.stderr

    def test_un_plist_manquant_rend_un(self, home_synchronise):
        """Le critère historique doit continuer de mordre."""
        agents = home_synchronise / "Library" / "LaunchAgents"
        victime = sorted(agents.glob("*.plist"))[0]
        victime.unlink()
        p = _lancer(home_synchronise)
        assert p.returncode == 1
        assert "ABSENT" in p.stdout

    def test_un_plist_divergent_rend_un(self, home_synchronise):
        agents = home_synchronise / "Library" / "LaunchAgents"
        victime = sorted(agents.glob("*.plist"))[0]
        avant = victime.read_text()
        # ⚠️ Une substitution ciblée (`<integer>30</integer>`) ne s'appliquait
        # pas au premier plist par ordre alphabétique : le test passait sur une
        # mutation qui n'avait rien muté, et rendait vert un script qu'il
        # croyait avoir mis en défaut. On mute donc à coup sûr, et on le
        # vérifie.
        victime.write_text(avant.replace("</dict>\n</plist>",
                                         "\t<key>Zzz</key><string>x</string>\n</dict>\n</plist>"))
        assert victime.read_text() != avant, "la mutation n'a rien changé"
        p = _lancer(home_synchronise)
        assert p.returncode == 1, p.stdout
        assert "ÉCART" in p.stdout


class TestSectionCodeServi:
    def test_muette_sans_service_en_cours(self, home_synchronise, monkeypatch, tmp_path):
        """Sur un runner de CI, aucun agent n'est chargé : la section ne doit
        rien écrire du tout. On le vérifie en neutralisant `launchctl`, ce qui
        rend l'absence de service certaine plutôt que supposée."""
        faux_bin = tmp_path / "bin"
        faux_bin.mkdir()
        (faux_bin / "launchctl").write_text("#!/bin/sh\nexit 1\n")
        (faux_bin / "launchctl").chmod(0o755)
        env_path = f"{faux_bin}:{os.environ['PATH']}"
        p = subprocess.run(
            ["/bin/sh", str(SCRIPT), "--check"],
            capture_output=True, text=True, timeout=180, cwd=str(REPO),
            env=dict(os.environ, HOME=str(home_synchronise), PATH=env_path),
        )
        assert p.returncode == 0
        assert "service(s) en cours" not in p.stdout
        assert "ATTENTION" not in p.stdout
        assert "PÉRIMÉ" not in p.stdout

    @pytest.mark.skipif(
        subprocess.run(["/bin/sh", "-c", "launchctl print gui/$(id -u) >/dev/null 2>&1"],
                       check=False).returncode != 0,
        reason="aucun domaine launchd (CI)",
    )
    def test_compte_les_services_quand_il_y_en_a(self, home_synchronise):
        p = _lancer(home_synchronise)
        m = re.search(r"(\d+) service\(s\) en cours, (\d+) au point d'entrée périmé",
                      p.stdout)
        if m is None:
            pytest.skip("aucun agent de ce dépôt chargé sur cette machine")
        assert int(m.group(1)) >= 1
        assert int(m.group(2)) <= int(m.group(1))


class TestPointDEntree:
    """L'attribution doit rester NOMINATIVE : un service n'est déclaré périmé
    que si SON lanceur ou SON module a bougé. La première version comparait à
    tout `klody_mcp/` et accusait 7 services sur 9 — un contrôle toujours rouge
    n'est plus lu."""

    def test_chaque_agent_mcp_a_un_point_d_entree_resoluble(self):
        script = SCRIPT.read_text()
        assert "point_d_entree()" in script, "la fonction a été renommée"
        for plist in sorted(AGENTS.glob("com.klody.*-mcp.plist")):
            sortie = subprocess.run(
                ["/bin/sh", "-c",
                 f'REPO_ROOT={REPO}; ORIG_REPO={ORIG_REPO}; ORIG_HOME={ORIG_HOME}; '
                 + _extraire_fonction(script, "point_d_entree")
                 + f' point_d_entree "{plist}"'],
                capture_output=True, text=True, timeout=60,
            ).stdout.split()
            assert len(sortie) == 2, f"{plist.name}: attendu lanceur + module, eu {sortie}"
            assert sortie[0].endswith(".sh")
            assert sortie[1].endswith(".py")


def _extraire_fonction(script: str, nom: str) -> str:
    """Isole une fonction shell du script pour la tester seule."""
    m = re.search(rf"^{nom}\(\) \{{.*?^\}}", script, re.S | re.M)
    assert m, f"fonction {nom} introuvable"
    return m.group(0) + "\n"
