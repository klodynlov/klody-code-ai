"""`install-launchagents.sh` (sans argument) : le rechargement bootout → bootstrap.

Vécu le 2026-09-30 à 20:36. Quatre agents en écart (ableton-mcp, blender-mcp,
unity-mcp, veille-qwen) ; le script traite `com.klody.ableton-mcp` en premier :
`mv` du plist rendu, `launchctl bootout`, puis `launchctl bootstrap` IMMÉDIAT →
« Bootstrap failed: 5: Input/output error ». `bootout` d'un job en cours rend la
main avant que launchd ait fini de le retirer, et un bootstrap posé dans cette
fenêtre est refusé. Le script, en `set -eu`, s'est arrêté net APRÈS le bootout :
ableton-mcp hors service (:8094 muet), les trois autres jamais traités, et aucun
message ne nommait l'agent.

On EXÉCUTE la boucle d'installation, avec un `launchctl` bouchonné : la jouer
contre le vrai launchd rechargerait des services vivants. Par précaution
supplémentaire, les agents sont SYNTHÉTIQUES (`com.klody.essai-*`, programme
`/usr/bin/true`) et installés dans un `$HOME` jetable : même un bouchon ignoré
ne toucherait à aucun service réel.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "install-launchagents.sh"

LABELS = ("com.klody.essai-a", "com.klody.essai-b", "com.klody.essai-c", "com.klody.essai-d")

# État de launchd PAR LABEL, tenu dans des FICHIERS, jamais dans des variables :
# le script appelle `launchctl` depuis des `$(…)`, donc des sous-shells, où un
# compteur en variable repartirait de zéro à chaque appel — piège déjà vécu en
# écrivant `tests/test_workflow_preflight.py`, où il rendait tout vert.
#
#   <label>.charge      présent ⇔ le job est chargé
#   <label>.retrait     N : après un bootout, `print` réussit encore N fois
#   <label>.en_retrait  décompte en cours de ces N
#   <label>.bootstrap   codes rendus par les bootstrap successifs (« 5 0 ») ;
#                       le dernier reste
#
# Et une règle de réalisme, indépendante du scénario : un bootstrap posé tant que
# le job est encore présent rend 5 — c'est l'incident lui-même.
_LAUNCHCTL = r"""#!/bin/sh
cmd=$1
case "$cmd" in
  bootout|print) lab=${2##*/} ;;
  bootstrap) lab=$(basename "$3" .plist) ;;
  *) echo "$* => 0" >> "$BOUCHON/journal"; exit 0 ;;
