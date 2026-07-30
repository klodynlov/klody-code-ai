"""Tests du préflight « Vérifier services locaux » de `bench-nightly.yml`.

Ce shell-là garde le banc : s'il se trompe, un nightly rouge envoie chercher la
panne au mauvais endroit — ce qui est arrivé le 2026-07-30, où un « l'alias
'coder' ne résout pas » désignait le resolver du gateway alors que le resolver
allait très bien et que la vraie réponse était un 503 « RAM insuffisante ».

Plutôt que de relire le YAML à l'œil, on EXÉCUTE le bloc `run:` avec un `curl`
bouchonné, et on vérifie les cinq conduites qui comptent. Même raison que pour
`bench/gate.py` : une logique inline sans test laisse passer une erreur qui ne se
voit qu'en production, une fois par nuit.

⚠️ Le bloc tourne sous `/bin/bash` (macOS 3.2 sur le runner), pas sous le bash du
conteneur de test : c'est la version qui l'exécutera pour de vrai.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/bench-nightly.yml"
ETAPE = "Vérifier services locaux"

# Le bouchon compte ses appels dans un FICHIER, jamais dans une variable shell :
# `reponse=$(curl …)` s'exécute dans un SOUS-SHELL, où une variable est remise à
# zéro à chaque appel. Un compteur en variable rend donc toujours la première
# réponse, et tous les scénarios d'échec passent au vert — constaté en écrivant
# ces tests, et c'est précisément le genre de faux vert qu'ils existent pour éviter.
_BOUCHON = r"""
curl() {
  url=""; echec_dur=0
  for a in "$@"; do
    case "$a" in http*) url="$a";; -sf|-fs|-f) echec_dur=1;; esac
  done
  case "$url" in
    */models) return 0 ;;
    */admin/status) return 1 ;;
  esac
  i=$(wc -c < "$CPT" | tr -d ' ')
  printf 'x' >> "$CPT"
  IFS='|' read -ra R <<< "$SCENARIO"
  r="${R[$i]}"
  code="${r%%:*}"
  corps="${r#*:}"
  if [ "$code" = "000" ]; then printf '%s\n000' "$corps"; return 28; fi
  # `-f` fidèle au vrai curl : sur 4xx/5xx il n'écrit RIEN et sort en 22. C'est ce
  # qui rendait 404 et 503 indiscernables. Émulé pour qu'un retour de `-f` sur cet
  # appel — qui reviderait le corps qu'on lit — fasse rougir la suite.
  if [ "$echec_dur" = "1" ] && [ "$code" -ge 400 ]; then return 22; fi
  printf '%s\n%s' "$corps" "$code"
}
sleep() { :; }
source "$ETAPE_SH"
"""

_RAM = '503:{"error":"RAM insuffisante pour coder (~30 Go)"}'


@pytest.fixture(scope="module")
def etape_sh(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Le bloc `run:` de l'étape, extrait du workflow réel (jamais recopié)."""
    donnees = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in donnees["jobs"].values():
        for etape in job.get("steps", []):
            if etape.get("name") == ETAPE:
                chemin = tmp_path_factory.mktemp("preflight") / "etape.sh"
                chemin.write_text(etape["run"], encoding="utf-8")
                return chemin
    # `raise` explicite plutôt que `pytest.fail()` : ce dernier sort par exception
    # lui aussi, mais l'analyse statique ne le sait pas et voit la fonction tomber
    # en fin de corps sans rien rendre (CodeQL — « explicit returns mixed with
    # implicit returns »). Un test qui garde un contrat mérite un flot lisible.
    raise AssertionError(f"étape « {ETAPE} » introuvable dans {WORKFLOW}")


def jouer(etape_sh: Path, tmp_path: Path, *reponses: str) -> subprocess.CompletedProcess:
    """Exécute le préflight en servant `reponses` aux appels /chat/completions."""
    compteur = tmp_path / "cpt"
    compteur.write_bytes(b"")
    return subprocess.run(
        ["/bin/bash", "-e", "-c", _BOUCHON],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": "/usr/bin:/bin",
            "SCENARIO": "|".join(reponses),
            "CPT": str(compteur),
            "ETAPE_SH": str(etape_sh),
            "MLX_BASE_URL": "http://stub/v1",
            "MLX_MODEL": "brain",
            "MLX_CODE_MODEL": "coder",
        },
    )


@pytest.mark.skipif(not shutil.which("/bin/bash"), reason="/bin/bash requis")
class TestPreflightAlias:
    def test_les_deux_alias_repondent(self, etape_sh, tmp_path):
        r = jouer(etape_sh, tmp_path, "200:{}", "200:{}")
        assert r.returncode == 0, r.stdout + r.stderr

    def test_manque_de_ram_reessaye_puis_passe(self, etape_sh, tmp_path):
        """503 « RAM insuffisante » = l'alias A résolu. Transitoire, donc on attend.

        Le préflight PROVOQUE lui-même ce cas : pinger `brain` charge 44 Go, après
        quoi `vm_stat` sous-estime la RAM disponible (les pages fraîchement touchées
        comptent `active`, pas encore `inactive`). Mesuré : 41 GiB à t+9 s, 62 à t+2 min.
        """
        r = jouer(etape_sh, tmp_path, "200:{}", _RAM, "200:{}")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "RAM indisponible (essai 1/6)" in r.stdout

    def test_alias_inconnu_echoue_sans_reessai(self, etape_sh, tmp_path):
        """404 = la panne que ce contrôle existe pour attraper (incident 2026-07-03).

        Elle doit rougir TOUT DE SUITE : réessayer cinq minutes une erreur qui ne se
        résorbera pas retarde le diagnostic sans rien changer au verdict.
        """
        r = jouer(etape_sh, tmp_path, "200:{}", '404:{"error":"modele inconnu"}')
        assert r.returncode == 1
        assert "ne résout pas" in r.stdout
        assert "essai 1/6" not in r.stdout

    def test_gateway_muet_ne_blame_pas_le_resolver(self, etape_sh, tmp_path):
        """Timeout/connexion coupée → code 000. Nommer le resolver enverrait
        chercher la panne au mauvais endroit, exactement le défaut corrigé ici."""
        r = jouer(etape_sh, tmp_path, "200:{}", "000:")
        assert r.returncode == 1
        assert "Pas de réponse du gateway" in r.stdout
        assert "resolver" not in r.stdout.split("Pas de réponse")[0]

    def test_ram_jamais_liberee_finit_par_echouer(self, etape_sh, tmp_path):
        """Le réessai est borné : un gateway durablement saturé doit rougir.

        Et le message dit que l'alias RÉSOUT — sans quoi on repartirait fouiller
        le resolver, qui n'y est pour rien.
        """
        r = jouer(etape_sh, tmp_path, "200:{}", *([_RAM] * 6))
        assert r.returncode == 1
        assert "résout, mais le gateway n'a pas libéré la RAM" in r.stdout
        assert "essai 5/6" in r.stdout
