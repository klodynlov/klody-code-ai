"""Tests du shell inline de `bench-nightly.yml` — préflight et code de sortie.

Ce shell-là garde le banc, et il s'est trompé deux fois le 2026-07-30 :

- « L'alias 'coder' ne résout pas » désignait le resolver du gateway, qui allait
  très bien : la vraie réponse était un 503 « RAM insuffisante ».
- Le job est ensuite ressorti rouge sur un run PARFAIT (29/30, Δ +0,0 %), parce
  que le code 2 de `bench.run` faisait échouer l'étape et SAUTER la porte — le
  seul juge qui connaisse la baseline.

Plutôt que de relire le YAML à l'œil, on EXÉCUTE les blocs `run:` avec des
commandes bouchonnées. Même raison que pour `bench/gate.py` : une logique inline
sans test laisse passer une erreur qui ne se voit qu'en production, une fois par
nuit.

⚠️ Les blocs tournent sous `/bin/bash` (macOS 3.2 sur le runner), pas sous le bash
du conteneur de test : c'est la version qui les exécutera pour de vrai.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/bench-nightly.yml"
ETAPE = "Vérifier services locaux"
ETAPE_BENCH = "Run bench"


def extraire_run(nom: str) -> str:
    """Le bloc `run:` de l'étape `nom`, lu dans le workflow réel (jamais recopié).

    ⚠️ Les expressions GitHub `${{ … }}` sont substituées côté Actions, jamais par
    bash — laissées telles quelles elles donnent un « bad substitution ». On les
    neutralise (chaîne vide), ce qui correspond au cas nominal du nightly :
    `inputs.category` est vide sur un dispatch sans argument, donc les 30 tâches.
    """
    donnees = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in donnees["jobs"].values():
        for etape in job.get("steps", []):
            if etape.get("name") == nom:
                brut: str = etape["run"]
                while "${{" in brut:
                    debut = brut.index("${{")
                    fin = brut.index("}}", debut) + 2
                    brut = brut[:debut] + brut[fin:]
                return brut
    raise AssertionError(f"étape « {nom} » introuvable dans {WORKFLOW}")

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
    """Le bloc `run:` du préflight, sur disque pour être `source`é par le bouchon."""
    chemin = tmp_path_factory.mktemp("preflight") / "etape.sh"
    chemin.write_text(extraire_run(ETAPE), encoding="utf-8")
    return chemin


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


def jouer_bench(tmp_path: Path, code_python: int) -> subprocess.CompletedProcess:
    """Exécute l'étape « Run bench » avec un `python` qui sort en `code_python`."""
    faux_venv = tmp_path / ".venv/bin"
    faux_venv.mkdir(parents=True)
    (faux_venv / "activate").write_text(":\n", encoding="utf-8")
    etape = tmp_path / "bench.sh"
    etape.write_text(extraire_run(ETAPE_BENCH), encoding="utf-8")
    return subprocess.run(
        ["/bin/bash", "-e", "-c", f'python() {{ return {code_python}; }}\nsource "$0"', str(etape)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "PROJECT_ROOT": str(tmp_path / "bench")},
    )


@pytest.mark.skipif(not shutil.which("/bin/bash"), reason="/bin/bash requis")
class TestCodeDeSortieDuBanc:
    """Le code 2 de `bench.run` est un RÉSULTAT, pas un verdict de CI.

    Depuis que la baseline est passée à 30 tâches en y gelant
    `discovery/hidden_invariant` comme échec attendu, `bench.run` ne peut plus
    rendre 0. Le faire échouer l'étape sautait la porte — le seul juge qui
    connaisse la baseline. Constaté le 2026-07-30 : run 29/30, porte rejouée à la
    main = Δ +0,0 %, aucune régression, et le job pourtant rouge.
    """

    def test_tout_passe(self, tmp_path):
        assert jouer_bench(tmp_path, 0).returncode == 0

    def test_des_taches_echouent_laisse_la_porte_juger(self, tmp_path):
        r = jouer_bench(tmp_path, 2)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "la porte tranche" in r.stdout

    @pytest.mark.parametrize("code", [1, 3, 127])
    def test_harnais_casse_reste_fatal(self, tmp_path, code):
        """⚠️ Un `|| true` confondrait « des tâches ont échoué » et « le banc est
        en panne ». C'est la panne la plus coûteuse de ce dépôt : le banc a rendu
        ~13 % pendant des mois en se mesurant lui-même, sans que rien ne rougisse.
        """
        r = jouer_bench(tmp_path, code)
        assert r.returncode == code
        assert "le harnais est en panne" in r.stdout