esac
f="$BOUCHON/$lab"
rc=0
note=""
case "$cmd" in
  bootout)
    if [ -f "$f.charge" ]; then
      rm -f "$f.charge"
      n=$(cat "$f.retrait" 2>/dev/null || echo 0)
      [ "$n" -le 0 ] || echo "$n" > "$f.en_retrait"
    else
      rc=3
    fi ;;
  print)
    if [ -f "$f.charge" ]; then
      rc=0
    elif [ -f "$f.en_retrait" ]; then
      n=$(($(cat "$f.en_retrait") - 1))
      if [ "$n" -le 0 ]; then rm -f "$f.en_retrait"; else echo "$n" > "$f.en_retrait"; fi
      rc=0
    else
      rc=113
    fi ;;
  bootstrap)
    codes=$(cat "$f.bootstrap" 2>/dev/null || echo 0)
    rc=${codes%% *}
    reste=${codes#* }
    [ "$reste" = "$codes" ] || echo "$reste" > "$f.bootstrap"
    if [ -f "$f.charge" ] || [ -f "$f.en_retrait" ]; then
      rc=5
      note=" (job encore présent)"
    fi
    if [ "$rc" -eq 0 ]; then
      : > "$f.charge"
    else
      echo "Bootstrap failed: $rc: Input/output error" >&2
    fi ;;
esac
echo "$cmd $lab => $rc$note" >> "$BOUCHON/journal"
exit "$rc"
"""

_SLEEP = '#!/bin/sh\necho "sleep $*" >> "$BOUCHON/journal"\n'

# `plutil` n'existe pas sur le runner Linux de la CI ; ce n'est pas lui qu'on
# teste. Il refuse seulement un plist marqué, pour le cas « plist invalide ».
_PLUTIL = "#!/bin/sh\n! grep -q '<string>INVALIDE</string>' \"$2\"\n"


def _plist(label: str, version: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f"\t<key>Label</key>\n\t<string>{label}</string>\n"
        "\t<key>ProgramArguments</key>\n\t<array><string>/usr/bin/true</string></array>\n"
        f"\t<key>Comment</key>\n\t<string>{version}</string>\n"
        "</dict>\n</plist>\n"
    )


class Machine:
    """Un dépôt, un `$HOME` et un launchd factices."""

    def __init__(self, racine: Path) -> None:
        self.depot = racine / "depot"
        self.home = racine / "home"
        self.bouchon = racine / "etat"
        self.bin = racine / "bin"
        for d in (self.depot / "scripts", self.depot / "launchagents",
                  self.home / "Library" / "LaunchAgents", self.bouchon, self.bin):
            d.mkdir(parents=True)
        self.script = self.depot / "scripts" / SCRIPT.name
        self.script.write_text(SCRIPT.read_text())
        for nom, corps in (("launchctl", _LAUNCHCTL), ("sleep", _SLEEP), ("plutil", _PLUTIL)):
            (self.bin / nom).write_text(corps)
            (self.bin / nom).chmod(0o755)
        (self.bouchon / "journal").write_text("")
        self.path = f"{self.bin}:/usr/bin:/bin"

    def installe(self, label: str) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{label}.plist"

    def agent(self, label: str, *, installe: str | None = "ancienne", charge: bool = True,
              retrait: int = 0, bootstrap: str = "0", versionne: str = "nouvelle") -> None:
        (self.depot / "launchagents" / f"{label}.plist").write_text(_plist(label, versionne))
        if installe is not None:
            self.installe(label).write_text(_plist(label, installe))
        if charge:
            (self.bouchon / f"{label}.charge").write_text("")
        (self.bouchon / f"{label}.retrait").write_text(str(retrait))
        (self.bouchon / f"{label}.bootstrap").write_text(bootstrap)

    def lancer(self, shell: str) -> subprocess.CompletedProcess:
        # Garde-fou : c'est BIEN le bouchon qui répondra, pas /bin/launchctl.
        assert shutil.which("launchctl", path=self.path) == str(self.bin / "launchctl")
        return subprocess.run(
            [shell, str(self.script)],
            capture_output=True, text=True, timeout=60, cwd=str(self.depot),
            env={"PATH": self.path, "HOME": str(self.home), "BOUCHON": str(self.bouchon)},
        )

    def journal(self) -> list[str]:
        return (self.bouchon / "journal").read_text().splitlines()

    def appels(self, cmd: str, label: str) -> list[str]:
        return [ligne for ligne in self.journal() if ligne.startswith(f"{cmd} {label} ")]


_SHELLS = [s for s in ("/bin/sh", "/bin/dash") if Path(s).exists()]


@pytest.fixture(params=_SHELLS)
def shell(request) -> str:
    """`/bin/sh` est bash 3.2 sur macOS, dash sur le runner Linux : le script
    se veut POSIX, on le joue sous les deux quand ils sont là."""
    return request.param


@pytest.fixture
def machine(tmp_path) -> Machine:
    return Machine(tmp_path)


def _domaine() -> str:
    return f"gui/{os.getuid()}"


class TestAttendreLeRetrait:
    def test_l_incident_quatre_agents_en_ecart(self, machine, shell):
        """Le scénario du 2026-09-30 : quatre agents chargés, en écart. Chaque job
        reste visible de launchd encore 3 `print` après son bootout, et le premier
        bootstrap posé une fois le job retiré rend quand même 5."""
        for lab in LABELS:
            machine.agent(lab, retrait=3, bootstrap="5 0")
        p = machine.lancer(shell)
        assert p.returncode == 0, p.stdout + p.stderr
        for lab in LABELS:
            assert f"installé  {lab}" in p.stdout, p.stdout + p.stderr
        assert "4 installé(s), 0 déjà à jour." in p.stdout
        journal = "\n".join(machine.journal())
        assert "job encore présent" not in journal, (
            "un bootstrap a été posé avant le retrait effectif du job — "
            f"la fenêtre de l'incident :\n{journal}"
        )

    def test_attend_que_print_echoue_avant_de_bootstrapper(self, machine, shell):
        """L'ordre exact, pour un agent : bootout, `print` jusqu'à l'échec, puis
        seulement bootstrap."""
        machine.agent(LABELS[0], retrait=3)
        p = machine.lancer(shell)
        assert p.returncode == 0, p.stdout + p.stderr
        seq = [ligne.split()[0] for ligne in machine.journal()
               if LABELS[0] in ligne and not ligne.startswith("sleep")]
        i_bootout = seq.index("bootout")
        i_bootstrap = seq.index("bootstrap")
        prints = machine.appels("print", LABELS[0])
        assert prints[-1].endswith("=> 113"), "le bootstrap doit suivre un print EN ÉCHEC"
        assert seq[i_bootout + 1:i_bootstrap] == ["print"] * 4
        assert "sleep 1" in machine.journal()

    def test_premiere_installation_sans_attente(self, machine, shell):
        """Rien n'était chargé : le bootout échoue, `print` aussi dès le premier
        appel — aucune pause ne doit être payée."""
        for lab in LABELS:
            machine.agent(lab, installe=None, charge=False)
        p = machine.lancer(shell)
        assert p.returncode == 0, p.stdout + p.stderr
        assert not [ligne for ligne in machine.journal() if ligne.startswith("sleep")]
        for lab in LABELS:
            assert machine.appels("bootstrap", lab) == [f"bootstrap {lab} => 0"]

    def test_un_agent_a_jour_n_est_pas_recharge(self, machine, shell):
        """L'idempotence qui rend le script sûr à relancer : un bootstrap
        inconditionnel redémarrerait l'API en pleine session."""
        machine.agent(LABELS[0], installe="meme", versionne="meme")
        machine.agent(LABELS[1], retrait=1)
        p = machine.lancer(shell)
        assert p.returncode == 0, p.stdout + p.stderr
        assert not machine.appels("bootout", LABELS[0])
        assert not machine.appels("bootstrap", LABELS[0])
        assert "1 installé(s), 1 déjà à jour." in p.stdout


class TestEchecNomme:
    def test_bootstrap_refuse_nomme_l_agent_et_continue(self, machine, shell):
        """Le premier agent ne se recharge JAMAIS. Il doit être nommé, déclaré
        arrêté avec sa commande de relance, et les trois autres traités quand
        même — puis un code non nul, jamais un vert."""
        machine.agent(LABELS[0], retrait=2, bootstrap="5")
        for lab in LABELS[1:]:
            machine.agent(lab, retrait=2)
        p = machine.lancer(shell)

        assert p.returncode == 1, p.stdout + p.stderr
        for lab in LABELS[1:]:
            assert f"installé  {lab}" in p.stdout, p.stdout + p.stderr
        assert f"installé  {LABELS[0]}" not in p.stdout

        relance = f"launchctl bootstrap {_domaine()} {machine.installe(LABELS[0])}"
        assert f"ÉCHEC   {LABELS[0]}" in p.stderr
        assert "Bootstrap failed: 5: Input/output error" in p.stderr, "l'erreur de launchd est relayée"
        assert "ARRÊTÉ" in p.stderr
        assert relance in p.stderr

        # Le récapitulatif, en DERNIER : c'est ce qui reste à l'écran.
        fin = p.stderr.strip().splitlines()
        assert fin[-2] == "3 installé(s), 0 déjà à jour, 1 en ÉCHEC :"
        assert LABELS[0] in fin[-1] and relance in fin[-1]

        # Réessayé, espacé, borné.
        essais = machine.appels("bootstrap", LABELS[0])
        assert 2 <= len(essais) <= 10, essais
        assert machine.journal().count("sleep 2") >= len(essais) - 1

        # Le plist neuf est bien en place : la commande de relance le chargera.
        assert "nouvelle" in machine.installe(LABELS[0]).read_text()

    def test_retrait_jamais_constate_reste_borne(self, machine, shell):
        """launchd ne lâche jamais le job : l'attente doit s'arrêter d'elle-même,
        le dire, et l'agent finir nommé en échec sans bloquer les autres."""
        machine.agent(LABELS[0], retrait=10_000)
        machine.agent(LABELS[1])
        p = machine.lancer(shell)

        assert p.returncode == 1, p.stdout + p.stderr
        assert f"installé  {LABELS[1]}" in p.stdout
        assert f"ÉCHEC   {LABELS[0]}" in p.stderr
        assert "retrait jamais constaté" in p.stderr
        assert len(machine.appels("print", LABELS[0])) <= 40, "attente non bornée"

    def test_premiere_installation_refusee_n_est_pas_dite_arretee(self, machine, shell):
        """Un agent qui ne tournait pas n'a pas été « arrêté » par le script :
        le message ne doit pas inventer une coupure."""
        machine.agent(LABELS[0], installe=None, charge=False, bootstrap="5")
        p = machine.lancer(shell)
        assert p.returncode == 1
        assert f"ÉCHEC   {LABELS[0]}" in p.stderr
        assert "ARRÊTÉ" not in p.stderr
        assert "non chargé" in p.stderr

    def test_plist_invalide_ne_touche_a_rien_et_continue(self, machine, shell):
        machine.agent(LABELS[0], versionne="INVALIDE")
        machine.agent(LABELS[1], retrait=1)
        p = machine.lancer(shell)
        assert p.returncode == 1, p.stdout + p.stderr
        assert f"ÉCHEC   {LABELS[0]}" in p.stderr
        assert not machine.appels("bootout", LABELS[0]), "le service ne doit pas être coupé"
        assert "ancienne" in machine.installe(LABELS[0]).read_text()
        assert f"installé  {LABELS[1]}" in p.stdout
